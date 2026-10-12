"""Run a specific Colosseum adviser against a decision brief."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.adviser_factory import run_adviser
from core.schemas import DecisionBrief
from harness.context_builder import build_pack


def main():
    parser = argparse.ArgumentParser(description="Run a Colosseum adviser agent")
    parser.add_argument(
        "adviser_id",
        nargs="?",
        default="finance",
        help="Adviser ID to run (e.g. finance, business, customer, legal, infosec, ops). Default: finance",
    )
    args = parser.parse_args()
    adviser_id = args.adviser_id.lower().strip()

    # 1. Load sample decision brief
    root = Path(__file__).resolve().parent.parent
    brief_file = root / "evals" / "brief_DEC-001.json"
    if not brief_file.is_file():
        raise FileNotFoundError(f"Brief file not found: {brief_file}")

    with open(brief_file, "r", encoding="utf-8") as f:
        brief_data = json.load(f)
    brief = DecisionBrief.model_validate(brief_data)

    print(f"Building evidence pack for '{adviser_id}'...")
    pack = build_pack(adviser_id=adviser_id, brief=brief)
    print(f"Evidence pack ready: {len(pack.facts)} facts (total matching: {pack.total_matching_facts})")

    print(f"\nRunning adviser agent '{adviser_id}'...")
    start_time = time.time()
    position = run_adviser(adviser_id=adviser_id, brief=brief, evidence_pack=pack)
    elapsed = time.time() - start_time

    # 2. Print output in plain English
    print("\n" + "=" * 70)
    print(f"ADVISER POSITION: {adviser_id.upper()}")
    print("=" * 70)
    print(f"\nStance:     {position.stance}")
    print(f"Confidence: {position.confidence}")
    print(f"\nSummary:\n{position.summary}")

    if position.conditions:
        print("\nConditions:")
        for cond in position.conditions:
            print(f"  * {cond}")

    print("\nClaims:")
    for claim in position.claims:
        val_str = f" [value: {claim.value} {claim.unit or ''}]" if claim.value is not None else ""
        print(f"  [{claim.claim_id}] ({claim.kind.upper()}){val_str} {claim.text}")
        print(f"      Evidence IDs: {claim.evidence_ids}")

    print("\nCriterion Scores:")
    for cs in position.criterion_scores:
        print(f"  * {cs.criterion} | Option {cs.option}: {cs.score}/5 — {cs.reason}")
        print(f"      Evidence IDs: {cs.evidence_ids}")

    if position.blocking_findings:
        print("\nBlocking Findings:")
        for bf in position.blocking_findings:
            print(f"  * BLOCKER: {bf.text}")
            print(f"      Fix: {bf.fix}")
            print(f"      Evidence IDs: {bf.evidence_ids}")

    if position.handoffs:
        print("\nHandoffs to other advisers:")
        for h in position.handoffs:
            print(f"  * {h}")

    print(f"\nTime taken: {elapsed:.2f}s")
    print("=" * 70)

    # 3. Save JSON to runs/day4/<id>.json
    out_dir = root / "runs" / "day4"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{adviser_id}.json"
    out_file.write_text(position.model_dump_json(indent=2), encoding="utf-8")
    print(f"\nSaved position to {out_file}")


if __name__ == "__main__":
    main()
