"""Unit tests for check_memo in agents/moderator.py."""
import json
from unittest.mock import patch
import pytest

from agents.moderator import check_memo
from core.schemas import (
    AdviserPosition,
    BlockingFinding,
    Claim,
    ClaimStatus,
    ClaimVerdict,
    Criterion,
    DecisionBrief,
    FinalVerdict,
    Memo,
    MemoIssue,
    MemoVerdictType,
    Scorecard,
    ScorecardRow,
)


@pytest.fixture
def sample_brief():
    return DecisionBrief(
        decision_id="DEC-TEST",
        question="Should we choose Option A or B?",
        options={"A": "Option A", "B": "Option B"},
        constraints=["Data localisation in India"],
        criteria=[
            Criterion(
                name="Cost",
                weight=0.5,
                owners=["finance"],
                good_looks_like="Lower cost",
            ),
            Criterion(
                name="Security & compliance",
                weight=0.5,
                owners=["infosec"],
                good_looks_like="Compliant",
            ),
        ],
    )


@pytest.fixture
def sample_scorecard():
    return Scorecard(
        rows=[
            ScorecardRow(
                criterion="Cost",
                weight=0.5,
                scores={"A": 2.0, "B": 4.0},
                evidence_ids=["FIN-01"],
            ),
            ScorecardRow(
                criterion="Security & compliance",
                weight=0.5,
                scores={"A": 4.0, "B": 1.0},
                evidence_ids=["SEC-01"],
            ),
        ],
        weighted_totals={"A": 3.0, "B": 2.5},
        blocked_options={"B": ["Brevis violates data localisation"]},
    )


@pytest.fixture
def sample_memo(sample_scorecard):
    return Memo(
        decision_id="DEC-TEST",
        recommendation="Adopt Option B conditionally upon activating India hosting add-on.",
        stance="conditional",
        confidence=0.85,
        conditions=[
            "Activate India hosting add-on to resolve data localisation (BLOCK-01)",
        ],
        phased_plan=["Phase 1: Pilot", "Phase 2: Migration"],
        scorecard=sample_scorecard,
        key_numbers=[
            Claim(
                claim_id="fin-1",
                text="Option B gross saving is 14.4 lakh",
                kind="calculation",
                formula="120 - 105.6",
                value=14.4,
                unit="lakh",
                evidence_ids=["FIN-02", "FIN-03"],
            )
        ],
        blocking_findings=[
            BlockingFinding(
                text="Brevis violates data localisation in Singapore",
                evidence_ids=["SEC-01"],
                fix="Order India hosting add-on to restrict processing to Mumbai",
            )
        ],
        dissent=["infosec: Objected to Brevis due to overseas data routing (SEC-01)"],
        assumptions=["Cost remains stable"],
        numbers_to_watch=["Monthly hosting spend"],
        evidence_index=["FIN-02", "FIN-03", "SEC-01"],
    )


def test_check_memo_cannot_be_pass_if_code_check_fails(sample_brief, sample_memo):
    # Alter memo so calculation formula mismatches value
    sample_memo.key_numbers[0].value = 999.0  # Formula is 120 - 105.6 = 14.4 != 999.0

    bf = BlockingFinding(
        text="Brevis violates data localisation in Singapore",
        evidence_ids=["SEC-01"],
        fix="Order India hosting add-on to restrict processing to Mumbai",
    )
    pos = AdviserPosition(
        adviser_id="infosec",
        stance="object",
        summary="Infosec objection",
        claims=[],
        criterion_scores=[],
        blocking_findings=[bf],
        confidence=0.9,
    )

    # Even if LLM would return PASS, code check must override to WRONG/INCOMPLETE
    mock_llm_reply = json.dumps({
        "verdict": "PASS",
        "issues": [],
        "revision_instructions": "",
    })

    with patch("agents.moderator.get_genai_client") as mock_client:
        mock_client.return_value.models.generate_content.return_value.text = mock_llm_reply
        verdict = check_memo(
            brief=sample_brief,
            memo=sample_memo,
            selected_advisers=["infosec"],
            positions={"infosec": pos},
            claim_verdicts=[],
        )

    assert verdict.verdict != MemoVerdictType.pass_, "Verdict can NEVER be PASS when code check fails"
    assert any(i.kind == "number_mismatch" for i in verdict.issues)


def test_check_memo_flags_missing_dissent_when_adviser_disagrees(sample_brief, sample_memo):
    # Empty dissent when infosec objected
    sample_memo.dissent = []

    pos_infosec = AdviserPosition(
        adviser_id="infosec",
        stance="object",
        summary="Infosec objection",
        claims=[],
        criterion_scores=[],
        blocking_findings=[],
        confidence=0.9,
    )

    mock_llm_reply = json.dumps({
        "verdict": "PASS",
        "issues": [],
        "revision_instructions": "",
    })

    with patch("agents.moderator.get_genai_client") as mock_client:
        mock_client.return_value.models.generate_content.return_value.text = mock_llm_reply
        verdict = check_memo(
            brief=sample_brief,
            memo=sample_memo,
            selected_advisers=["infosec"],
            positions={"infosec": pos_infosec},
            claim_verdicts=[],
        )

    assert verdict.verdict != MemoVerdictType.pass_
    assert any(i.kind == "missing_dissent" for i in verdict.issues)


def test_check_memo_passes_when_all_checks_succeed(sample_brief, sample_memo):
    bf = BlockingFinding(
        text="Brevis violates data localisation in Singapore",
        evidence_ids=["SEC-01"],
        fix="Order India hosting add-on to restrict processing to Mumbai",
    )
    pos_infosec = AdviserPosition(
        adviser_id="infosec",
        stance="object",
        summary="Infosec objection",
        claims=[],
        criterion_scores=[],
        blocking_findings=[bf],
        confidence=0.9,
    )

    mock_llm_reply = json.dumps({
        "verdict": "PASS",
        "issues": [],
        "revision_instructions": "",
    })

    with patch("agents.moderator.get_genai_client") as mock_client:
        mock_client.return_value.models.generate_content.return_value.text = mock_llm_reply
        verdict = check_memo(
            brief=sample_brief,
            memo=sample_memo,
            selected_advisers=["infosec"],
            positions={"infosec": pos_infosec},
            claim_verdicts=[],
        )

    assert verdict.verdict == MemoVerdictType.pass_
    assert len(verdict.issues) == 0
