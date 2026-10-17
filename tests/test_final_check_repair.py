"""Unit tests for points 1-5 of the final check repair and scorecard handling."""
import asyncio
import json
from unittest.mock import MagicMock, patch
import pytest

from agents.adviser_factory import repair_adviser_criterion_scores
from agents.moderator import check_memo
from agents.pipeline import run_decision
from agents.scorecard import build_scorecard, normalize_criterion_name
from core.schemas import (
    AdviserPosition,
    BlockingFinding,
    Claim,
    ClaimStatus,
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
    Scorecard,
    ScorecardRow,
)


# ==============================================================================
# Point 1: Adviser Completeness & Score Repair
# ==============================================================================

@pytest.mark.asyncio
async def test_adviser_completeness_missing_scores_repaired_and_logged(capsys):
    """When an adviser returns missing criterion scores for criteria it owns,
    it triggers a short repair call (no tools), merges the result, and logs:
    [adviser] ops repaired scores: Reliability.
    """
    brief = DecisionBrief(
        decision_id="DEC-TEST-01",
        question="Which system should we deploy?",
        options={"A": "Alpha System", "B": "Beta System"},
        constraints=[],
        criteria=[
            Criterion(
                name="Reliability",
                weight=1.0,
                owners=["ops"],
                good_looks_like="High uptime and resilience",
            ),
        ],
    )

    pos_ops = AdviserPosition(
        adviser_id="ops",
        stance="conditional",
        summary="Ops evaluation summary",
        claims=[
            Claim(
                claim_id="ops-1",
                text="Alpha has proven 99.99% uptime in benchmarks",
                evidence_ids=["OPS-01"],
                kind="fact",
            )
        ],
        criterion_scores=[],  # Missing scores for Reliability on A and B
        confidence=0.85,
    )

    mock_llm_repair_json = json.dumps({
        "criterion_scores": [
            {
                "criterion": "Reliability",
                "option": "A",
                "score": 4.5,
                "reason": "Proven uptime from benchmarks",
                "evidence_ids": ["OPS-01"],
            },
            {
                "criterion": "Reliability",
                "option": "B",
                "score": 2.5,
                "reason": "Limited uptime history",
                "evidence_ids": ["OPS-01"],
            },
        ]
    })

    with patch("agents.adviser_factory.generate", return_value=mock_llm_repair_json) as mock_gen:
        repaired = await repair_adviser_criterion_scores("ops", brief, pos_ops)

    # 1. Verify generate was called for repair
    assert mock_gen.called
    assert "Score these criteria for these options, using your own claims" in mock_gen.call_args[1]["contents"]

    # 2. Verify scores were merged
    assert len(repaired.criterion_scores) == 2
    scores_by_opt = {cs.option: cs.score for cs in repaired.criterion_scores}
    assert scores_by_opt["A"] == 4.5
    assert scores_by_opt["B"] == 2.5

    # 3. Verify exact log format: [adviser] ops repaired scores: Reliability
    captured = capsys.readouterr().out
    assert "[adviser] ops repaired scores: Reliability" in captured


@pytest.mark.asyncio
async def test_adviser_completeness_case_and_punctuation_insensitive():
    """Matching criterion names case-insensitively and ignoring punctuation."""
    brief = DecisionBrief(
        decision_id="DEC-TEST-02",
        question="Which architecture?",
        options={"A": "Option A", "B": "Option B"},
        constraints=[],
        criteria=[
            Criterion(
                name="Security & Compliance.",
                weight=1.0,
                owners=["infosec"],
                good_looks_like="Strict compliance",
            ),
        ],
    )

    # Adviser position already has scores for "security compliance" without punctuation/casing
    pos_infosec = AdviserPosition(
        adviser_id="infosec",
        stance="favours_A",
        summary="Infosec position",
        claims=[],
        criterion_scores=[
            CriterionScore(
                criterion="security compliance",
                option="A",
                score=4.0,
                reason="Compliant",
                evidence_ids=["SEC-01"],
            ),
            CriterionScore(
                criterion="SECURITY & COMPLIANCE",
                option="B",
                score=2.0,
                reason="Weak controls",
                evidence_ids=["SEC-02"],
            ),
        ],
        confidence=0.9,
    )

    with patch("agents.adviser_factory.generate") as mock_gen:
        repaired = await repair_adviser_criterion_scores("infosec", brief, pos_infosec)

    # No repair call needed because normalized names matched
    assert not mock_gen.called
    assert len(repaired.criterion_scores) == 2


# ==============================================================================
# Point 2: Scorecard Normalisation, None for Missing, Re-normalised Weights
# ==============================================================================

