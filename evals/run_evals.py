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
from core.settings import get_settings


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


def main():
    parser = argparse.ArgumentParser(description="Colosseum Evaluation Runner")
    parser.add_argument(
        "mode",
        choices=["planted", "routing"],
        help="Evaluation mode ('planted' or 'routing')",
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


if __name__ == "__main__":
    main()

