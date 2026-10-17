"""Colosseum end-to-end orchestration pipeline agent."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import time
from typing import Any, Optional

from agents.adviser_factory import repair_adviser_criterion_scores_sync, run_advisers_sync
from agents.moderator import check_claims, check_memo
from agents.scorecard import build_scorecard
from agents.scribe import write_memo
from core import ledger
from core.gemini import get_model_used
from core.schemas import (
    AdviserPosition,
    ClaimVerdict,
    DecisionBrief,
    EvidencePack,
    FinalVerdict,
    Memo,
    MemoIssue,
    MemoVerdictType,
    RoutingDecision,
    RunRecord,
    Scorecard,
)
from core.settings import get_settings
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
    settings = get_settings()

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
    model_used: dict[str, str] = {}
    routing: Optional[RoutingDecision] = None
    evidence_packs: list[EvidencePack] = []
    positions: dict[str, AdviserPosition] = {}
    adviser_status: dict[str, Any] = {}
    positions_live: dict[str, Any] = {}
    claim_verdicts: list[ClaimVerdict] = []
    scorecard: Optional[Scorecard] = None
    memo_versions: list[Memo] = []
    final_verdicts: list[FinalVerdict] = []
    memo_revisions: list[dict[str, Any]] = []
    current_stage = "init"
    start_total_t = time.time()

    # Create run in ledger
    try:
        ledger.create_run(run_id=run_id, brief=brief_obj)
    except Exception as exc:
        logger.warning("Failed to create run in ledger: %s", exc)

    try:
        # Step 1: Routing
        current_stage = "routing"
        try:
            ledger.update_status(run_id, "routing", "Routing decision brief to advisers")
        except Exception as exc:
            logger.warning("Failed to update status in ledger: %s", exc)

        t0 = time.time()
        routing = route_decision(brief_obj)
        metrics["routing_time_s"] = round(time.time() - t0, 3)
        router_model = get_model_used("router") or getattr(settings.models, "adviser", "gemini-3.8-flash")
        model_used["routing"] = router_model

        try:
            ledger.save_routing(run_id, routing)
            ledger.save_section(run_id, "routing", routing)
            ledger.save_section(run_id, "model_used", model_used)
        except Exception as exc:
            logger.warning("Failed to save routing in ledger: %s", exc)

        selected_advisers = routing.selected

        # Step 2: Evidence packs
        t0 = time.time()
        evidence_packs = []
        for aid in selected_advisers:
            pack = build_pack(adviser_id=aid, brief=brief_obj, routing_query=routing.routing_query)
            evidence_packs.append(pack)
        metrics["evidence_packs_time_s"] = round(time.time() - t0, 3)
        model_used["evidence_packs"] = get_model_used("aggregator") or "none"

        try:
            ledger.save_section(run_id, "evidence_packs", evidence_packs)
            ledger.save_section(run_id, "model_used", model_used)
        except Exception as exc:
            logger.warning("Failed to save evidence_packs in ledger: %s", exc)

        # Step 3: Advisers in parallel
        current_stage = "advisers"
        try:
            ledger.update_status(run_id, "advisers", f"Running {len(selected_advisers)} advisers in parallel")
            ledger.init_adviser_statuses(run_id, selected_advisers)
        except Exception as exc:
            logger.warning("Failed to update status in ledger: %s", exc)

        async def _on_adviser_done(aid: str, pos: AdviserPosition | None, err: str | None, secs: float):
            state_str = "timeout" if err == "timeout" else ("failed" if err else "done")
            now_iso = datetime.now(timezone.utc).isoformat()
            adviser_status[aid] = {
                "state": state_str,
                "started_at": None,
                "finished_at": now_iso,
                "seconds": secs,
                "error": err,
            }
            if pos is not None:
                positions_live[aid] = pos.model_dump(mode="json") if hasattr(pos, "model_dump") else pos
            await ledger.save_adviser_result(
                run_id=run_id,
                adviser_id=aid,
                position=pos,
                error=err,
                seconds=secs,
            )

        t0 = time.time()
        positions_map = run_advisers_sync(
            selected_advisers,
            brief=brief_obj,
            packs=evidence_packs,
            on_adviser_done=_on_adviser_done,
        )
        metrics["advisers_time_s"] = round(time.time() - t0, 3)
        model_used["advisers"] = getattr(settings.models, "adviser", "gemini-3.8-flash")

        positions = {}
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
            ledger.save_section(run_id, "model_used", model_used)
        except Exception as exc:
            logger.warning("Failed to save positions in ledger: %s", exc)

        # Step 4: Claim checks (moderating)
        current_stage = "moderating"
        try:
            ledger.update_status(run_id, "moderating", "Checking claims and blocking findings")
        except Exception as exc:
            logger.warning("Failed to update status in ledger: %s", exc)

        t0 = time.time()
        claim_verdicts = check_claims(positions=positions)
        metrics["moderating_time_s"] = round(time.time() - t0, 3)
        mod_model = get_model_used("moderator") or getattr(settings.models, "reasoning", "gemini-3.1-pro-preview")
        model_used["moderating"] = mod_model

        try:
            ledger.save_section(run_id, "claim_verdicts", claim_verdicts)
            ledger.save_section(run_id, "model_used", model_used)
        except Exception as exc:
            logger.warning("Failed to save claim_verdicts in ledger: %s", exc)

        # Step 5: Scorecard (scoring)
        current_stage = "scoring"
        try:
            ledger.update_status(run_id, "scoring", "Computing scorecard synthesis")
        except Exception as exc:
            logger.warning("Failed to update status in ledger: %s", exc)

        # Completeness check: ensure every adviser position covers criteria it owns before scorecard is built
        for aid in list(positions.keys()):
            pos = positions[aid]
            try:
                positions[aid] = repair_adviser_criterion_scores_sync(aid, brief_obj, pos)
            except Exception as exc:
                logger.warning("Failed repairing adviser completeness for %s: %s", aid, exc)

        t0 = time.time()
        scorecard = build_scorecard(brief=brief_obj, positions=positions, claim_verdicts=claim_verdicts)
        metrics["scoring_time_s"] = round(time.time() - t0, 3)
        model_used["scoring"] = "none"

        try:
            ledger.save_section(run_id, "scorecard", scorecard)
            ledger.save_section(run_id, "model_used", model_used)
        except Exception as exc:
            logger.warning("Failed to save scorecard in ledger: %s", exc)

        # Step 6: Scribe ⇄ final check loop (max 2 revisions)
        max_revisions = 2
        memo_versions = []
        final_verdicts = []
        memo_revisions = []
        revision_instructions = ""
        revision_number = 0
        previous_code_issues: set[tuple[str, str]] = set()

        while True:
            current_stage = "writing"
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
            scribe_model = get_model_used("scribe") or getattr(settings.models, "reasoning", "gemini-3.1-pro-preview")
            model_used["writing"] = scribe_model
            model_used["scribe"] = scribe_model

            # Scribe's memo must be saved to the run document as memo before the final check runs
            try:
                ledger.save_memo(run_id, memo)
                ledger.save_section(run_id, "memo_versions", memo_versions)
                ledger.save_section(run_id, "model_used", model_used)
            except Exception as exc:
                logger.warning("Failed to save memo in ledger: %s", exc)

            current_stage = "checking"
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
            check_model = get_model_used("final_check") or getattr(settings.models, "reasoning", "gemini-3.1-pro-preview")
            model_used["checking"] = check_model
            model_used["final_check"] = check_model

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
                ledger.save_section(run_id, "model_used", model_used)
            except Exception as exc:
                logger.warning("Failed to save final_verdict and revisions in ledger: %s", exc)

            # Code-check failure safeguard: if the exact same code-check failure appears in two drafts in a row,
            # classify it as upstream_gap, stop revising immediately, and return INCOMPLETE.
            code_check_kinds = {
                "missing_criterion",
                "missing_adviser",
                "unresolved_blocker",
                "ignored_blocker",
                "missing_dissent",
                "number_mismatch",
            }
            current_code_issues = [
                i for i in verdict.issues
                if i.kind in code_check_kinds or "code_check" in i.kind.lower()
            ]
            repeated_code_issues = [
                i for i in current_code_issues
                if (i.kind, i.detail.strip()) in previous_code_issues
            ]

            if repeated_code_issues:
                logger.info(
                    "Pipeline: exact same code-check failure appeared in two drafts in a row (%s); classifying as upstream_gap and returning INCOMPLETE.",
                    [i.detail for i in repeated_code_issues],
                )
                classified_issues = []
                for i in verdict.issues:
                    if (i.kind, i.detail.strip()) in previous_code_issues:
                        classified_issues.append(
                            MemoIssue(
                                kind="upstream_gap",
                                detail=i.detail,
                                evidence_ids=i.evidence_ids,
                            )
                        )
                    else:
                        classified_issues.append(i)

                verdict = FinalVerdict(
                    verdict=MemoVerdictType.incomplete,
                    issues=classified_issues,
                    revision_instructions="",
                    revision_number=revision_number,
                )
                final_verdicts[-1] = verdict
                memo_revisions[-1]["verdict"] = verdict
                try:
                    ledger.save_final_verdict(run_id, verdict)
                    ledger.save_section(run_id, "final_verdicts", final_verdicts)
                    ledger.save_memo_revisions(run_id, memo_revisions)
                except Exception as exc:
                    logger.warning("Failed to update final_verdict in ledger: %s", exc)
                break

            previous_code_issues = {(i.kind, i.detail.strip()) for i in current_code_issues}

            # If the only remaining issues are upstream_gap, stop revising and set verdict to INCOMPLETE
            if verdict.issues and all(getattr(i, "kind", "") == "upstream_gap" for i in verdict.issues):
                logger.info("Pipeline: only remaining issues are upstream_gap; stopping revisions and setting verdict to INCOMPLETE.")
                verdict = FinalVerdict(
                    verdict=MemoVerdictType.incomplete,
                    issues=verdict.issues,
                    revision_instructions="",
                    revision_number=revision_number,
                )
                final_verdicts[-1] = verdict
                memo_revisions[-1]["verdict"] = verdict
                try:
                    ledger.save_final_verdict(run_id, verdict)
                    ledger.save_section(run_id, "final_verdicts", final_verdicts)
                    ledger.save_memo_revisions(run_id, memo_revisions)
                except Exception as exc:
                    logger.warning("Failed to update final_verdict in ledger: %s", exc)
                break

            if verdict.verdict == MemoVerdictType.pass_:
                break

            if revision_number >= max_revisions:
                break

            revision_instructions = verdict.revision_instructions
            revision_number += 1

        # Step 7: Done
        metrics["total_time_s"] = round(time.time() - start_total_t, 3)
        final_status = "done"
        try:
            status_note = "Decision pipeline stopped: upstream_gap in scorecard" if (final_verdicts and final_verdicts[-1].verdict == MemoVerdictType.incomplete) else "Decision pipeline completed successfully"
            ledger.update_status(run_id, "done", status_note)
            ledger.save_section(run_id, "metrics", metrics)
            ledger.save_section(run_id, "model_used", model_used)
        except Exception as exc:
            logger.warning("Failed to update status in ledger: %s", exc)

        return RunRecord(
            run_id=run_id,
            brief=brief_obj,
            routing=routing if isinstance(routing, RoutingDecision) else None,
            evidence_packs=evidence_packs,
            positions=list(positions.values()),
            adviser_status=adviser_status,
            positions_live=positions_live,
            claim_verdicts=claim_verdicts,
            scorecard=scorecard,
            memo=memo_versions[-1] if memo_versions else None,
            memo_versions=memo_versions,
            final_verdict=final_verdicts[-1] if final_verdicts else None,
            final_verdicts=final_verdicts,
            memo_revisions=memo_revisions,
            metrics=metrics,
            status=final_status,
            model_used=model_used,
        )

    except Exception as exc:
        metrics["total_time_s"] = round(time.time() - start_total_t, 3)
        error_msg = f"Stage '{current_stage}' failed: {exc}"
        logger.error("Pipeline run %s failed at stage '%s': %s", run_id, current_stage, exc, exc_info=True)
        try:
            ledger.update_status(run_id, "failed", error_msg)
            ledger.save_section(run_id, "error", error_msg)
            ledger.save_section(run_id, "failed_stage", current_stage)
            ledger.save_section(run_id, "model_used", model_used)
            ledger.save_section(run_id, "metrics", metrics)
        except Exception as log_err:
            logger.warning("Failed to record failure in ledger: %s", log_err)

        return RunRecord(
            run_id=run_id,
            brief=brief_obj,
            routing=routing if isinstance(routing, RoutingDecision) else None,
            evidence_packs=evidence_packs,
            positions=list(positions.values()),
            adviser_status=adviser_status,
            positions_live=positions_live,
            claim_verdicts=claim_verdicts,
            scorecard=scorecard,
            memo=memo_versions[-1] if memo_versions else None,
            memo_versions=memo_versions,
            final_verdict=final_verdicts[-1] if final_verdicts else None,
            final_verdicts=final_verdicts,
            memo_revisions=memo_revisions,
            metrics=metrics,
            status="failed",
            error=error_msg,
            failed_stage=current_stage,
            model_used=model_used,
        )