def test_scorecard_normalisation_missing_scores_and_renormalised_weights():
    """Verify that criterion names are normalized ignoring case and punctuation,
    missing criteria get None (never 0.0), missing_owners is recorded,
    has_missing_scores is set, and weighted totals are computed over scored criteria
    with weights re-normalized.
    """
    brief = DecisionBrief(
        decision_id="DEC-TEST-SC",
        question="Choose provider",
        options={"A": "Alpha", "B": "Beta"},
        constraints=[],
        criteria=[
            Criterion(
                name="Cost & 3-Yr TCO",
                weight=0.6,
                owners=["finance"],
                good_looks_like="Low TCO",
            ),
            Criterion(
                name="Reliability / Uptime.",
                weight=0.4,
                owners=["ops"],
                good_looks_like="High uptime",
            ),
        ],
    )

    # Finance scores "cost 3 yr tco" (matches normalized "Cost & 3-Yr TCO")
    pos_finance = AdviserPosition(
        adviser_id="finance",
        stance="favours_A",
        summary="Finance summary",
        claims=[],
        criterion_scores=[
            CriterionScore(
                criterion="cost  3 yr tco",
                option="A",
                score=4.0,
                reason="Cheaper",
                evidence_ids=["FIN-01"],
            ),
            CriterionScore(
                criterion="Cost 3-Yr TCO",
                option="B",
                score=2.0,
                reason="Expensive",
                evidence_ids=["FIN-02"],
            ),
        ],
        confidence=0.9,
    )

    # Ops did NOT score "Reliability / Uptime." (missing)
    pos_ops = AdviserPosition(
        adviser_id="ops",
        stance="conditional",
        summary="Ops summary",
        claims=[],
        criterion_scores=[],  # missing scores
        confidence=0.8,
    )

    scorecard = build_scorecard(brief, {"finance": pos_finance, "ops": pos_ops})

    # 1. Verify Cost row
    cost_row = next(r for r in scorecard.rows if r.criterion == "Cost & 3-Yr TCO")
    assert cost_row.scores["A"] == 4.0
    assert cost_row.scores["B"] == 2.0
    assert cost_row.missing_owners == []

    # 2. Verify Reliability row has None (never 0.0) and records missing_owners
    rel_row = next(r for r in scorecard.rows if r.criterion == "Reliability / Uptime.")
    assert rel_row.scores["A"] is None
    assert rel_row.scores["B"] is None
    assert "ops" in rel_row.missing_owners

    # 3. Verify visible flag on Scorecard
    assert scorecard.has_missing_scores is True

    # 4. Verify weighted totals re-normalized:
    # Only Cost is scored (weight 0.6). Re-normalized weight is 0.6 / 0.6 = 1.0.
    # Total for A = 1.0 * 4.0 = 4.0.
    # Total for B = 1.0 * 2.0 = 2.0.
    assert scorecard.weighted_totals["A"] == pytest.approx(4.0)
    assert scorecard.weighted_totals["B"] == pytest.approx(2.0)


# ==============================================================================
# Point 3: "Every selected adviser represented" check
# ==============================================================================

