"""Diagnostic script for Scribe memo drafting.

Runs moderation and scorecard synthesis on the latest saved adviser outputs (runs/day4/),
calls write_memo to generate a decision memo, and prints it in readable sections:
recommendation, stance, confidence, conditions, phased plan, key numbers (with evidence),
blocking findings, dissent, assumptions, numbers to watch.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

# Ensure project root is on sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from agents.moderator import check_claims
from agents.scorecard import build_scorecard
from agents.scribe import write_memo
from core.schemas import AdviserPosition, DecisionBrief


def main():
    runs_dir = root_dir / "runs" / "day4"
    brief_file = root_dir / "evals" / "brief_DEC-001.json"

    print("=" * 85)
    print("SCRIBE MEMO GENERATOR FOR LATEST RUN (runs/day4/)")
    print("=" * 85)

    if not runs_dir.is_dir():
        print(f"Error: Runs directory not found at {runs_dir}")
        return

    if not brief_file.is_file():
        print(f"Error: Brief file not found at {brief_file}")
        return

    # Load brief
    brief_data = json.loads(brief_file.read_text(encoding="utf-8"))
    brief = DecisionBrief(**brief_data)

    # Load adviser positions
    positions: dict[str, AdviserPosition] = {}
    adviser_order = ["finance", "business", "customer", "legal", "infosec", "ops"]
    for aid in adviser_order:
        p = runs_dir / f"{aid}.json"
        if p.is_file():
            positions[aid] = AdviserPosition(**json.loads(p.read_text(encoding="utf-8")))

    print(f"Loaded {len(positions)} adviser positions for Decision {brief.decision_id}.")

    # Step 1: Moderate claims
    print("\nRunning claim moderation across advisers...")
    claim_verdicts = check_claims(positions=positions)
    n_kept = sum(1 for v in claim_verdicts if v.status.value == "KEEP")
    n_flag = sum(1 for v in claim_verdicts if v.status.value == "FLAG")
    n_strike = sum(1 for v in claim_verdicts if v.status.value == "STRIKE")
    print(f"Moderation finished: {n_kept} KEEP, {n_flag} FLAG, {n_strike} STRIKE.")

    # Step 2: Compute scorecard
    print("Computing scorecard synthesis...")
    scorecard = build_scorecard(brief=brief, positions=positions, claim_verdicts=claim_verdicts)
    totals_summary = ", ".join(f"Opt {opt}: {val:.2f}" for opt, val in scorecard.weighted_totals.items())
    print(f"Scorecard computed: Weighted totals ({totals_summary}) | Blocked options: {list(scorecard.blocked_options.keys())}")

    # Step 3: Write memo
    print("\nDrafting decision memo with Scribe reasoning model (temp 0.2)...")
    memo = write_memo(
        brief=brief,
        positions=positions,
        claim_verdicts=claim_verdicts,
        scorecard=scorecard,
    )
    print("Memo generated successfully!\n")

    # Step 4: Print readable sections
    print("=" * 85)
    print(f"DECISION MEMO: {memo.decision_id}")
    print("=" * 85)

    print("\n[RECOMMENDATION]")
    print(f"{memo.recommendation}")

    print("\n[STANCE & CONFIDENCE]")
    print(f"  * Stance    : {memo.stance}")
    print(f"  * Confidence: {memo.confidence:.1%}")

    print("\n[CONDITIONS]")
    if memo.conditions:
        for idx, cond in enumerate(memo.conditions, start=1):
            print(f"  {idx}. {cond}")
    else:
        print("  None.")

    print("\n[PHASED PLAN]")
    if memo.phased_plan:
        for idx, phase in enumerate(memo.phased_plan, start=1):
            print(f"  Phase {idx}: {phase}")
    else:
        print("  None.")

    print("\n[KEY NUMBERS (WITH EVIDENCE)]")
    if memo.key_numbers:
        for idx, kn in enumerate(memo.key_numbers, start=1):
            val_str = f" [Value: {kn.value} {kn.unit or ''}]".rstrip() if kn.value is not None else ""
            eids_str = ", ".join(kn.evidence_ids) if kn.evidence_ids else "None"
            print(f"  {idx}. {kn.text}{val_str}")
            print(f"     Evidence IDs: {eids_str}")
    else:
        print("  None.")

    print("\n[BLOCKING FINDINGS]")
    if memo.blocking_findings:
        for idx, bf in enumerate(memo.blocking_findings, start=1):
            print(f"  {idx}. Finding : {bf.text}")
            print(f"     Fix     : {bf.fix}")
            print(f"     Evidence: {', '.join(bf.evidence_ids)}")
    else:
        print("  None.")

    print("\n[DISSENT]")
    if memo.dissent:
        for idx, d in enumerate(memo.dissent, start=1):
            print(f"  {idx}. {d}")
    else:
        print("  None.")

    print("\n[ASSUMPTIONS]")
    if memo.assumptions:
        for idx, a in enumerate(memo.assumptions, start=1):
            print(f"  {idx}. {a}")
    else:
        print("  None.")

    print("\n[NUMBERS TO WATCH]")
    if memo.numbers_to_watch:
        for idx, nw in enumerate(memo.numbers_to_watch, start=1):
            print(f"  {idx}. {nw}")
    else:
        print("  None.")

    print("\n[EVIDENCE INDEX]")
    print(f"  Total unique evidence items cited: {len(memo.evidence_index)}")
    print(f"  {', '.join(memo.evidence_index)}")
    print("=" * 85)


if __name__ == "__main__":
    main()
