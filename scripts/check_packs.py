"""Check evidence pack construction and planted fact coverage per adviser."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import sys
from typing import Dict, List, Set

# Ensure project root is in sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from core.registry import get_registry
from core.schemas import DecisionBrief
from core.settings import get_settings
from harness.context_builder import build_pack
from harness.routing_query import make_routing_query


def load_planted_facts() -> Dict[str, List[str]]:
    """Load planted fact IDs grouped by adviser from BigQuery or fallback to CSV."""
    settings = get_settings()
    planted_by_adviser: Dict[str, List[str]] = {}

    try:
        from google.cloud import bigquery

        client = bigquery.Client(
            project=settings.project_id,
            location=settings.bigquery.location,
        )
        query = f"""
            SELECT fact_id, adviser, planted
            FROM `{settings.project_id}.{settings.bigquery.eval_dataset}.planted_facts_answer_key`
        """
        query_job = client.query(query, timeout=10.0)
        for row in query_job.result(timeout=10.0):
            is_planted = row.planted is True or str(row.planted).lower() in ("yes", "true", "1")
            if is_planted:
                aid = str(row.adviser).lower().strip()
                fid = str(row.fact_id).strip()
                planted_by_adviser.setdefault(aid, []).append(fid)
        if planted_by_adviser:
            return planted_by_adviser
    except Exception as exc:
        print(f"[check_packs] Note: BigQuery colosseum_eval query fell back to local CSV ({exc})")

    csv_key_file = root_dir / "evals" / "planted_facts_answer_key.csv"
    if csv_key_file.is_file():
        with open(csv_key_file, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("planted", "").lower() in ("yes", "true", "1"):
                    aid = row["adviser"].lower().strip()
                    fid = row["fact_id"].strip()
                    planted_by_adviser.setdefault(aid, []).append(fid)

    return planted_by_adviser


def main():
    parser = argparse.ArgumentParser(description="Check evidence packs for gateway decision brief")
    parser.add_argument(
        "--max",
        type=int,
        default=None,
        help="Maximum facts per pack before aggregation (default from settings)",
    )
    args = parser.parse_args()

    settings = get_settings()
    max_cap = args.max if args.max is not None else settings.retrieval.max_facts_per_adviser

    # 1. Load gateway brief
    brief_file = root_dir / "evals" / "brief_DEC-001.json"
    if not brief_file.is_file():
        raise FileNotFoundError(f"Brief file not found: {brief_file}")

    with open(brief_file, "r", encoding="utf-8") as f:
        brief_data = json.load(f)
    brief = DecisionBrief.model_validate(brief_data)

    print("=" * 90)
    print(f"CHECK EVIDENCE PACKS: {brief.decision_id} — {brief.question}")
    print(f"Max facts threshold: {max_cap}")
    print("=" * 90)

    # 2. Generate routing query
    print("\nGenerating routing query...")
    routing_query = make_routing_query(brief)
    print(f"Routing query:\n{routing_query}\n")

    # 3. Load planted facts from colosseum_eval
    planted_by_adviser = load_planted_facts()

    registry = get_registry()
    adviser_ids = registry.get_ids()

    # 4. Build packs and collect statistics
    pack_stats = []
    adviser_breakdowns = []

    print("-" * 90)
    print(f"{'adviser':<12} | {'matching facts':<14} | {'in pack':<8} | {'summaries':<10} | {'truncated':<10}")
    print("-" * 90)

    for aid in adviser_ids:
        pack = build_pack(
            adviser_id=aid,
            brief=brief,
            routing_query=routing_query,
            max_facts=max_cap,
        )

        in_pack_count = len(pack.facts)
        summaries_count = len(pack.summaries)
        matching_count = pack.total_matching_facts
        truncated_flag = pack.truncated

        print(f"{aid:<12} | {matching_count:<14} | {in_pack_count:<8} | {summaries_count:<10} | {str(truncated_flag):<10}")

        # Check planted facts status
        planted_fids = sorted(planted_by_adviser.get(aid, []))
        full_fact_ids: Set[str] = {f.fact_id for f in pack.facts}

        in_full: List[str] = []
        in_summaries: List[str] = []
        missing: List[str] = []

        for fid in planted_fids:
            if fid in full_fact_ids:
                in_full.append(fid)
            elif any(fid in s for s in pack.summaries):
                in_summaries.append(fid)
            else:
                missing.append(fid)

        adviser_breakdowns.append({
            "adviser": aid,
            "total_planted": len(planted_fids),
            "in_full": in_full,
            "in_summaries": in_summaries,
            "missing": missing,
        })

    # 5. Print planted facts breakdown
    print("\n" + "=" * 90)
    print("PLANTED FACTS COVERAGE PER ADVISER")
    print("=" * 90)

    for b in adviser_breakdowns:
        aid = b["adviser"]
        tot = b["total_planted"]
        in_full_str = ", ".join(b["in_full"]) if b["in_full"] else "(none)"
        in_sum_str = ", ".join(b["in_summaries"]) if b["in_summaries"] else "(none)"
        missing_str = ", ".join(b["missing"]) if b["missing"] else "(none)"

        print(f"\n[{aid.upper()}] (Total planted facts: {tot})")
        print(f"  • Full facts in pack ({len(b['in_full'])}): {in_full_str}")
        print(f"  • In summaries only  ({len(b['in_summaries'])}): {in_sum_str}")
        print(f"  • Missing entirely   ({len(b['missing'])}): {missing_str}")


if __name__ == "__main__":
    main()
