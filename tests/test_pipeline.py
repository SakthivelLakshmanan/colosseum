"""Unit tests for agents/pipeline.py."""
import json
from unittest.mock import MagicMock, patch
import pytest

from agents.pipeline import run_decision
from core.schemas import (
    AdviserPosition,
    AdviserScore,
    Claim,
    ClaimVerdict,
    Criterion,
    DecisionBrief,
    EvidencePack,
    FinalVerdict,
    Memo,
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
        criterion_scores=[],
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
         patch("agents.pipeline.write_memo", return_value=memo_draft) as mock_write, \
         patch("agents.pipeline.check_memo", side_effect=check_returns) as mock_check, \
         patch("core.ledger.create_run"), \
         patch("core.ledger.update_status"), \
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
