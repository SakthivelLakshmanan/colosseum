"""Diagnostic script for moderator claim verification.

(a) Runs check_claims on the latest saved adviser outputs (runs/day4/*.json) and prints
    per-adviser counts (kept / flagged / struck) and every FLAG and STRIKE with reason.
(b) Runs check_claims on three hand-made test claims:
    1. "Brevis saves 18%" citing FIN-12 (superseded)
    2. "Gross saving is ₹18 lakh a month" with formula "120 - 105.6", value 18, citing FIN-02 and FIN-03
    3. "Migration takes under 4 weeks" citing only OPS-13 (low reliability)
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
from core.registry import get_registry
from core.schemas import AdviserPosition, Claim, ClaimStatus, ClaimVerdict, DecisionBrief


def run_part_a(runs_dir: Path):
    print("=" * 85)
    print("PART (A): MODERATING LATEST SAVED ADVISER OUTPUTS (runs/day4/)")
    print("=" * 85)

    registry = get_registry()
    adviser_ids = registry.get_ids()

    positions: dict[str, AdviserPosition] = {}
    for aid in adviser_ids:
        json_file = runs_dir / f"{aid}.json"
        if not json_file.is_file():
            print(f"Warning: File not found: {json_file}")
            continue
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            pos = AdviserPosition.model_validate(data)
            positions[aid] = pos
        except Exception as e:
            print(f"Warning: Could not parse {json_file}: {e}")

    if not positions:
        print("No valid adviser positions found to moderate.")
        return

    print(f"\nRunning check_claims on {len(positions)} advisers...")
    verdicts = check_claims(positions=positions)

    # Map verdicts by adviser
    verdicts_by_adviser: dict[str, list[ClaimVerdict]] = {aid: [] for aid in positions}
    for v in verdicts:
        for aid, pos in positions.items():
            expected_ids = {c.claim_id for c in pos.claims} | {
                f"{aid}-B{idx}" for idx in range(1, len(pos.blocking_findings) + 1)
            }
            if v.claim_id in expected_ids:
                verdicts_by_adviser[aid].append(v)
                break

    # Summary table
    print("\n" + "-" * 70)
    print(f"{'Adviser':<12} | {'Total Claims':<14} | {'Kept':<8} | {'Flagged':<9} | {'Struck':<8}")
    print("-" * 70)

    total_claims = 0
    total_kept = 0
    total_flagged = 0
    total_struck = 0
    flagged_and_struck: list[tuple[str, ClaimVerdict]] = []

    for aid in adviser_ids:
        v_list = verdicts_by_adviser.get(aid, [])
        n_total = len(v_list)
        n_kept = sum(1 for v in v_list if v.status == ClaimStatus.keep)
        n_flagged = sum(1 for v in v_list if v.status == ClaimStatus.flag)
        n_struck = sum(1 for v in v_list if v.status == ClaimStatus.strike)

        total_claims += n_total
        total_kept += n_kept
        total_flagged += n_flagged
        total_struck += n_struck

        for v in v_list:
            if v.status in (ClaimStatus.flag, ClaimStatus.strike):
                flagged_and_struck.append((aid, v))

        print(f"{aid:<12} | {n_total:<14} | {n_kept:<8} | {n_flagged:<9} | {n_struck:<8}")

    print("-" * 70)
    print(f"{'TOTAL':<12} | {total_claims:<14} | {total_kept:<8} | {total_flagged:<9} | {total_struck:<8}")
    print("-" * 70)

    # Detailed list of FLAG and STRIKE
    print("\nDETAILED FLAGGED & STRUCK CLAIMS:")
    if not flagged_and_struck:
        print("  None. All claims were KEPT.")
    else:
        for aid, v in flagged_and_struck:
            status_tag = f"[{v.status.value}]"
            print(f"  {status_tag:<8} ({aid}) Claim '{v.claim_id}' [{v.check}]: {v.reason}")

    # Blocking findings section
    print("\n" + "=" * 85)
    print("BLOCKING FINDINGS")
    print("=" * 85)

    all_blocking_findings = []
    for aid in adviser_ids:
        pos = positions.get(aid)
        if pos and pos.blocking_findings:
            for idx, bf in enumerate(pos.blocking_findings, start=1):
                bf_id = f"{aid}-B{idx}"
                all_blocking_findings.append((aid, bf_id, bf))

    if not all_blocking_findings:
        print("WARNING: no blocking findings raised")
    else:
        verdict_by_id = {v.claim_id: v for v in verdicts}
        for aid, bf_id, bf in all_blocking_findings:
            v = verdict_by_id.get(bf_id)
            status_str = v.status.value if v else "UNKNOWN"
            reason_str = v.reason if v else "No verdict recorded"
            check_str = v.check if v else "unknown"

            print(f"\n[{status_str}] Adviser: {aid} | Finding: {bf_id} [{check_str}]")
            print(f"  * Text        : {bf.text}")
            print(f"  * Evidence IDs: {', '.join(bf.evidence_ids)}")
            print(f"  * Verdict     : {status_str}")
            print(f"  * Reason      : {reason_str}")

    # Scorecard section
    brief_file = root_dir / "evals" / "brief_DEC-001.json"
    if brief_file.is_file():
        brief_data = json.loads(brief_file.read_text())
        brief = DecisionBrief(**brief_data)
        scorecard = build_scorecard(brief=brief, positions=positions, claim_verdicts=verdicts)

        print("\n" + "=" * 85)
        print("SCORECARD (SYNTHESIS)")
        print("=" * 85)
        opt_headers = " | ".join(f"{'Opt ' + opt:<8}" for opt in brief.options)
        print(f"{'Criterion':<24} | {'Weight':<6} | {opt_headers} | Evidence IDs")
        print("-" * 85)
        for row in scorecard.rows:
            scores_str = " | ".join(f"{row.scores.get(opt, 0.0):<8.2f}" for opt in brief.options)
            eids_str = ", ".join(row.evidence_ids[:5]) + ("..." if len(row.evidence_ids) > 5 else "")
            print(f"{row.criterion:<24} | {row.weight:<6.2f} | {scores_str} | {eids_str}")
        print("-" * 85)
        totals_str = " | ".join(
            f"{'Option ' + opt + ': ' + str(round(scorecard.weighted_totals.get(opt, 0.0), 2)):<14}"
            for opt in brief.options
        )
        print(f"WEIGHTED TOTALS: {totals_str}")
        print("-" * 85)

        print("\nBLOCKED OPTIONS:")
        if not scorecard.blocked_options:
            print("  None. No options are currently blocked.")
        else:
            for opt, findings in scorecard.blocked_options.items():
                print(f"  Option {opt} is BLOCKED by {len(findings)} unresolved finding(s):")
                for f_text in findings:
                    print(f"    - {f_text}")


def run_part_b():
    print("\n" + "=" * 85)
    print("PART (B): THREE HAND-MADE CLAIMS VERIFICATION")
    print("=" * 85)

    c1 = Claim(
        claim_id="hand-01",
        text="Brevis saves 18%",
        evidence_ids=["FIN-12"],
        kind="fact",
    )
    p1 = AdviserPosition(
        adviser_id="finance",
        stance="conditional",
        summary="Hand-made test claim 1",
        claims=[c1],
        criterion_scores=[],
        confidence=0.9,
    )

    c2 = Claim(
        claim_id="hand-02",
        text="Gross saving is ₹18 lakh a month",
        evidence_ids=["FIN-02", "FIN-03"],
        kind="calculation",
        formula="120 - 105.6",
        value=18.0,
        unit="INR lakh",
    )
    p2 = AdviserPosition(
        adviser_id="finance",
        stance="conditional",
        summary="Hand-made test claim 2",
        claims=[c2],
        criterion_scores=[],
        confidence=0.9,
    )

    c3 = Claim(
        claim_id="hand-03",
        text="Migration takes under 4 weeks",
        evidence_ids=["OPS-13"],
        kind="fact",
    )
    p3 = AdviserPosition(
        adviser_id="ops",
        stance="conditional",
        summary="Hand-made test claim 3",
        claims=[c3],
        criterion_scores=[],
        confidence=0.9,
    )

    positions = {
        "finance_test_1": p1,
        "finance_test_2": p2,
        "ops_test_3": p3,
    }

    verdicts = check_claims(positions=positions)

    claims_map = {c1.claim_id: c1, c2.claim_id: c2, c3.claim_id: c3}

    for v in verdicts:
        claim_obj = claims_map.get(v.claim_id)
        c_text = claim_obj.text if claim_obj else "unknown"
        c_eids = ", ".join(claim_obj.evidence_ids) if claim_obj else ""
        print(f"\nClaim: \"{c_text}\" (citing: {c_eids})")
        print(f"  * Status : {v.status.value}")
        print(f"  * Check  : {v.check}")
        print(f"  * Reason : {v.reason}")

    print("\n" + "=" * 85)


def main():
    root = Path(__file__).resolve().parent.parent
    runs_dir = root / "runs" / "day4"
    run_part_a(runs_dir)
    run_part_b()


if __name__ == "__main__":
    main()
