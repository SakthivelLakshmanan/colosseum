"""Run end-to-end decision pipeline and print summary report.

Runs evals/brief_DEC-001.json through agents.pipeline.run_decision and prints:
1. Run ID
2. Routing table
3. Each adviser's stance and claim count
4. Kept/flagged/struck totals
5. The scorecard
6. The final memo's recommendation and conditions
7. The verdict sequence (e.g. "INCOMPLETE -> PASS")
8. Total time
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

# Ensure project root is on sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from agents.pipeline import run_decision
from core.schemas import ClaimStatus, DecisionBrief


def main():
    brief_file = root_dir / "evals" / "brief_DEC-001.json"
    if not brief_file.is_file():
        print(f"Error: Brief file not found at {brief_file}")
        sys.exit(1)

    brief_data = json.loads(brief_file.read_text(encoding="utf-8"))
    brief = DecisionBrief.model_validate(brief_data)

    print("=" * 85)
    print("COLOSSEUM END-TO-END DECISION PIPELINE")
    print(f"Decision : {brief.decision_id} - {brief.question[:70]}...")
    print("=" * 85)

    # Execute pipeline
    run_record = run_decision(brief=brief)

    # 1. Run ID
    print("\n" + "=" * 85)
    print("PIPELINE EXECUTION SUMMARY")
    print("=" * 85)
    print(f"Run ID: {run_record.run_id}")

    # 2. Routing table
    print("\n[ROUTING TABLE]")
    print(f"{'Adviser':<12} | {'Similarity':<10} | {'Selected':<8} | Reason")
    print("-" * 85)
    for s in run_record.routing.scores:
        sel_str = "YES" if s.selected else "NO"
        print(f"{s.adviser_id:<12} | {s.similarity:<10.4f} | {sel_str:<8} | {s.reason}")

    # 3. Each adviser's stance and claim count
    print("\n[ADVISER POSITIONS]")
    print(f"{'Adviser':<12} | {'Stance':<20} | {'Claims':<8} | {'Blockers':<8} | Confidence")
    print("-" * 85)
    for pos in run_record.positions:
        print(
            f"{pos.adviser_id:<12} | {pos.stance:<20} | {len(pos.claims):<8} | "
            f"{len(pos.blocking_findings):<8} | {pos.confidence:.1%}"
        )

    # 4. Kept/flagged/struck totals
    verdicts = run_record.claim_verdicts
    n_kept = sum(1 for v in verdicts if v.status == ClaimStatus.keep)
    n_flag = sum(1 for v in verdicts if v.status == ClaimStatus.flag)
    n_strike = sum(1 for v in verdicts if v.status == ClaimStatus.strike)

    print("\n[CLAIM MODERATION TOTALS]")
    print(f"  * Total Claims Evaluated: {len(verdicts)}")
    print(f"  * Kept   : {n_kept}")
    print(f"  * Flagged: {n_flag}")
    print(f"  * Struck : {n_strike}")

    # 5. Scorecard
    if run_record.memo_versions:
        scorecard = run_record.memo_versions[-1].scorecard
        print("\n[SCORECARD]")
        opt_headers = " | ".join(f"{'Opt ' + opt:<8}" for opt in brief.options)
        print(f"{'Criterion':<24} | {'Weight':<6} | {opt_headers} | Evidence IDs")
        print("-" * 85)
        for row in scorecard.rows:
            scores_str = " | ".join(f"{row.scores.get(opt, 0.0):<8.2f}" for opt in brief.options)
            eids_str = ", ".join(row.evidence_ids[:5]) + ("..." if len(row.evidence_ids) > 5 else "")
            print(f"{row.criterion:<24} | {row.weight:<6.2f} | {scores_str} | {eids_str}")
        print("-" * 85)
        totals_str = " | ".join(
            f"Option {opt}: {round(scorecard.weighted_totals.get(opt, 0.0), 2)}"
            for opt in brief.options
        )
        print(f"WEIGHTED TOTALS: {totals_str}")
        if scorecard.blocked_options:
            print("BLOCKED OPTIONS:")
            for b_opt, f_list in scorecard.blocked_options.items():
                print(f"  * Option {b_opt} is BLOCKED by {len(f_list)} finding(s)")

    # 6. Final memo's recommendation and conditions
    final_memo = run_record.memo_versions[-1]
    print("\n[FINAL MEMO RECOMMENDATION]")
    print(f"{final_memo.recommendation}")

    print("\n[FINAL MEMO CONDITIONS]")
    if final_memo.conditions:
        for idx, cond in enumerate(final_memo.conditions, start=1):
            print(f"  {idx}. {cond}")
    else:
        print("  None.")

    # 7. Verdict sequence
    verdict_sequence = " -> ".join(v.verdict.value for v in run_record.final_verdicts)
    print("\n[VERDICT SEQUENCE]")
    print(f"  {verdict_sequence}")

    # 8. Total time
    total_time = run_record.metrics.get("total_time_s", 0.0)
    print("\n[EXECUTION METRICS]")
    print(f"  * Total Time     : {total_time:.2f}s")
    for k, v in sorted(run_record.metrics.items()):
        if k != "total_time_s":
            print(f"    - {k:<25}: {v:.2f}s")
    print("=" * 85)


if __name__ == "__main__":
    main()
