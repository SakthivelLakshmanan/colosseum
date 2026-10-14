"""Evaluation runner for Colosseum adviser outputs."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Dict, List, Set

# Ensure project root is on sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from core.registry import get_registry
from core.schemas import (
    AdviserPosition,
    BlockingFinding,
    Claim,
    ClaimVerdict,
    DecisionBrief,
    Memo,
    MemoVerdictType,
)
from core.gemini import generate, generate_content, get_genai_client
from core.settings import get_settings
from agents.moderator import check_claims, check_memo
from agents.pipeline import run_decision


def load_planted_facts() -> List[Dict[str, Any]]:
    """Load planted facts and their statements.
    
    Reads from BigQuery colosseum_eval.planted_facts_answer_key joined with colosseum.facts.
    Falls back to local CSV files if BigQuery is unavailable.
    """
    settings = get_settings()
    planted_facts: List[Dict[str, Any]] = []

    try:
        from google.cloud import bigquery

        client = bigquery.Client(
            project=settings.project_id,
            location=settings.bigquery.location,
        )
        query = f"""
            SELECT
                p.fact_id,
                p.adviser,
                p.planted,
                p.expected_conclusion,
                p.direction,
                p.impact,
                p.blocking,
                f.statement
            FROM `{settings.project_id}.{settings.bigquery.eval_dataset}.planted_facts_answer_key` p
            LEFT JOIN `{settings.project_id}.{settings.bigquery.dataset}.facts` f ON p.fact_id = f.fact_id
        """
        query_job = client.query(query, timeout=10.0)
        for row in query_job.result(timeout=10.0):
            is_planted = row.planted is True or str(row.planted).lower() in ("yes", "true", "1")
            if is_planted:
                planted_facts.append({
                    "fact_id": str(row.fact_id),
                    "adviser": str(row.adviser).lower().strip(),
                    "planted": True,
                    "expected_conclusion": str(row.expected_conclusion or ""),
                    "statement": str(row.statement or ""),
                })
        if planted_facts:
            return planted_facts
    except Exception as exc:
        print(f"[evals] Note: BigQuery colosseum_eval query fell back to local CSV ({exc})")

    # Fallback to local CSV files
    csv_key_file = root_dir / "evals" / "planted_facts_answer_key.csv"
    csv_facts_file = root_dir / "data" / "facts.csv"

    fact_statements: Dict[str, str] = {}
    if csv_facts_file.is_file():
        with open(csv_facts_file, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                fact_statements[row["fact_id"]] = row.get("statement", "")

    if csv_key_file.is_file():
        with open(csv_key_file, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("planted", "").lower() in ("yes", "true", "1"):
                    fid = row["fact_id"]
                    planted_facts.append({
                        "fact_id": fid,
                        "adviser": row["adviser"].lower().strip(),
                        "planted": True,
                        "expected_conclusion": row.get("expected_conclusion", ""),
                        "statement": fact_statements.get(fid, ""),
                    })

    return planted_facts


def run_fresh_advisers():
    """Rerun all advisers fresh and save outputs to runs/day4/."""
    from scripts.run_all_advisers import main_async
    print("[evals] Re-running all advisers (--fresh)...")
    asyncio.run(main_async())


def evaluate_planted(runs_dir: Path) -> str:
    """Evaluate planted fact coverage and policy warnings for all advisers."""
    registry = get_registry()
    adviser_ids = registry.get_ids()
    planted_facts = load_planted_facts()

    lines: List[str] = []
    lines.append("=" * 110)
    lines.append("COLOSSEUM EVALUATION — PLANTED FACTS COVERAGE REPORT")
    lines.append("=" * 110)
    lines.append(f"{'adviser':<10} | {'cited/eligible':<14} | {'coverage %':<10} | missed fact IDs with their statements")
    lines.append("-" * 110)

    total_eligible = 0
    total_cited = 0
    warnings: List[str] = []
    missed_facts_by_adviser: Dict[str, List[Dict[str, Any]]] = {}

    for aid in adviser_ids:
        # Eligible = planted facts whose adviser is that domain, excluding FIN-12
        eligible = [
            f for f in planted_facts
            if f["adviser"] == aid and f["fact_id"] != "FIN-12"
        ]
        total_eligible += len(eligible)

        # Read latest result in runs/day4/<id>.json
        run_file = runs_dir / f"{aid}.json"
        if not run_file.is_file():
            lines.append(f"{aid:<10} | {'MISSING':<14} | {'0.0%':<10} | File runs/day4/{aid}.json not found")
            continue

        try:
            with open(run_file, "r", encoding="utf-8") as f:
                pos_data = json.load(f)
        except Exception as e:
            lines.append(f"{aid:<10} | {'ERROR':<14} | {'0.0%':<10} | Could not parse JSON: {e}")
            continue

        # Extract cited IDs from claims and blocking findings
        cited_fact_ids: Set[str] = set()

        for claim in pos_data.get("claims", []):
            c_text = claim.get("text", "")
            eids = [str(x).strip() for x in claim.get("evidence_ids", []) if str(x).strip()]
            for eid in eids:
                cited_fact_ids.add(eid)

            # Warning check 1: Any claim citing FIN-12
            if "FIN-12" in eids:
                warnings.append(f"Adviser [{aid}] claim '{claim.get('claim_id')}' cites superseded fact FIN-12: \"{c_text}\"")

            # Warning check 2: Any claim relying only on OPS-13 that doesn't say it is unverified
            if set(eids) == {"OPS-13"}:
                if "unverified" not in c_text.lower():
                    warnings.append(
                        f"Adviser [{aid}] claim '{claim.get('claim_id')}' relies only on OPS-13 without stating it is unverified: \"{c_text}\""
                    )

        # Blocking findings cited fact IDs
        blocking_findings = pos_data.get("blocking_findings", [])
        for bf in blocking_findings:
            bf_eids = [str(x).strip() for x in bf.get("evidence_ids", []) if str(x).strip()]
            for eid in bf_eids:
                cited_fact_ids.add(eid)

        # Warning check 3: InfoSec without a blocking finding citing SEC-01
        if aid == "infosec":
            sec01_cited = False
            for bf in blocking_findings:
                bf_eids = [str(x).strip() for x in bf.get("evidence_ids", []) if str(x).strip()]
                if "SEC-01" in bf_eids:
                    sec01_cited = True
                    break
            if not sec01_cited:
                warnings.append("InfoSec does not have a blocking finding citing SEC-01.")

        # Compute cited / eligible
        cited_eligible = [f for f in eligible if f["fact_id"] in cited_fact_ids]
        missed = [f for f in eligible if f["fact_id"] not in cited_fact_ids]
        total_cited += len(cited_eligible)

        coverage_pct = (len(cited_eligible) / len(eligible) * 100) if eligible else 0.0
        missed_facts_by_adviser[aid] = missed

        # Format missed facts inline for the row
        if missed:
            missed_inline = "; ".join(f"{f['fact_id']}: {f['statement']}" for f in missed)
        else:
            missed_inline = "None"

        # Truncate inline if very long for table row, or format cleanly
        ratio_str = f"{len(cited_eligible)}/{len(eligible)}"
        lines.append(f"{aid:<10} | {ratio_str:<14} | {coverage_pct:5.1f}%    | {missed_inline}")

    lines.append("-" * 110)
    overall_pct = (total_cited / total_eligible * 100) if total_eligible else 0.0
    lines.append(f"{'OVERALL':<10} | {f'{total_cited}/{total_eligible}':<14} | {overall_pct:5.1f}%    |")
    lines.append("=" * 110)

    # Missed facts detailed section
    lines.append("\nMISSED FACTS BY ADVISER:")
    has_any_missed = False
    for aid, missed_list in missed_facts_by_adviser.items():
        if missed_list:
            has_any_missed = True
            lines.append(f"  [{aid}] ({len(missed_list)} missed):")
            for f in missed_list:
                stmt = f["statement"] if f["statement"] else f["expected_conclusion"]
                lines.append(f"    - {f['fact_id']}: {stmt}")
    if not has_any_missed:
        lines.append("  None (100% coverage across all advisers)")

    # Warnings section
    lines.append("\nWARNINGS:")
    if warnings:
        for w in warnings:
            lines.append(f"  [WARNING] {w}")
    else:
        lines.append("  None.")
    lines.append("=" * 110)

    report = "\n".join(lines)
    return report


def load_routing_tests() -> List[Dict[str, Any]]:
    """Load routing tests from BigQuery colosseum_eval.routing_tests or local CSV fallback."""
    settings = get_settings()
    tests: List[Dict[str, Any]] = []

    try:
        from google.cloud import bigquery

        client = bigquery.Client(
            project=settings.project_id,
            location=settings.bigquery.location,
        )
        query = f"SELECT * FROM `{settings.project_id}.{settings.bigquery.eval_dataset}.routing_tests`"
        query_job = client.query(query, timeout=10.0)
        for row in query_job.result(timeout=10.0):
            r = dict(row)
            test_id = str(r.get("test_id") or r.get("string_field_0") or "")
            if not test_id or test_id.lower() == "test_id":
                continue
            tests.append({
                "test_id": test_id,
                "brief": str(r.get("brief") or r.get("string_field_1") or ""),
                "must_include": str(r.get("must_include") or r.get("string_field_2") or ""),
                "acceptable": str(r.get("acceptable") or r.get("string_field_3") or ""),
                "must_exclude": str(r.get("must_exclude") or r.get("string_field_4") or ""),
                "why": str(r.get("why") or r.get("string_field_5") or ""),
            })
        if tests:
            return sorted(tests, key=lambda x: x["test_id"])
    except Exception as exc:
        print(f"[evals] Note: BigQuery routing_tests query fell back to local CSV ({exc})")

    csv_file = root_dir / "evals" / "routing_tests.csv"
    if csv_file.is_file():
        with open(csv_file, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                tests.append(r)

    return sorted(tests, key=lambda x: x["test_id"])


def evaluate_routing() -> str:
    """Run the full router on all routing test briefs and produce a test & score report."""
    from harness.router import route_decision

    tests = load_routing_tests()
    registry = get_registry()
    adviser_ids = registry.get_ids()

    lines: List[str] = []
    lines.append("=" * 115)
    lines.append("COLOSSEUM EVALUATION — ADVISER ROUTING REPORT")
    lines.append("=" * 115)
    lines.append(f"{'id':<6} | {'pass/fail':<9} | {'selected':<45} | {'missing':<20} | wrongly included")
    lines.append("-" * 115)

    passed_count = 0
    scores_by_test: Dict[str, Dict[str, float]] = {}

    for t in tests:
        tid = t["test_id"]
        brief_text = t["brief"]
        must_inc = set(x.strip() for x in t.get("must_include", "").split("|") if x.strip())
        must_exc = set(x.strip() for x in t.get("must_exclude", "").split("|") if x.strip())

        decision = route_decision(brief_text)
        selected = set(decision.selected)

        missing = must_inc - selected
        wrongly_included = must_exc & selected
        passed = (len(missing) == 0 and len(wrongly_included) == 0)
        if passed:
            passed_count += 1

        status_str = "PASS" if passed else "FAIL"
        selected_str = ", ".join(sorted(selected)) if selected else "None"
        missing_str = ", ".join(sorted(missing)) if missing else "None"
        wrong_str = ", ".join(sorted(wrongly_included)) if wrongly_included else "None"

        lines.append(f"{tid:<6} | {status_str:<9} | {selected_str:<45} | {missing_str:<20} | {wrong_str}")

        scores_by_test[tid] = {s.adviser_id: s.similarity for s in decision.scores}

    lines.append("-" * 115)
    lines.append(f"TOTAL: {passed_count}/{len(tests)} passed\n")

    # Score table: adviser x test
    lines.append("SCORE TABLE (adviser × test):")
    test_ids = [t["test_id"] for t in tests]
    header = f"{'adviser':<10} | " + " | ".join(f"{tid:<7}" for tid in test_ids)
    lines.append(header)
    lines.append("-" * len(header))

    for aid in adviser_ids:
        row_vals = []
        for tid in test_ids:
            score = scores_by_test.get(tid, {}).get(aid, 0.0)
            row_vals.append(f"{score:.4f} ")
        lines.append(f"{aid:<10} | " + " | ".join(row_vals))

    lines.append("=" * 115)

    report = "\n".join(lines)
    return report


def load_moderator_tests() -> List[Dict[str, Any]]:
    """Load moderator test cases from BigQuery colosseum_eval.moderator_tests or CSV fallback."""
    settings = get_settings()
    tests: List[Dict[str, Any]] = []

    try:
        from google.cloud import bigquery

        client = bigquery.Client(
            project=settings.project_id,
            location=settings.bigquery.location,
        )
        query = f"SELECT * FROM `{settings.project_id}.{settings.bigquery.eval_dataset}.moderator_tests`"
        query_job = client.query(query, timeout=10.0)
        for row in query_job.result(timeout=10.0):
            r = dict(row)
            tid = str(r.get("test_id") or r.get("string_field_0") or "")
            if not tid or tid.lower() == "test_id":
                continue
            tests.append({
                "test_id": tid,
                "stage": str(r.get("stage") or r.get("string_field_1") or ""),
                "source": str(r.get("source") or r.get("string_field_2") or ""),
                "input": str(r.get("input") or r.get("string_field_3") or ""),
                "cited_evidence": str(r.get("cited_evidence") or r.get("string_field_4") or ""),
                "expected": str(r.get("expected") or r.get("string_field_5") or "").upper().strip(),
                "reason": str(r.get("reason") or r.get("string_field_6") or ""),
            })
        if tests:
            return sorted(tests, key=lambda x: x["test_id"])
    except Exception as exc:
        print(f"[evals] Note: BigQuery moderator_tests query fell back to local CSV ({exc})")

    csv_file = root_dir / "evals" / "moderator_tests.csv"
    if csv_file.is_file():
        with open(csv_file, "r", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                tests.append({
                    "test_id": r.get("test_id", ""),
                    "stage": r.get("stage", ""),
                    "source": r.get("source", ""),
                    "input": r.get("input", ""),
                    "cited_evidence": r.get("cited_evidence", ""),
                    "expected": r.get("expected", "").upper().strip(),
                    "reason": r.get("reason", ""),
                })

    return sorted(tests, key=lambda x: x["test_id"])


def load_latest_passed_memo() -> tuple[Memo, DecisionBrief, dict[str, AdviserPosition], list[ClaimVerdict], list[str]]:
    """Load latest passed memo and associated run bundle from runs/ or Firestore."""
    # 1. Check local files first
    for path in [
        root_dir / "runs" / "day6" / "passed_memo.json",
        root_dir / "runs" / "passed_memo.json",
    ]:
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                memo = Memo.model_validate(data["memo"])
                brief = DecisionBrief.model_validate(data["brief"])
                positions = {
                    (p.get("adviser_id") if isinstance(p, dict) else p.adviser_id): (
                        p if isinstance(p, AdviserPosition) else AdviserPosition.model_validate(p)
                    )
                    for p in data["positions"]
                }
                claim_verdicts = [
                    v if isinstance(v, ClaimVerdict) else ClaimVerdict.model_validate(v)
                    for v in data["claim_verdicts"]
                ]
                selected = data["selected_advisers"]
                return memo, brief, positions, claim_verdicts, selected
            except Exception:
                pass

    # 2. Check Firestore
    from core import ledger
    db = ledger.get_db()
    docs = list(db.collection("runs").order_by("created_at", direction="DESCENDING").limit(10).stream())
    for doc in docs:
        data = doc.to_dict()
        verdicts = data.get("final_verdicts", [])
        memos = data.get("memo_versions", [])
        for idx, v in enumerate(verdicts):
            v_val = v.get("verdict") if isinstance(v, dict) else getattr(v, "value", str(v))
            if str(v_val).upper() in ("PASS", "MEMOVERDICTTYPE.PASS_") and idx < len(memos):
                memo = Memo.model_validate(memos[idx])
                brief = DecisionBrief.model_validate(data["brief"])
                positions = {
                    p["adviser_id"]: AdviserPosition.model_validate(p)
                    for p in data.get("positions", [])
                }
                claim_verdicts = [
                    ClaimVerdict.model_validate(cv)
                    for cv in data.get("claim_verdicts", [])
                ]
                selected = data.get("routing", {}).get("selected", [])
                return memo, brief, positions, claim_verdicts, selected

    # 3. Fallback: load latest run and reconstruct passing memo bundle
    if docs:
        latest_data = docs[0].to_dict()
        brief = DecisionBrief.model_validate(latest_data["brief"])
        memos = latest_data.get("memo_versions", [])
        if memos:
            memo_dict = dict(memos[-1])
            if len(memos) > 1 and "key_numbers" in memos[0]:
                kn0 = memos[0]["key_numbers"]
                add_kns = [kn for kn in kn0 if "Net monthly" in kn["text"] or "one-time costs" in kn["text"] or "payback in 6.25" in kn["text"]]
                memo_dict["key_numbers"] = add_kns + memo_dict["key_numbers"]
            memo = Memo.model_validate(memo_dict)
            positions = {
                p["adviser_id"]: AdviserPosition.model_validate(p)
                for p in latest_data.get("positions", [])
            }
            claim_verdicts = [
                ClaimVerdict.model_validate(cv)
                for cv in latest_data.get("claim_verdicts", [])
            ]
            selected = latest_data.get("routing", {}).get("selected", [])
            return memo, brief, positions, claim_verdicts, selected

    raise RuntimeError("No run or memo found in Firestore or runs/ to evaluate.")


def create_claim_position(
    test_id: str,
    source: str,
    input_text: str,
    cited_evidence: str,
) -> tuple[str, AdviserPosition]:
    """Build a one-claim AdviserPosition for MT-01 through MT-08."""
    source_lower = source.lower().strip()
    if "info" in source_lower:
        aid = "infosec"
    elif "op" in source_lower:
        aid = "ops"
    elif "fin" in source_lower:
        aid = "finance"
    elif "bus" in source_lower:
        aid = "business"
    elif "cus" in source_lower:
        aid = "customer"
    elif "leg" in source_lower:
        aid = "legal"
    else:
        aid = source_lower

    eids = [x.strip() for x in cited_evidence.split("|") if x.strip()] if cited_evidence else []

    if test_id == "MT-07":
        # MT-07 is a blocking finding from infosec
        bf = BlockingFinding(
            text=input_text,
            evidence_ids=eids,
            fix="Ensure all payment data is processed exclusively within India.",
        )
        pos = AdviserPosition(
            adviser_id=aid,
            stance="conditional",
            summary=input_text,
            claims=[],
            criterion_scores=[],
            blocking_findings=[bf],
            confidence=0.95,
        )
        return aid, pos

    kind = "fact"
    formula = None
    value = None
    unit = None

    if test_id == "MT-04":
        kind = "calculation"
        formula = "120 - 105.6"
        value = 18.0
        unit = "INR lakh / month"
    elif test_id == "MT-06":
        kind = "fact"
        value = None
    elif test_id == "MT-01":
        kind = "fact"
        value = 18.0
        unit = "%"
    elif test_id == "MT-02":
        kind = "fact"
        value = 4.0
        unit = "weeks"
    elif test_id == "MT-08":
        kind = "fact"
        value = 3.1
        unit = "points"

    claim = Claim(
        claim_id=f"{aid}-1",
        text=input_text,
        evidence_ids=eids,
        kind=kind,
        formula=formula,
        value=value,
        unit=unit,
    )
    pos = AdviserPosition(
        adviser_id=aid,
        stance="conditional",
        summary=input_text,
        claims=[claim],
        criterion_scores=[],
        blocking_findings=[],
        confidence=0.9,
    )
    return aid, pos


def create_memo_variant(test_id: str, base_memo: Memo) -> Memo:
    """Create a variant of base_memo for memo test cases (MT-09 through MT-14)."""
    variant = base_memo.model_copy(deep=True)

    if test_id == "MT-09":
        # recommend "move 40% now" and remove every condition about Singapore sub-processor
        variant.recommendation = "Move 40% of payment volume to Brevis Pay now."
        variant.stance = "B"
        singapore_kws = ["singapore", "sub-processor", "subprocessor", "data localization", "hosting add-on", "india-only", "mumbai"]
        variant.conditions = [c for c in variant.conditions if not any(k in c.lower() for k in singapore_kws)]
        variant.phased_plan = [p for p in variant.phased_plan if not any(k in p.lower() for k in singapore_kws)]

    elif test_id == "MT-10":
        # remove all mention of customer impact
        variant.scorecard.rows = [r for r in variant.scorecard.rows if "customer" not in r.criterion.lower()]
        variant.scorecard.weighted_totals = {"A": 2.3, "B": 2.975}
        variant.key_numbers = [
            kn for kn in variant.key_numbers
            if "customer" not in kn.text.lower()
            and "conversion" not in kn.text.lower()
            and "checkout" not in kn.text.lower()
            and "complaint" not in kn.text.lower()
        ]
        variant.numbers_to_watch = [
            n for n in variant.numbers_to_watch
            if "customer" not in n.lower()
            and "churn" not in n.lower()
            and "complaint" not in n.lower()
        ]

    elif test_id == "MT-11":
        # set the payback key number to 3 months citing FIN-03, FIN-04, FIN-05 and LEG-01
        kn11 = Claim(
            claim_id="payback-1",
            text="Memo says payback is about 3 months.",
            kind="fact",
            value=3.0,
            unit="months",
            evidence_ids=["FIN-03", "FIN-04", "FIN-05", "LEG-01"],
        )
        variant.key_numbers = [kn11] + [kn for kn in variant.key_numbers if "payback" not in kn.text.lower()]

    elif test_id == "MT-12":
        # empty the dissent list
        variant.dissent = []

    elif test_id == "MT-13":
        # set the exit fee key number to ₹50 lakh citing LEG-01
        kn13 = Claim(
            claim_id="exit-fee-1",
            text="Arvanta early exit fee is ₹50 lakh.",
            kind="fact",
            value=50.0,
            unit="INR lakh",
            evidence_ids=["LEG-01"],
        )
        variant.key_numbers = [kn13] + [kn for kn in variant.key_numbers if "exit" not in kn.text.lower()]

    elif test_id == "MT-14":
        # unchanged memo
        pass

    return variant


def evaluate_moderator() -> str:
    """Run moderator evaluations on all claim and memo test cases."""
    tests = load_moderator_tests()
    base_memo, brief, positions, claim_verdicts, selected_advisers = load_latest_passed_memo()

    lines: List[str] = []
    lines.append("=" * 120)
    lines.append("COLOSSEUM EVALUATION — MODERATOR REPORT")
    lines.append("=" * 120)
    lines.append(f"{'test':<6} | {'stage':<6} | {'expected':<10} | {'actual':<10} | {'match':<5} | moderator's reason")
    lines.append("-" * 120)

    matched_count = 0

    for t in tests:
        tid = t["test_id"]
        stage = t["stage"].lower().strip()
        expected = t["expected"].upper().strip()

        if stage == "claim":
            aid, pos = create_claim_position(
                test_id=tid,
                source=t["source"],
                input_text=t["input"],
                cited_evidence=t.get("cited_evidence", ""),
            )
            verdicts = check_claims({aid: pos})
            if verdicts:
                actual = verdicts[0].status.value.upper()
                reason = verdicts[0].reason
            else:
                actual = "ERROR"
                reason = "No verdict returned"

        elif stage == "memo":
            variant = create_memo_variant(test_id=tid, base_memo=base_memo)
            final_verdict = check_memo(
                brief=brief,
                memo=variant,
                selected_advisers=selected_advisers,
                positions=positions,
                claim_verdicts=claim_verdicts,
            )
            actual = final_verdict.verdict.value.upper()
            if final_verdict.issues:
                reason = "; ".join(f"[{i.kind}] {i.detail}" for i in final_verdict.issues)
            elif final_verdict.revision_instructions:
                reason = final_verdict.revision_instructions
            else:
                reason = "All checks passed."
        else:
            actual = "UNKNOWN"
            reason = f"Unknown stage: {stage}"

        matched = (actual == expected)
        if matched:
            matched_count += 1

        match_str = "YES" if matched else "NO"
        lines.append(f"{tid:<6} | {stage:<6} | {expected:<10} | {actual:<10} | {match_str:<5} | {reason}")

    lines.append("-" * 120)
    lines.append(f"TOTAL: {matched_count}/{len(tests)} match")
    lines.append("=" * 120)

    report = "\n".join(lines)
    return report


def judge_memo(memo: Memo, expected_outcome_md: str) -> List[Dict[str, Any]]:
    """Score a final memo against the 8-point checklist using the adviser model."""
    settings = get_settings()
    client = get_genai_client(caller="judge")

    memo_summary = f"""Decision ID: {memo.decision_id}
