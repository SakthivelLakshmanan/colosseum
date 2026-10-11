"""Run all six Colosseum advisers concurrently and display a summary table."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

# Ensure project root is on sys.path
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from agents.adviser_factory import get_last_adviser_durations, run_advisers, simple_pack
from core.registry import get_registry
from core.schemas import AdviserPosition, DecisionBrief


async def main_async(timeout_override: float | None = None):
    root = Path(__file__).resolve().parent.parent
    brief_file = root / "evals" / "brief_DEC-001.json"
    if not brief_file.is_file():
        raise FileNotFoundError(f"Brief file not found: {brief_file}")

    with open(brief_file, "r", encoding="utf-8") as f:
        brief = DecisionBrief.model_validate(json.load(f))

    registry = get_registry()
    adviser_ids = registry.get_ids()
    print("=" * 80)
    print(f"COLOSSEUM — RUNNING ALL {len(adviser_ids)} ADVISERS CONCURRENTLY")
    print(f"Decision: {brief.decision_id} — {brief.question[:70]}...")
    print("=" * 80)

    # 1. Build simple evidence pack for each adviser
    print("\nPreparing evidence packs for advisers...")
    packs = {}
    for aid in adviser_ids:
        pack = simple_pack(adviser_id=aid, brief=brief)
        packs[aid] = pack
        print(f"  * {aid:<10}: {len(pack.facts)} facts (matching: {pack.total_matching_facts})")

    # 2. Run advisers concurrently with timeout
    print(f"\nDispatching {len(adviser_ids)} advisers in parallel (asyncio)...")
    wall_start = time.time()
    results = await run_advisers(
        adviser_ids=adviser_ids,
        brief=brief,
        packs=packs,
        timeout_s=timeout_override,
    )
    wall_elapsed = time.time() - wall_start
    durations = get_last_adviser_durations()

    # 3. Save each result to runs/day4/<id>.json
    out_dir = root / "runs" / "day4"
    out_dir.mkdir(parents=True, exist_ok=True)
    for aid in adviser_ids:
        res = results.get(aid)
        out_file = out_dir / f"{aid}.json"
        if isinstance(res, AdviserPosition):
            out_file.write_text(res.model_dump_json(indent=2), encoding="utf-8")
        else:
            err_data = {
                "adviser_id": aid,
                "status": "failed",
                "error": str(res),
                "duration_s": durations.get(aid, 0.0),
            }
            out_file.write_text(json.dumps(err_data, indent=2), encoding="utf-8")

    # 4. Format and print the summary table
    print("\n" + "=" * 105)
    print("ALL ADVISERS EXECUTION SUMMARY")
    print("=" * 105)

    headers = [
        "Adviser",
        "Stance",
        "Claims",
        "Distinct Ev IDs",
        "Blockers",
        "Seconds",
        "Error",
    ]
    rows: list[list[str]] = []

    for aid in adviser_ids:
        res = results.get(aid)
        sec_str = f"{durations.get(aid, 0.0):.1f}"

        if isinstance(res, AdviserPosition):
            distinct_ev: set[str] = set()
            for c in res.claims:
                distinct_ev.update(c.evidence_ids)
            for cs in res.criterion_scores:
                distinct_ev.update(cs.evidence_ids)
            for bf in res.blocking_findings:
                distinct_ev.update(bf.evidence_ids)

            rows.append(
                [
                    aid,
                    res.stance,
                    str(len(res.claims)),
                    str(len(distinct_ev)),
                    str(len(res.blocking_findings)),
                    sec_str,
                    "-",
                ]
            )
        else:
            rows.append(
                [
                    aid,
                    "-",
                    "0",
                    "0",
                    "0",
                    sec_str,
                    str(res),
                ]
            )

    # Compute column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(val))

    def make_divider(char="-") -> str:
        return "+" + "+".join(char * (w + 2) for w in col_widths) + "+"

    def format_row(row_vals: list[str]) -> str:
        items = [f" {val:<{col_widths[i]}} " for i, val in enumerate(row_vals)]
        return "|" + "|".join(items) + "|"

    print(make_divider("-"))
    print(format_row(headers))
    print(make_divider("="))
    for row in rows:
        print(format_row(row))
    print(make_divider("-"))

    print(f"\nTotal wall-clock time: {wall_elapsed:.2f}s")
    print(f"Individual runs saved to: {out_dir}/")
    print("=" * 105)


def main():
    parser = argparse.ArgumentParser(description="Run all six advisers concurrently")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Optional timeout in seconds per adviser (defaults to settings adviser_timeout_s)",
    )
    args = parser.parse_args()
    asyncio.run(main_async(timeout_override=args.timeout))


if __name__ == "__main__":
    main()
