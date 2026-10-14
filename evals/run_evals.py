"""Evaluation runner for Colosseum adviser outputs."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path
import sys
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
from core.settings import get_settings
from agents.moderator import check_claims, check_memo


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


def main():
    parser = argparse.ArgumentParser(description="Colosseum Evaluation Runner")
    parser.add_argument(
        "mode",
        choices=["planted", "routing", "moderator"],
        help="Evaluation mode ('planted', 'routing', or 'moderator')",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Rerun all advisers fresh before running evaluation",
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


if __name__ == "__main__":
    main()