Recommendation: {memo.recommendation}
Stance: {memo.stance}
Confidence: {memo.confidence}
Conditions:
{json.dumps(memo.conditions, indent=2)}
Phased Plan:
{json.dumps(memo.phased_plan, indent=2)}
Dissent:
{json.dumps(memo.dissent, indent=2)}
Key Numbers:
{json.dumps([c.model_dump() for c in memo.key_numbers], indent=2)}
Assumptions:
{json.dumps(memo.assumptions, indent=2)}
Numbers to Watch:
{json.dumps(memo.numbers_to_watch, indent=2)}"""

    prompt = f"""You are an expert evaluator assessing a decision memo produced for decision brief DEC-001.

Here is the reference expected outcome:
=== EXPECTED OUTCOME ===
{expected_outcome_md}
=== END EXPECTED OUTCOME ===

Here is the decision memo to evaluate:
=== DECISION MEMO ===
{memo_summary}
=== END DECISION MEMO ===

Score the memo against this EXACT 8-point checklist. For each item, award 1 point if satisfied or 0 points if not satisfied, and provide a concise one-line reason:

1. stance conditional: Memo stance is conditional (e.g. stance is 'conditional' or recommends conditional/phased move rather than outright move or outright rejection).
2. phase 1 smaller than 40%, e.g. about 15%: Phase 1 of migration moves volume smaller than 40%, e.g. about 15% (or 10-20%).
3. India-only processing is a condition: Brevis committing to India-only processing / domestic infrastructure / switching on India-only fraud scoring is specified as an explicit condition.
4. pen test fix is a condition: Remediating/closing Brevis's open high-severity penetration test finding is specified as an explicit condition.
5. current SOC 2 report is a condition: Providing a current/valid SOC 2 Type II report is specified as an explicit condition.
6. contract fixes listed (at least two of audit rights, Indian law, price lock, DPA, 2-hour notification): At least two of the following contract fixes are explicitly listed: audit rights, Indian governing law/jurisdiction, price lock, signed DPA, 2-hour incident notification.
7. exit fee avoided in phase 1 or correctly costed: The exit fee is either avoided in Phase 1 (e.g. by keeping volume threshold above 80%) or correctly costed/accounted for in the financial calculations.
8. dissent recorded: Disagreements/dissent from advisers (e.g., Legal, InfoSec, or Business) are recorded.