def test_adviser_represented_check_variations():
    """An adviser counts as represented if its id or display name appears anywhere
    in memo text (recommendation, conditions, phased_plan, dissent, assumptions),
    or any key number cites one of its claims or evidence IDs.
    """
    brief = DecisionBrief(
        decision_id="DEC-TEST-ADV",
        question="Choose provider",
        options={"A": "Alpha", "B": "Beta"},
        constraints=[],
        criteria=[],
    )

    base_scorecard = Scorecard(
        rows=[],
        weighted_totals={"A": 3.0, "B": 3.0},
        blocked_options={},
    )

    pos_ops = AdviserPosition(
        adviser_id="ops",
        stance="favours_A",
        summary="Ops position",
        claims=[
            Claim(claim_id="ops-claim-1", text="Ops claim text", evidence_ids=["OPS-EV-01"], kind="fact")
        ],
        criterion_scores=[],
        confidence=0.9,
    )

    # 3a. Represented in recommendation
    memo_rec = Memo(
        decision_id="DEC-TEST-ADV",
        recommendation="Adopt Option A based on Ops advice.",
        stance="A",
        confidence=0.9,
        conditions=[],
        phased_plan=[],
        scorecard=base_scorecard,
        key_numbers=[],
        blocking_findings=[],
        dissent=[],
        assumptions=[],
        numbers_to_watch=[],
        evidence_index=[],
    )
    with patch("agents.moderator.generate", return_value=json.dumps({"verdict": "PASS", "issues": []})):
        v = check_memo(brief, memo_rec, selected_advisers=["ops"], positions={"ops": pos_ops})
        assert not any(i.kind == "missing_adviser" for i in v.issues)

    # 3b. Represented in assumptions
    memo_assumptions = Memo(
        decision_id="DEC-TEST-ADV",
        recommendation="Adopt Option A.",
        stance="A",
        confidence=0.9,
        conditions=[],
        phased_plan=[],
        scorecard=base_scorecard,
        key_numbers=[],
        blocking_findings=[],
        dissent=[],
        assumptions=["Ops infrastructure capacity suffices"],
        numbers_to_watch=[],
        evidence_index=[],
    )
    with patch("agents.moderator.generate", return_value=json.dumps({"verdict": "PASS", "issues": []})):
        v = check_memo(brief, memo_assumptions, selected_advisers=["ops"], positions={"ops": pos_ops})
        assert not any(i.kind == "missing_adviser" for i in v.issues)

    # 3c. Represented in key_numbers citing evidence ID
    memo_kn_eid = Memo(
        decision_id="DEC-TEST-ADV",
        recommendation="Adopt Option A.",
        stance="A",
        confidence=0.9,
        conditions=[],
        phased_plan=[],
        scorecard=base_scorecard,
        key_numbers=[
            Claim(claim_id="kn-1", text="Latency benchmark", value=10.0, evidence_ids=["OPS-EV-01"], kind="fact")
        ],
        blocking_findings=[],
        dissent=[],
        assumptions=[],
        numbers_to_watch=[],
        evidence_index=[],
    )
    with patch("agents.moderator.generate", return_value=json.dumps({"verdict": "PASS", "issues": []})), \
         patch("agents.moderator.get_fact", return_value=None):
        v = check_memo(brief, memo_kn_eid, selected_advisers=["ops"], positions={"ops": pos_ops})
        assert not any(i.kind == "missing_adviser" for i in v.issues)

    # 3d. Absent adviser is flagged as missing_adviser
    memo_absent = Memo(
        decision_id="DEC-TEST-ADV",
        recommendation="Adopt Option A without input.",
        stance="A",
        confidence=0.9,
        conditions=[],
        phased_plan=[],
        scorecard=base_scorecard,
        key_numbers=[],
        blocking_findings=[],
        dissent=[],
        assumptions=[],
        numbers_to_watch=[],
        evidence_index=[],
    )
    with patch("agents.moderator.generate", return_value=json.dumps({"verdict": "PASS", "issues": []})):
        v = check_memo(brief, memo_absent, selected_advisers=["ops"], positions={"ops": pos_ops})
        assert any(i.kind == "missing_adviser" for i in v.issues)


# ==============================================================================
# Point 4: Memo number check
# ==============================================================================

def test_memo_number_check_brief_formula_and_evidence():
    """Accept a number if it matches cited evidence, or a formula recomputes it,
    or it appears in the decision brief (with BRIEF cited as evidence ID).
    """
    brief = DecisionBrief(
        decision_id="DEC-TEST-NUM",
        question="Should we move 40% of payment volume from Arvanta to Brevis Pay?",
        options={
            "A": "Stay with Arvanta Payments for 100% of volume",
            "B": "Move 40% of volume to Brevis Pay",
        },
        constraints=[],
        criteria=[],
    )

    base_scorecard = Scorecard(
        rows=[],
        weighted_totals={"A": 3.0, "B": 3.0},
        blocked_options={},
    )

    # 4a. Number appears in decision brief and Scribe cites BRIEF
    memo = Memo(
        decision_id="DEC-TEST-NUM",
        recommendation="Move 40% of volume to Brevis Pay (finance).",
        stance="B",
        confidence=0.9,
        conditions=[],
        phased_plan=[],
        scorecard=base_scorecard,
        key_numbers=[
            # Brief number: 40% citing BRIEF
            Claim(
                claim_id="kn-brief",
                text="40% payment volume to be migrated",
                value=40.0,
                unit="%",
                evidence_ids=["BRIEF"],
                kind="fact",
            ),
            # Formula recomputation: 120 - 105.6 = 14.4
            Claim(
                claim_id="kn-calc",
                text="Option B gross saving is 14.4 lakh",
                kind="calculation",
                formula="120 - 105.6",
                value=14.4,
                unit="lakh",
                evidence_ids=["FIN-01"],
            ),
        ],
        blocking_findings=[],
        dissent=[],
        assumptions=[],
        numbers_to_watch=[],
        evidence_index=[],
    )

    with patch("agents.moderator.generate", return_value=json.dumps({"verdict": "PASS", "issues": []})):
        v = check_memo(brief, memo, selected_advisers=["finance"], positions={})
        # Both numbers accepted, no number_mismatch
        assert not any(i.kind == "number_mismatch" for i in v.issues)

    # 4b. Contradicted number without match produces number_mismatch
    memo_bad_num = Memo(
        decision_id="DEC-TEST-NUM",
        recommendation="Move volume (finance).",
        stance="B",
        confidence=0.9,
        conditions=[],
        phased_plan=[],
        scorecard=base_scorecard,
        key_numbers=[
            Claim(
                claim_id="kn-bad-calc",
                text="Incorrect calculation",
                kind="calculation",
                formula="100 - 50",  # 50 != 999
                value=999.0,
                evidence_ids=["FIN-01"],
            ),
        ],
        blocking_findings=[],
        dissent=[],
        assumptions=[],
        numbers_to_watch=[],
        evidence_index=[],
    )
    with patch("agents.moderator.generate", return_value=json.dumps({"verdict": "PASS", "issues": []})):
        v_bad = check_memo(brief, memo_bad_num, selected_advisers=["finance"], positions={})
        assert any(i.kind == "number_mismatch" for i in v_bad.issues)


