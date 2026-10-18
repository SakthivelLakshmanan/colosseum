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

    with patch("agents.moderator.generate") as mock_generate:
        mock_generate.return_value = mock_llm_reply
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

    with patch("agents.moderator.generate") as mock_generate:
        mock_generate.return_value = mock_llm_reply
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

    with patch("agents.moderator.generate") as mock_generate:
        mock_generate.return_value = mock_llm_reply
        verdict = check_memo(
            brief=sample_brief,
            memo=sample_memo,
            selected_advisers=["infosec"],
            positions={"infosec": pos_infosec},
            claim_verdicts=[],
        )

    assert verdict.verdict == MemoVerdictType.pass_
    assert len(verdict.issues) == 0


def test_check_memo_dropped_condition_gives_missing_condition_and_incomplete(sample_brief, sample_memo):
    """Dropped condition gives missing_condition issue and INCOMPLETE verdict."""
    pos_legal = AdviserPosition(
        adviser_id="legal",
        stance="conditional",
        summary="Legal approval requires compliance audit",
        conditions=["Require formal third-party compliance audit before contract execution (LEG-01)"],
        claims=[],
        criterion_scores=[],
        blocking_findings=[],
        confidence=0.9,
    )
    sample_memo.conditions = ["Activate India hosting add-on to resolve data localisation (BLOCK-01)"]
    sample_memo.phased_plan = ["Phase 1: Pilot", "Phase 2: Migration"]
    sample_memo.recommendation = "Adopt Option B conditionally upon review (legal)."
    sample_memo.dissent = []

    mock_llm_reply = json.dumps({
        "verdict": "PASS",
        "issues": [],
        "revision_instructions": "",
    })

    with patch("agents.moderator.generate") as mock_generate:
        mock_generate.return_value = mock_llm_reply
        verdict = check_memo(
            brief=sample_brief,
            memo=sample_memo,
            selected_advisers=["legal"],
            positions={"legal": pos_legal},
            claim_verdicts=[],
        )

    assert verdict.verdict == MemoVerdictType.incomplete
    missing_issues = [i for i in verdict.issues if i.kind == "missing_condition"]
    assert len(missing_issues) == 1
    assert "LEG-01" in missing_issues[0].evidence_ids
    assert "Require formal third-party compliance audit" in missing_issues[0].detail
    assert "Condition from" in missing_issues[0].detail
    assert "Resolve code check failures" in verdict.revision_instructions


def test_check_memo_condition_carried_through_with_evidence_id_passes(sample_brief, sample_memo):
    """Condition carried through into the memo with its cited evidence ID passes."""
    pos_legal = AdviserPosition(
        adviser_id="legal",
        stance="conditional",
        summary="Legal approval requires compliance audit",
        conditions=["Require formal third-party compliance audit before contract execution (LEG-01)"],
        claims=[],
        criterion_scores=[],
        blocking_findings=[],
        confidence=0.9,
    )
    # Memo carries the evidence ID in conditions
    sample_memo.conditions = [
        "Complete vendor compliance verification under LEG-01 prior to rollout",
    ]
    sample_memo.phased_plan = ["Phase 1: Pilot", "Phase 2: Migration"]
    sample_memo.recommendation = "Adopt Option B conditionally upon review (legal)."
    sample_memo.dissent = []

    mock_llm_reply = json.dumps({
        "verdict": "PASS",
        "issues": [],
        "revision_instructions": "",
    })

    with patch("agents.moderator.generate") as mock_generate:
        mock_generate.return_value = mock_llm_reply
        verdict = check_memo(
            brief=sample_brief,
            memo=sample_memo,
            selected_advisers=["legal"],
            positions={"legal": pos_legal},
            claim_verdicts=[],
        )

    assert verdict.verdict == MemoVerdictType.pass_
    assert not any(i.kind == "missing_condition" for i in verdict.issues)


def test_check_memo_three_similar_adviser_conditions_merged_passes(sample_brief, sample_memo):
    """Three similar adviser conditions merged into one memo condition passes."""
    pos_legal = AdviserPosition(
        adviser_id="legal",
        stance="conditional",
        summary="Legal approval",
        conditions=["Mandate multi-factor authentication for administrative access"],
        claims=[],
        criterion_scores=[],
        blocking_findings=[],
        confidence=0.9,
    )
    pos_infosec = AdviserPosition(
        adviser_id="infosec",
        stance="conditional",
        summary="InfoSec approval",
        conditions=["Require multi-factor authentication for administrative users"],
        claims=[],
        criterion_scores=[],
        blocking_findings=[],
        confidence=0.9,
    )
    pos_ops = AdviserPosition(
        adviser_id="ops",
        stance="conditional",
        summary="Ops approval",
        conditions=["Enforce multi-factor authentication across administrative accounts"],
        claims=[],
        criterion_scores=[],
        blocking_findings=[],
        confidence=0.9,
    )

    # Memo has one single merged condition covering all 3
    sample_memo.conditions = [
        "Enforce multi-factor authentication for all administrative access across users and accounts",
    ]
    sample_memo.phased_plan = ["Phase 1: Pilot", "Phase 2: Migration"]
    sample_memo.recommendation = "Adopt Option B conditionally upon MFA rollout (legal, infosec, ops)."
    sample_memo.dissent = []

    mock_llm_reply = json.dumps({
        "verdict": "PASS",
        "issues": [],
        "revision_instructions": "",
    })

    with patch("agents.moderator.generate") as mock_generate:
        mock_generate.return_value = mock_llm_reply
        verdict = check_memo(
            brief=sample_brief,
            memo=sample_memo,
            selected_advisers=["legal", "infosec", "ops"],
            positions={"legal": pos_legal, "infosec": pos_infosec, "ops": pos_ops},
            claim_verdicts=[],
        )

    assert verdict.verdict == MemoVerdictType.pass_
    assert not any(i.kind == "missing_condition" for i in verdict.issues)


def test_check_memo_struck_blocking_finding_fix_not_required(sample_brief, sample_memo):
    """Struck blocking finding's fix is not required in the memo."""
    bf = BlockingFinding(
        text="Arvanta datacenter in EU violates RBI rules",
        evidence_ids=["REG-99"],
        fix="Migrate database cluster to Mumbai within 30 days",
    )
    pos_infosec = AdviserPosition(
        adviser_id="infosec",
        stance="conditional",
        summary="Infosec position",
        conditions=[],
        claims=[],
        criterion_scores=[],
        blocking_findings=[bf],
        confidence=0.9,
    )

    struck_verdict = ClaimVerdict(
        claim_id="infosec-B1",
        status=ClaimStatus.strike,
        check="superseded",
        reason="Regulation REG-99 has been superseded",
    )

    sample_memo.conditions = ["Activate India hosting add-on (BLOCK-01)"]
    sample_memo.phased_plan = ["Phase 1: Pilot", "Phase 2: Migration"]
    sample_memo.recommendation = "Adopt Option B (infosec)."
    sample_memo.dissent = []

    mock_llm_reply = json.dumps({
        "verdict": "PASS",
        "issues": [],
        "revision_instructions": "",
    })

    with patch("agents.moderator.generate") as mock_generate:
        mock_generate.return_value = mock_llm_reply
        verdict = check_memo(
            brief=sample_brief,
            memo=sample_memo,
            selected_advisers=["infosec"],
            positions={"infosec": pos_infosec},
            claim_verdicts=[struck_verdict],
        )

    assert verdict.verdict == MemoVerdictType.pass_
    assert not any(i.kind == "missing_condition" for i in verdict.issues)

