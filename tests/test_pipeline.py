"""Unit tests for agents/pipeline.py."""
import json
from unittest.mock import MagicMock, patch
import pytest

from agents.pipeline import format_cumulative_revision_instructions, run_decision
from core.schemas import (
    AdviserPosition,
    AdviserScore,
    Claim,
    ClaimVerdict,
    Criterion,
    CriterionScore,
    DecisionBrief,
    EvidencePack,
    FinalVerdict,
    Memo,
    MemoIssue,
    MemoVerdictType,
    RoutingDecision,
    RunRecord,
    Scorecard,
    ScorecardRow,
)


@pytest.fixture
def mock_brief():
    return DecisionBrief(
        decision_id="DEC-TEST",
        question="Should we choose Option A or B?",
        options={"A": "Option A", "B": "Option B"},
        constraints=[],
        criteria=[
            Criterion(
                name="Cost",
                weight=1.0,
                owners=["finance"],
                good_looks_like="Lower cost",
            ),
        ],
    )


def test_run_decision_end_to_end_orchestration(mock_brief):
    routing = RoutingDecision(
        decision_id="DEC-TEST",
        routing_query="costs pricing",
        scores=[
            AdviserScore(
                adviser_id="finance",
                similarity=0.9,
                best_matching_capability="Costs",
                selected=True,
                reason="above margin",
            )
        ],
        selected=["finance"],
        created_at="2026-10-14T12:00:00Z",
    )

    pack = EvidencePack(
        adviser_id="finance",
        facts=[],
        summaries=[],
        total_matching_facts=5,
        truncated=False,
    )

    pos = AdviserPosition(
        adviser_id="finance",
        stance="conditional",
        summary="Finance summary",
        claims=[],
        criterion_scores=[
            CriterionScore(
                criterion="Cost",
                option="A",
                score=2.0,
                reason="Cost A",
                evidence_ids=["FIN-01"],
            ),
            CriterionScore(
                criterion="Cost",
                option="B",
                score=4.0,
                reason="Cost B",
                evidence_ids=["FIN-01"],
            ),
        ],
        confidence=0.9,
    )

    scorecard = Scorecard(
        rows=[
            ScorecardRow(
                criterion="Cost",
                weight=1.0,
                scores={"A": 2.0, "B": 4.0},
                evidence_ids=["FIN-01"],
            )
        ],
        weighted_totals={"A": 2.0, "B": 4.0},
        blocked_options={},
    )

    memo_draft = Memo(
        decision_id="DEC-TEST",
        recommendation="Adopt B conditionally",
        stance="conditional",
        confidence=0.85,
        conditions=["Clear blocker"],
        phased_plan=["Phase 1"],
        scorecard=scorecard,
        key_numbers=[],
        blocking_findings=[],
        dissent=[],
        assumptions=[],
        numbers_to_watch=[],
        evidence_index=[],
    )

    # First check returns INCOMPLETE, second check returns PASS
    verdict_incomplete = FinalVerdict(
        verdict=MemoVerdictType.incomplete,
        issues=[],
        revision_instructions="Add specific metric to watch",
        revision_number=0,
    )
    verdict_pass = FinalVerdict(
        verdict=MemoVerdictType.pass_,
        issues=[],
        revision_instructions="",
        revision_number=1,
    )

    check_returns = [verdict_incomplete, verdict_pass]

    with patch("agents.pipeline.route_decision", return_value=routing) as mock_route, \
         patch("agents.pipeline.build_pack", return_value=pack) as mock_pack, \
         patch("agents.pipeline.run_advisers_sync", return_value={"finance": pos}) as mock_adv, \
         patch("agents.pipeline.check_claims", return_value=[]) as mock_claims, \
         patch("agents.pipeline.build_scorecard", return_value=scorecard) as mock_sc, \
         patch("agents.pipeline.repair_adviser_criterion_scores_sync", side_effect=lambda aid, brief, p: p), \
         patch("agents.pipeline.write_memo", return_value=memo_draft) as mock_write, \
         patch("agents.pipeline.check_memo", side_effect=check_returns) as mock_check, \
         patch("core.ledger.create_run"), \
         patch("core.ledger.update_status"), \
         patch("core.ledger.init_adviser_statuses"), \
         patch("core.ledger.save_section"), \
         patch("core.ledger.save_routing"):

        run_record = run_decision(brief=mock_brief, run_id="test_run_123")

    assert isinstance(run_record, RunRecord)
    assert run_record.run_id == "test_run_123"
    assert len(run_record.memo_versions) == 2, "Should have 2 memo versions across revisions"
    assert len(run_record.final_verdicts) == 2, "Should have 2 final verdicts"
    assert run_record.final_verdicts[0].verdict == MemoVerdictType.incomplete
    assert run_record.final_verdicts[1].verdict == MemoVerdictType.pass_
    assert "routing_time_s" in run_record.metrics
    assert "advisers_time_s" in run_record.metrics
    assert "total_time_s" in run_record.metrics