# ==============================================================================
# Point 5: Final Check Loop: upstream_gap stops revising immediately
# ==============================================================================

def test_pipeline_stops_revising_on_upstream_gap():
    """In the pipeline, if the only remaining issues are upstream_gap,
    stop revising and set the verdict to INCOMPLETE with those issues listed.
    """
    brief = DecisionBrief(
        decision_id="DEC-TEST-GAP",
        question="Which system?",
        options={"A": "Option A", "B": "Option B"},
        constraints=[],
        criteria=[],
    )

    fake_scorecard = Scorecard(
        rows=[],
        weighted_totals={"A": 3.0, "B": 3.0},
        blocked_options={},
        has_missing_scores=True,
    )

    fake_memo = Memo(
        decision_id="DEC-TEST-GAP",
        recommendation="Adopt Option A.",
        stance="A",
        confidence=0.9,
        conditions=[],
        phased_plan=[],
        scorecard=fake_scorecard,
        key_numbers=[],
        blocking_findings=[],
        dissent=[],
        assumptions=[],
        numbers_to_watch=[],
        evidence_index=[],
    )

    # Final check returns WRONG but the only issue is upstream_gap
    upstream_verdict = FinalVerdict(
        verdict=MemoVerdictType.wrong,
        issues=[
            MemoIssue(
                kind="upstream_gap",
                detail="Scorecard is missing criterion scores from ops adviser.",
                evidence_ids=[],
            )
        ],
        revision_instructions="Fix scorecard numbers",
        revision_number=0,
    )

    routing = RoutingDecision(
        decision_id="DEC-TEST-GAP",
        routing_query="operations",
        scores=[],
        selected=["ops"],
        created_at="2026-10-15T10:00:00Z",
    )

    fake_pack = EvidencePack(
        adviser_id="ops",
        facts=[],
        summaries=[],
        total_matching_facts=0,
        truncated=False,
    )

    write_memo_mock = MagicMock(return_value=fake_memo)

    with patch("agents.pipeline.route_decision", return_value=routing), \
         patch("agents.pipeline.build_pack", return_value=fake_pack), \
         patch("agents.pipeline.run_advisers_sync", return_value={}), \
         patch("agents.pipeline.check_claims", return_value=[]), \
         patch("agents.pipeline.build_scorecard", return_value=fake_scorecard), \
         patch("agents.pipeline.write_memo", write_memo_mock), \
         patch("agents.pipeline.check_memo", return_value=upstream_verdict), \
         patch("agents.pipeline.repair_adviser_criterion_scores_sync", side_effect=lambda aid, brief, p: p), \
         patch("core.ledger.create_run"), \
         patch("core.ledger.update_status"), \
         patch("core.ledger.init_adviser_statuses"), \
         patch("core.ledger.save_routing"), \
         patch("core.ledger.save_memo"), \
         patch("core.ledger.save_final_verdict"), \
         patch("core.ledger.save_memo_revisions"), \
         patch("core.ledger.save_section"):

        record = run_decision(brief=brief, run_id="run_test_gap")

    # 1. Pipeline stopped revising immediately: write_memo called only ONCE
    assert write_memo_mock.call_count == 1

    # 2. Verdict was set to INCOMPLETE
    assert record.final_verdicts[-1].verdict == MemoVerdictType.incomplete

    # 3. The upstream_gap issue is preserved
    assert len(record.final_verdicts[-1].issues) == 1
    assert record.final_verdicts[-1].issues[0].kind == "upstream_gap"
    assert "Scorecard is missing criterion scores" in record.final_verdicts[-1].issues[0].detail