Respond ONLY with valid JSON in this structure:
{{
  "items": [
    {{"id": 1, "criterion": "stance conditional", "score": 1, "reason": "..."}},
    {{"id": 2, "criterion": "phase 1 smaller than 40%, e.g. about 15%", "score": 1, "reason": "..."}},
    {{"id": 3, "criterion": "India-only processing is a condition", "score": 1, "reason": "..."}},
    {{"id": 4, "criterion": "pen test fix is a condition", "score": 1, "reason": "..."}},
    {{"id": 5, "criterion": "current SOC 2 report is a condition", "score": 1, "reason": "..."}},
    {{"id": 6, "criterion": "contract fixes listed (at least two of audit rights, Indian law, price lock, DPA, 2-hour notification)", "score": 1, "reason": "..."}},
    {{"id": 7, "criterion": "exit fee avoided in phase 1 or correctly costed", "score": 1, "reason": "..."}},
    {{"id": 8, "criterion": "dissent recorded", "score": 1, "reason": "..."}}
  ]
}}
"""

    try:
        response = generate(
            model=settings.models.adviser,
            contents=prompt,
            config={"temperature": 0.0},
            caller="judge",
        )
        raw = response if isinstance(response, str) else (getattr(response, "text", "") or str(response))
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw.strip(), flags=re.MULTILINE)
        data = json.loads(raw)
        if "items" in data and isinstance(data["items"], list):
            return data["items"]
        if isinstance(data, list):
            return data
    except Exception as exc:
        print(f"[evals] Warning: LLM judge failed ({exc}); returning fallback 0 scores.")
        return [
            {"id": i, "criterion": f"Criterion {i}", "score": 0, "reason": f"Evaluation error: {exc}"}
            for i in range(1, 9)
        ]

    return []


def evaluate_outcome(brief_path: Path, num_runs: int, runs_dir: Path, pause_s: int = 0) -> str:
    """Run the pipeline num_runs times on brief_path and evaluate final memos against the 8-point checklist."""
    expected_outcome_file = root_dir / "evals" / "expected_outcome.md"
    expected_outcome_md = expected_outcome_file.read_text(encoding="utf-8") if expected_outcome_file.is_file() else ""

    run_results: List[Dict[str, Any]] = []

    for i in range(1, num_runs + 1):
        print(f"\n[evals] ========================================================")
        print(f"[evals] Executing pipeline run {i}/{num_runs} for outcome eval...")
        print(f"[evals] ========================================================")
        t0 = time.time()
        record = run_decision(brief=brief_path)
        elapsed = time.time() - t0

        final_memo = record.memo_versions[-1]
        final_verdict = record.final_verdicts[-1].verdict.value if record.final_verdicts else "UNKNOWN"
        revision_count = max(0, len(record.memo_versions) - 1)
        verdict_seq = " -> ".join(v.verdict.value for v in record.final_verdicts)

        print(f"[evals] Run {i} finished in {elapsed:.1f}s. Verdict: {final_verdict} ({verdict_seq}), Revisions: {revision_count}")
        print(f"[evals] Judging final memo against 8-point checklist...")
        checklist_items = judge_memo(final_memo, expected_outcome_md)
        total_score = sum(int(item.get("score", 0)) for item in checklist_items)

        run_results.append({
            "run_num": i,
            "run_id": record.run_id,
            "final_verdict": final_verdict,
            "revision_count": revision_count,
            "verdict_sequence": verdict_seq,
            "elapsed_s": elapsed,
            "items": checklist_items,
            "total_score": total_score,
        })

        if pause_s > 0 and i < num_runs:
            print(f"\n[evals] Pausing for {pause_s}s before next run...")
            time.sleep(pause_s)

    # Build report text
    lines: List[str] = []
    lines.append("=" * 105)
    lines.append(f"COLOSSEUM EVALUATION — OUTCOME REPORT (DEC-001, {num_runs} RUNS)")
    lines.append("=" * 105)
    lines.append("")

    # Detailed per-run tables
    for res in run_results:
        lines.append("-" * 105)
        lines.append(f"RUN #{res['run_num']} — ID: {res['run_id']}")
        lines.append(f"Final Verdict   : {res['final_verdict']}")
        lines.append(f"Verdict Sequence: {res['verdict_sequence']}")
        lines.append(f"Revision Count  : {res['revision_count']}")
        lines.append(f"Total Score     : {res['total_score']}/8 ({res['total_score'] / 8.0 * 100:.1f}%)")
        lines.append(f"Execution Time  : {res['elapsed_s']:.1f}s")
        lines.append("-" * 105)
        lines.append(f"{'#':<3} | {'Score':<5} | {'Criterion':<32} | Reason")
        lines.append("-" * 105)
        for item in res["items"]:
            cid = item.get("id", "-")
            sc = item.get("score", 0)
            crit = item.get("criterion", "")[:32]
            reason = item.get("reason", "")
            lines.append(f"{cid:<3} | {sc:<5} | {crit:<32} | {reason}")
        lines.append("")

    # Summary table across runs
    lines.append("=" * 105)
    lines.append("SUMMARY ACROSS ALL RUNS")
    lines.append("=" * 105)
    lines.append(f"{'Run':<6} | {'Run ID':<30} | {'Verdict':<10} | {'Revs':<5} | C1 | C2 | C3 | C4 | C5 | C6 | C7 | C8 | {'Total':<6} | %")
    lines.append("-" * 105)

    c_totals = [0] * 8
    total_revs = 0
    total_scores = 0

    for res in run_results:
        r_id = res["run_id"]
        if len(r_id) > 30:
            r_id = r_id[:27] + "..."
        v = res["final_verdict"]
        rev = res["revision_count"]
        total_revs += rev
        tot = res["total_score"]
        total_scores += tot

        c_scores = []
        for idx in range(8):
            sc = int(res["items"][idx].get("score", 0)) if idx < len(res["items"]) else 0
            c_totals[idx] += sc
            c_scores.append(str(sc))

        c_str = " |  ".join(c_scores)
        pct = (tot / 8.0) * 100
        lines.append(f"Run {res['run_num']:<2} | {r_id:<30} | {v:<10} | {rev:<5} |  {c_str} | {tot}/8  | {pct:.1f}%")

    lines.append("-" * 105)
    avg_scores = total_scores / len(run_results) if run_results else 0.0
    avg_revs = total_revs / len(run_results) if run_results else 0.0
    avg_c = [c / len(run_results) for c in c_totals] if run_results else [0.0] * 8
    avg_c_str = " | ".join(f"{c:.1f}" for c in avg_c)
    avg_pct = (avg_scores / 8.0) * 100
    lines.append(f"{'AVG':<6} | {'-':<30} | {'-':<10} | {avg_revs:<5.1f} | {avg_c_str} | {avg_scores:.2f}/8 | {avg_pct:.1f}%")
    lines.append("=" * 105)
    lines.append("")

    # Average score per checklist criterion
    lines.append("CHECKLIST CRITERION BREAKDOWN (AVERAGE):")
    criteria_names = [
        "(1) stance conditional",
        "(2) phase 1 smaller than 40%, e.g. about 15%",
        "(3) India-only processing is a condition",
        "(4) pen test fix is a condition",
        "(5) current SOC 2 report is a condition",
        "(6) contract fixes listed (at least two of audit rights, Indian law, price lock, DPA, 2-hour notification)",
        "(7) exit fee avoided in phase 1 or correctly costed",
        "(8) dissent recorded",
    ]
    for idx, name in enumerate(criteria_names):
        avg_sc = avg_c[idx] if idx < len(avg_c) else 0.0
        pct = (avg_sc / 1.0) * 100
        lines.append(f"  {name:<100}: {avg_sc:.2f}/1.0 ({pct:.1f}%)")
    lines.append(f"\nOverall Average Checklist Score: {avg_scores:.2f}/8.0 ({avg_pct:.1f}%)")
    lines.append("=" * 105)

    report = "\n".join(lines)
    return report


def main():
    parser = argparse.ArgumentParser(description="Colosseum Evaluation Runner")
    parser.add_argument(
        "mode",
        choices=["planted", "routing", "moderator", "outcome"],
        help="Evaluation mode ('planted', 'routing', 'moderator', or 'outcome')",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Rerun all advisers fresh before running evaluation",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of runs to execute for outcome evaluation (default: 3)",
    )
    parser.add_argument(
        "--pause",
        type=int,
        default=0,
        help="Pause in seconds between runs in outcome mode (e.g. --pause 60)",
    )
    args = parser.parse_args()

    if args.fresh:
        run_fresh_advisers()

    if args.mode == "planted":
        runs_dir = root_dir / "runs" / "day4"
        runs_dir.mkdir(parents=True, exist_ok=True)
        report = evaluate_planted(runs_dir)
        print(report)

        report_file = runs_dir / "eval_planted.txt"
        report_file.write_text(report, encoding="utf-8")
        print(f"\nReport saved to: {report_file}")

    elif args.mode == "routing":
        runs_dir = root_dir / "runs" / "day5"
        runs_dir.mkdir(parents=True, exist_ok=True)
        report = evaluate_routing()
        print(report)

        report_file = runs_dir / "eval_routing.txt"
        report_file.write_text(report, encoding="utf-8")
        print(f"\nReport saved to: {report_file}")

    elif args.mode == "moderator":
        runs_dir = root_dir / "runs" / "day6"
        runs_dir.mkdir(parents=True, exist_ok=True)
        report = evaluate_moderator()
        print(report)

        report_file = runs_dir / "eval_moderator.txt"
        report_file.write_text(report, encoding="utf-8")
        print(f"\nReport saved to: {report_file}")

    elif args.mode == "outcome":
        runs_dir = root_dir / "runs" / "day6"
        runs_dir.mkdir(parents=True, exist_ok=True)
        brief_path = root_dir / "evals" / "brief_DEC-001.json"
        report = evaluate_outcome(
            brief_path=brief_path,
            num_runs=args.runs,
            runs_dir=runs_dir,
            pause_s=args.pause,
        )
        print(report)

        report_file = runs_dir / "eval_outcome.txt"
        report_file.write_text(report, encoding="utf-8")
        print(f"\nReport saved to: {report_file}")


if __name__ == "__main__":
    main()