def test_fake_run_saves_memo_and_final_verdict(mock_brief):
    """Prove that the Scribe's memo is saved to the run document as memo before
    the final check runs, and final_verdict and memo_revisions are saved with each verdict.
    """
    routing = RoutingDecision(
        decision_id="DEC-TEST",
        routing_query="costs pricing",
        scores=[
            AdviserScore(
                adviser_id="finance",
                similarity=0.9,
                best_matching_capability="Costs",
                selected=True,
                reason="above margin",
            )
        ],
        selected=["finance"],
        created_at="2026-10-14T12:00:00Z",
    )

    pack = EvidencePack(
        adviser_id="finance",
        facts=[],
        summaries=[],
        total_matching_facts=5,
        truncated=False,
    )

    pos = AdviserPosition(
        adviser_id="finance",
        stance="conditional",
        summary="Finance summary",
        claims=[],
        criterion_scores=[
            CriterionScore(
                criterion="Cost",
                option="A",
                score=2.0,
                reason="Cost A",
                evidence_ids=["FIN-01"],
            ),
            CriterionScore(
                criterion="Cost",
                option="B",
                score=4.0,
                reason="Cost B",
                evidence_ids=["FIN-01"],
            ),
        ],
        confidence=0.9,
    )

    scorecard = Scorecard(
        rows=[
            ScorecardRow(
                criterion="Cost",
                weight=1.0,
                scores={"A": 2.0, "B": 4.0},
                evidence_ids=["FIN-01"],
            )
        ],
        weighted_totals={"A": 2.0, "B": 4.0},
        blocked_options={},
    )

    fake_memo = Memo(
        decision_id="DEC-TEST",
        recommendation="Adopt B conditionally",
        stance="conditional",
        confidence=0.85,
        conditions=["Clear blocker"],
        phased_plan=["Phase 1"],
        scorecard=scorecard,
        key_numbers=[],
        blocking_findings=[],
        dissent=[],
        assumptions=[],
        numbers_to_watch=[],
        evidence_index=[],
    )

    fake_verdict = FinalVerdict(
        verdict=MemoVerdictType.pass_,
        issues=[],
        revision_instructions="",
        revision_number=0,
    )

    # Track sequence of events to prove memo is saved before check_memo runs
    event_sequence = []

    def tracking_save_memo(run_id, memo):
        event_sequence.append(("save_memo", run_id, memo))

    def tracking_check_memo(*args, **kwargs):
        event_sequence.append(("check_memo",))
        return fake_verdict

    def tracking_save_final_verdict(run_id, verdict):
        event_sequence.append(("save_final_verdict", run_id, verdict))

    def tracking_save_revisions(run_id, revisions):
        event_sequence.append(("save_memo_revisions", run_id, revisions))

    with patch("agents.pipeline.route_decision", return_value=routing), \
         patch("agents.pipeline.build_pack", return_value=pack), \
         patch("agents.pipeline.run_advisers_sync", return_value={"finance": pos}), \
         patch("agents.pipeline.check_claims", return_value=[]), \
         patch("agents.pipeline.build_scorecard", return_value=scorecard), \
         patch("agents.pipeline.repair_adviser_criterion_scores_sync", side_effect=lambda aid, brief, p: p), \
         patch("agents.pipeline.write_memo", return_value=fake_memo), \
         patch("agents.pipeline.check_memo", side_effect=tracking_check_memo), \
         patch("core.ledger.create_run"), \
         patch("core.ledger.update_status"), \
         patch("core.ledger.init_adviser_statuses"), \
         patch("core.ledger.save_routing"), \
         patch("core.ledger.save_memo", side_effect=tracking_save_memo) as mock_save_memo, \
         patch("core.ledger.save_final_verdict", side_effect=tracking_save_final_verdict) as mock_save_fv, \
         patch("core.ledger.save_memo_revisions", side_effect=tracking_save_revisions) as mock_save_revs, \
         patch("core.ledger.save_section"):

        run_record = run_decision(brief=mock_brief, run_id="run_fake_456")

    # 1. Proving memo was saved
    assert mock_save_memo.called
    assert mock_save_memo.call_args[0][0] == "run_fake_456"
    assert mock_save_memo.call_args[0][1] == fake_memo

    # 2. Proving final_verdict was saved
    assert mock_save_fv.called
    assert mock_save_fv.call_args[0][0] == "run_fake_456"
    assert mock_save_fv.call_args[0][1] == fake_verdict

    # 3. Proving memo was saved BEFORE the final check ran
    event_names = [e[0] for e in event_sequence]
    assert "save_memo" in event_names
    assert "check_memo" in event_names
    assert event_names.index("save_memo") < event_names.index("check_memo"), \
        "The Scribe's memo must be saved to the run document before check_memo is executed"

    # 4. Proving revisions were appended with their verdict
    assert mock_save_revs.called
    saved_revisions = mock_save_revs.call_args[0][1]
    assert len(saved_revisions) == 1
    assert saved_revisions[0]["revision"] == 0
    assert saved_revisions[0]["memo"] == fake_memo
    assert saved_revisions[0]["verdict"] == fake_verdict


