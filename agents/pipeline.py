"""Colosseum end-to-end orchestration pipeline agent."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time
from typing import Any, Optional

from agents.adviser_factory import run_advisers_sync
from agents.moderator import check_claims, check_memo
from agents.scorecard import build_scorecard
from agents.scribe import write_memo
from core import ledger
from core.schemas import (
    AdviserPosition,
    DecisionBrief,
    EvidencePack,
    FinalVerdict,
    Memo,
    MemoVerdictType,
    RoutingDecision,
    RunRecord,
)
from harness.context_builder import build_pack
from harness.router import route_decision

logger = logging.getLogger(__name__)


def run_decision(
    brief: DecisionBrief | dict[str, Any] | str | Path,
    run_id: Optional[str] = None,
) -> RunRecord:
    """Coordinate the end-to-end Colosseum decision flow.
    
    1. Create run in ledger.
    2. Route decision to select advisers.
    3. Build evidence packs per selected adviser.
    4. Run selected advisers in parallel.
    5. Moderator claim checks (deterministic + LLM support check).
    6. Scorecard synthesis.
    7. Scribe memo generation ⇄ Moderator final check loop (max 2 revisions).
    8. Record step timings in metrics and save all sections to ledger.
    """
    # Normalize brief
    if isinstance(brief, (str, Path)):
        brief_path = Path(brief)
        if not brief_path.is_file():
            raise FileNotFoundError(f"Decision brief file not found: {brief_path}")
        brief_obj = DecisionBrief.model_validate(json.loads(brief_path.read_text(encoding="utf-8")))
    elif isinstance(brief, dict):
        brief_obj = DecisionBrief.model_validate(brief)
    else:
        brief_obj = brief

    if not run_id:
        now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        run_id = f"run_{brief_obj.decision_id}_{now_str}"

    metrics: dict[str, float] = {}
    start_total_t = time.time()

    # Create run in ledger
    try:
        ledger.create_run(run_id=run_id, brief=brief_obj)
    except Exception as exc:
        logger.warning("Failed to create run in ledger: %s", exc)

    try:
        # Step 1: Routing
        try:
            ledger.update_status(run_id, "routing", "Routing decision brief to advisers")
        except Exception as exc:
            logger.warning("Failed to update status in ledger: %s", exc)

        t0 = time.time()
        routing = route_decision(brief_obj)
        metrics["routing_time_s"] = round(time.time() - t0, 3)

        try:
            ledger.save_routing(run_id, routing)
            ledger.save_section(run_id, "routing", routing)
        except Exception as exc:
            logger.warning("Failed to save routing in ledger: %s", exc)

        selected_advisers = routing.selected

        # Step 2: Evidence packs
        t0 = time.time()
        evidence_packs: list[EvidencePack] = []
        for aid in selected_advisers:
            pack = build_pack(adviser_id=aid, brief=brief_obj, routing_query=routing.routing_query)
            evidence_packs.append(pack)
        metrics["evidence_packs_time_s"] = round(time.time() - t0, 3)

        try:
            ledger.save_section(run_id, "evidence_packs", evidence_packs)
        except Exception as exc:
            logger.warning("Failed to save evidence_packs in ledger: %s", exc)

        # Step 3: Advisers in parallel
        try:
            ledger.update_status(run_id, "advisers", f"Running {len(selected_advisers)} advisers in parallel")
        except Exception as exc:
            logger.warning("Failed to update status in ledger: %s", exc)

        t0 = time.time()
        positions_map = run_advisers_sync(selected_advisers, brief=brief_obj, packs=evidence_packs)
        metrics["advisers_time_s"] = round(time.time() - t0, 3)

        positions: dict[str, AdviserPosition] = {}
        for aid in selected_advisers:
            pos = positions_map.get(aid)
            if isinstance(pos, AdviserPosition):
                positions[aid] = pos
            elif isinstance(pos, dict):
                positions[aid] = AdviserPosition(**pos)
            else:
                logger.warning("Adviser '%s' returned error or non-position: %s", aid, pos)

        try:
            ledger.save_section(run_id, "positions", list(positions.values()))
        except Exception as exc:
            logger.warning("Failed to save positions in ledger: %s", exc)

        # Step 4: Claim checks (moderating)
        try:
            ledger.update_status(run_id, "moderating", "Checking claims and blocking findings")
        except Exception as exc:
            logger.warning("Failed to update status in ledger: %s", exc)

        t0 = time.time()
        claim_verdicts = check_claims(positions=positions)
        metrics["moderating_time_s"] = round(time.time() - t0, 3)

        try:
            ledger.save_section(run_id, "claim_verdicts", claim_verdicts)
        except Exception as exc:
            logger.warning("Failed to save claim_verdicts in ledger: %s", exc)

        # Step 5: Scorecard (scoring)
        try:
            ledger.update_status(run_id, "scoring", "Computing scorecard synthesis")
        except Exception as exc:
            logger.warning("Failed to update status in ledger: %s", exc)

        t0 = time.time()
        scorecard = build_scorecard(brief=brief_obj, positions=positions, claim_verdicts=claim_verdicts)
        metrics["scoring_time_s"] = round(time.time() - t0, 3)

        try:
            ledger.save_section(run_id, "scorecard", scorecard)
        except Exception as exc:
            logger.warning("Failed to save scorecard in ledger: %s", exc)

        # Step 6: Scribe ⇄ final check loop (max 2 revisions)
        max_revisions = 2
        memo_versions: list[Memo] = []
        final_verdicts: list[FinalVerdict] = []
        memo_revisions: list[dict[str, Any]] = []
        revision_instructions = ""
        revision_number = 0

        while True:
            try:
                ledger.update_status(run_id, "writing", f"Drafting memo (revision {revision_number})")
            except Exception as exc:
                logger.warning("Failed to update status in ledger: %s", exc)

            t0 = time.time()
            memo = write_memo(
                brief=brief_obj,
                positions=positions,
                claim_verdicts=claim_verdicts,
                scorecard=scorecard,
                revision_instructions=revision_instructions,
            )
            metrics[f"writing_rev_{revision_number}_time_s"] = round(time.time() - t0, 3)
            memo_versions.append(memo)

            # Scribe's memo must be saved to the run document as memo before the final check runs
            try:
                ledger.save_memo(run_id, memo)
                ledger.save_section(run_id, "memo_versions", memo_versions)
            except Exception as exc:
                logger.warning("Failed to save memo in ledger: %s", exc)

            try:
                ledger.update_status(run_id, "checking", f"Checking memo (revision {revision_number})")
            except Exception as exc:
                logger.warning("Failed to update status in ledger: %s", exc)

            t0 = time.time()
            verdict = check_memo(
                brief=brief_obj,
                memo=memo,
                selected_advisers=selected_advisers,
                positions=positions,
                claim_verdicts=claim_verdicts,
                revision_number=revision_number,
            )
            metrics[f"checking_rev_{revision_number}_time_s"] = round(time.time() - t0, 3)
            final_verdicts.append(verdict)

            # Each revision appended to memo_revisions with its verdict
            revision_entry = {
                "revision": revision_number,
                "memo": memo,
                "verdict": verdict,
            }
            memo_revisions.append(revision_entry)

            try:
                ledger.save_final_verdict(run_id, verdict)
                ledger.save_section(run_id, "final_verdicts", final_verdicts)
                ledger.save_memo_revisions(run_id, memo_revisions)
            except Exception as exc:
                logger.warning("Failed to save final_verdict and revisions in ledger: %s", exc)

            if verdict.verdict == MemoVerdictType.pass_:
                break

            if revision_number >= max_revisions:
                break

            revision_instructions = verdict.revision_instructions
            revision_number += 1

        # Step 7: Done
        metrics["total_time_s"] = round(time.time() - start_total_t, 3)
        try:
            ledger.update_status(run_id, "done", "Decision pipeline completed successfully")
            ledger.save_section(run_id, "metrics", metrics)
        except Exception as exc:
            logger.warning("Failed to update status in ledger: %s", exc)

        return RunRecord(
            run_id=run_id,
            brief=brief_obj,
            routing=routing,
            evidence_packs=evidence_packs,
            positions=list(positions.values()),
            claim_verdicts=claim_verdicts,
            memo_versions=memo_versions,
            final_verdicts=final_verdicts,
            metrics=metrics,
        )

    except Exception as exc:
        metrics["total_time_s"] = round(time.time() - start_total_t, 3)
        try:
            ledger.update_status(run_id, "failed", f"Failed: {exc}")
            ledger.save_section(run_id, "error", str(exc))
            ledger.save_section(run_id, "metrics", metrics)
        except Exception as log_err:
            logger.warning("Failed to record failure in ledger: %s", log_err)
        raise