def test_cumulative_revision_instructions_across_drafts(mock_brief):
    """When a draft is sent back, the revision instructions given to the Scribe
    must contain the cumulative, de-duplicated list of issues from all previous drafts:
    draft 1 has issue A, draft 2 has issue B, so the third Scribe call receives both A and B.
    """
    routing = RoutingDecision(
        decision_id="DEC-TEST",
        routing_query="costs pricing",
        scores=[
            AdviserScore(
                adviser_id="finance",
                similarity=0.9,
                best_matching_capability="Costs",
                selected=True,
                reason="above margin",
            )
        ],
        selected=["finance"],
        created_at="2026-10-14T12:00:00Z",
    )

    pack = EvidencePack(
        adviser_id="finance",
        facts=[],
        summaries=[],
        total_matching_facts=5,
        truncated=False,
    )

    pos = AdviserPosition(
        adviser_id="finance",
        stance="conditional",
        summary="Finance summary",
        claims=[],
        criterion_scores=[
            CriterionScore(
                criterion="Cost",
                option="A",
                score=2.0,
                reason="Cost A",
                evidence_ids=["FIN-01"],
            ),
            CriterionScore(
                criterion="Cost",
                option="B",
                score=4.0,
                reason="Cost B",
                evidence_ids=["FIN-01"],
            ),
        ],
        confidence=0.9,
    )

    scorecard = Scorecard(
        rows=[
            ScorecardRow(
                criterion="Cost",
                weight=1.0,
                scores={"A": 2.0, "B": 4.0},
                evidence_ids=["FIN-01"],
            )
        ],
        weighted_totals={"A": 2.0, "B": 4.0},
        blocked_options={},
    )

    fake_memo = Memo(
        decision_id="DEC-TEST",
        recommendation="Adopt B conditionally",
        stance="conditional",
        confidence=0.85,
        conditions=["Clear blocker"],
        phased_plan=["Phase 1"],
        scorecard=scorecard,
        key_numbers=[],
        blocking_findings=[],
        dissent=[],
        assumptions=[],
        numbers_to_watch=[],
        evidence_index=[],
    )

    # Issue A on Draft 1
    issue_a = MemoIssue(
        kind="number_mismatch",
        detail="Cost figure 650 crore does not match cited evidence FIN-01",
        evidence_ids=["FIN-01"],
    )
    verdict_draft_1 = FinalVerdict(
        verdict=MemoVerdictType.wrong,
        issues=[issue_a],
        revision_instructions="Fix cost figure mismatch",
        revision_number=0,
    )

    # Issue B on Draft 2
    issue_b = MemoIssue(
        kind="missing_condition",
        detail="Condition from Ops regarding rollback plan is missing",
        evidence_ids=[],
    )
    verdict_draft_2 = FinalVerdict(
        verdict=MemoVerdictType.incomplete,
        issues=[issue_b],
        revision_instructions="Add Ops condition",
        revision_number=1,
    )

    # Draft 3 passes
    verdict_draft_3 = FinalVerdict(
        verdict=MemoVerdictType.pass_,
        issues=[],
        revision_instructions="",
        revision_number=2,
    )

    write_memo_mock = MagicMock(return_value=fake_memo)
    check_memo_mock = MagicMock(side_effect=[verdict_draft_1, verdict_draft_2, verdict_draft_3])

    with patch("agents.pipeline.route_decision", return_value=routing), \
         patch("agents.pipeline.build_pack", return_value=pack), \
         patch("agents.pipeline.run_advisers_sync", return_value={"finance": pos}), \
         patch("agents.pipeline.check_claims", return_value=[]), \
         patch("agents.pipeline.build_scorecard", return_value=scorecard), \
         patch("agents.pipeline.repair_adviser_criterion_scores_sync", side_effect=lambda aid, brief, p: p), \
         patch("agents.pipeline.write_memo", write_memo_mock), \
         patch("agents.pipeline.check_memo", check_memo_mock), \
         patch("core.ledger.create_run"), \
         patch("core.ledger.update_status"), \
         patch("core.ledger.init_adviser_statuses"), \
         patch("core.ledger.save_routing"), \
         patch("core.ledger.save_memo"), \
         patch("core.ledger.save_final_verdict"), \
         patch("core.ledger.save_memo_revisions"), \
         patch("core.ledger.save_section"):

        run_record = run_decision(brief=mock_brief, run_id="run_test_cumulative")

    # 1. Proving 3 drafts occurred and 3 Scribe calls
    assert write_memo_mock.call_count == 3
    assert check_memo_mock.call_count == 3

    # Call 1: initial draft receives empty revision instructions
    call_1_instr = write_memo_mock.call_args_list[0].kwargs.get("revision_instructions", "")
    assert call_1_instr == ""

    # Call 2 (after draft 1 fails with issue A): receives Fix now with issue A
    call_2_instr = write_memo_mock.call_args_list[1].kwargs.get("revision_instructions", "")
    assert "Fix now:" in call_2_instr
    assert "number_mismatch" in call_2_instr
    assert "Cost figure 650 crore does not match cited evidence FIN-01" in call_2_instr
    assert "Still required" not in call_2_instr

    # Call 3 (after draft 2 fails with issue B): receives BOTH issue B and issue A
    call_3_instr = write_memo_mock.call_args_list[2].kwargs.get("revision_instructions", "")
    assert "Fix now:" in call_3_instr
    assert "missing_condition" in call_3_instr
    assert "Condition from Ops regarding rollback plan is missing" in call_3_instr
    assert ("Still required (fixed earlier, don’t undo):" in call_3_instr or
            "Still required (fixed earlier, don't undo):" in call_3_instr)
    assert "number_mismatch" in call_3_instr
    assert "Cost figure 650 crore does not match cited evidence FIN-01" in call_3_instr


def test_format_cumulative_revision_instructions_deduplication_and_truncation():
    """Unit test format_cumulative_revision_instructions for deduplication, truncation, and formatting."""
    # Deduplication in current issues
    dup1 = MemoIssue(kind="number_mismatch", detail="Value 100 does not match")
    dup2 = MemoIssue(kind="number_mismatch", detail="Value 100 does not match")
    res = format_cumulative_revision_instructions([dup1, dup2], [])
    assert res.count("[number_mismatch]") == 1
    assert "Fix now:" in res
    assert "Still required" not in res

    # Issue present in current is excluded from past
    past1 = MemoIssue(kind="number_mismatch", detail="Value 100 does not match")
    past2 = MemoIssue(kind="missing_dissent", detail="Dissent from legal missing")
    curr1 = MemoIssue(kind="number_mismatch", detail="Value 100 does not match")
    curr2 = MemoIssue(kind="missing_condition", detail="New condition missing")
    res2 = format_cumulative_revision_instructions([curr1, curr2], [[past1, past2]])
    assert "Fix now:" in res2
    assert "missing_condition" in res2
    assert "Still required" in res2
    assert "missing_dissent" in res2
    still_req_part = res2.split("Still required")[1]
    assert "Value 100 does not match" not in still_req_part

    # Truncation of detail to 200 chars
    long_detail = "X" * 300
    long_issue = MemoIssue(kind="long_issue", detail=long_detail)
    res3 = format_cumulative_revision_instructions([long_issue], [])
    assert "X" * 200 in res3
    assert "X" * 201 not in res3

    # Total length capped at 2500
    many_issues = [MemoIssue(kind="kind", detail=f"Issue {i} " + "Y" * 150) for i in range(30)]
    res4 = format_cumulative_revision_instructions(many_issues, [])
    assert len(res4) <= 2500

