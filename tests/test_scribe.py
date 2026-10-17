"""Unit tests for agents/scribe.py."""
import json
from unittest.mock import patch
import pytest

from agents.scribe import write_memo
from core.schemas import (
    AdviserPosition,
    BlockingFinding,
    Claim,
    ClaimStatus,
    ClaimVerdict,
    Criterion,
    DecisionBrief,
    Memo,
    Scorecard,
    ScorecardRow,
)


@pytest.fixture
def sample_brief():
    return DecisionBrief(
        decision_id="DEC-TEST",
        question="Should we choose Option A or B?",
        options={"A": "Option A", "B": "Option B"},
        constraints=["Stay compliant"],
        criteria=[
            Criterion(
                name="Cost",
                weight=1.0,
                owners=["finance"],
                good_looks_like="Lower cost",
            ),
        ],
    )


@pytest.fixture
def sample_positions():
    c_kept = Claim(
        claim_id="fin-1",
        text="Option B saves 14.4 lakh",
        evidence_ids=["FIN-02", "FIN-03"],
        kind="fact",
        value=14.4,
        unit="INR lakh",
    )
    c_struck = Claim(
        claim_id="fin-2",
        text="Struck claim citing superseded fact",
        evidence_ids=["FIN-12"],
        kind="fact",
        value=18.0,
    )
    bf = BlockingFinding(
        text="Brevis violates data localisation",
        evidence_ids=["SEC-01"],
        fix="Order India hosting add-on",
    )
    pos = AdviserPosition(
        adviser_id="finance",
        stance="conditional",
        summary="Finance position",
        claims=[c_kept, c_struck],
        criterion_scores=[],
        blocking_findings=[bf],
        confidence=0.9,
    )
    return {"finance": pos}


@pytest.fixture
def sample_verdicts():
    return [
        ClaimVerdict(
            claim_id="fin-1",
            status=ClaimStatus.keep,
            check="supported",
            reason="Supported claim",
        ),
        ClaimVerdict(
            claim_id="fin-2",
            status=ClaimStatus.strike,
            check="not_superseded",
            reason="superseded by FIN-03",
        ),
    ]


@pytest.fixture
def sample_scorecard():
    return Scorecard(
        rows=[
            ScorecardRow(
                criterion="Cost",
                weight=1.0,
                scores={"A": 2.0, "B": 4.0},
                evidence_ids=["FIN-02", "FIN-03"],
            ),
        ],
        weighted_totals={"A": 2.0, "B": 4.0},
        blocked_options={"B": ["Brevis violates data localisation"]},
    )


def test_write_memo_retry_on_invalid_json_and_filter_struck_claims(
    sample_brief, sample_positions, sample_verdicts, sample_scorecard
):
    valid_memo_json = json.dumps({
        "decision_id": "DEC-TEST",
        "recommendation": "Adopt Option B conditionally upon activating India hosting add-on.",
        "stance": "conditional",
        "confidence": 0.85,
        "conditions": ["Activate India hosting add-on prior to launch"],
        "phased_plan": ["Phase 1: Pilot", "Phase 2: Migration"],
        "scorecard": sample_scorecard.model_dump(),
        "key_numbers": [
            {
                "claim_id": "fin-1",
                "text": "Option B saves 14.4 lakh",
                "evidence_ids": ["FIN-02", "FIN-03"],
                "kind": "fact",
                "value": 14.4,
                "unit": "INR lakh",
            }
        ],
        "blocking_findings": [
            {
                "text": "Brevis violates data localisation",
                "evidence_ids": ["SEC-01"],
                "fix": "Order India hosting add-on",
            }
        ],
        "dissent": ["finance: conditional due to hosting requirement (FIN-04)"],
        "assumptions": ["Hosting add-on can be delivered within 3 weeks"],
        "numbers_to_watch": ["Monthly hosting cost (FIN-04)"],
        "evidence_index": ["FIN-02", "FIN-03", "SEC-01"],
    })

    # Call returns invalid JSON on first attempt, valid on second
    call_history = []

    def mock_model(prompt: str) -> str:
        call_history.append(prompt)
        if len(call_history) == 1:
            return "```json\n{ Invalid JSON syntax... \n"
        return f"```json\n{valid_memo_json}\n```"

    with patch("agents.scribe._call_reasoning_model", side_effect=mock_model):
        memo = write_memo(
            brief=sample_brief,
            positions=sample_positions,
            claim_verdicts=sample_verdicts,
            scorecard=sample_scorecard,
        )

    # Verify that retry occurred
    assert len(call_history) == 2, "Should retry once on invalid JSON"
    assert "PREVIOUS ATTEMPT FAILED WITH VALIDATION ERROR" in call_history[1]

    # Verify that struck claim fin-2 was NOT passed into the prompt
    assert "fin-1" in call_history[0]
    assert "fin-2" not in call_history[0], "Struck claim fin-2 must be omitted"

    # Verify returned Memo object
    assert isinstance(memo, Memo)
    assert memo.decision_id == "DEC-TEST"
    assert memo.stance == "conditional"
    assert len(memo.key_numbers) == 1
    assert memo.key_numbers[0].evidence_ids == ["FIN-02", "FIN-03"]
    assert memo.scorecard.weighted_totals == {"A": 2.0, "B": 4.0}


def test_write_memo_repairs_key_numbers_missing_evidence(
    sample_brief, sample_positions, sample_verdicts, sample_scorecard
):
    # LLM omitted evidence_ids from key_numbers
    response_with_missing_eids = json.dumps({
        "decision_id": "DEC-TEST",
        "recommendation": "Adopt Option B conditionally.",
        "stance": "conditional",
        "confidence": 0.85,
        "conditions": ["Activate India hosting add-on"],
        "phased_plan": ["Phase 1"],
        "key_numbers": [
            {
                "claim_id": "fin-1",
                "text": "Option B saves 14.4 lakh",
                "evidence_ids": [],  # Omitted by LLM
                "kind": "fact",
                "value": 14.4,
                "unit": "INR lakh",
            }
        ],
        "blocking_findings": [
            {
                "text": "Brevis violates data localisation",
                "evidence_ids": ["SEC-01"],
                "fix": "Order India hosting add-on",
            }
        ],
        "dissent": [],
        "assumptions": [],
        "numbers_to_watch": [],
        "evidence_index": [],
    })

    with patch("agents.scribe._call_reasoning_model", return_value=response_with_missing_eids):
        memo = write_memo(
            brief=sample_brief,
            positions=sample_positions,
            claim_verdicts=sample_verdicts,
            scorecard=sample_scorecard,
        )

    assert len(memo.key_numbers) == 1
    assert memo.key_numbers[0].evidence_ids == ["FIN-02", "FIN-03"], "Should recover evidence_ids from accepted claim"


def test_write_memo_passes_adviser_inputs_and_prompt_rules(
    sample_brief, sample_positions, sample_verdicts, sample_scorecard
):
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    prompt_text = (root / "prompts" / "scribe.md").read_text(encoding="utf-8")

    # Verify new scribe rules in prompts/scribe.md
    assert "Name every selected adviser at least once" in prompt_text
    assert "include every specific condition from conditional or objecting advisers" in prompt_text
    assert "For every brief constraint that the evidence shows is at risk" in prompt_text
    assert "stays within the threshold" in prompt_text
    assert 'Every derived key number must be written as kind: "calculation" with its formula' in prompt_text
    assert "describe the phases as one migration with a staged traffic ramp" in prompt_text
    assert "state plainly which costs that phase triggers" in prompt_text
    assert 'Never say a cost is "avoided"' in prompt_text

    # Populate conditions and handoffs on sample position
    sample_positions["finance"].conditions = ["Activate India hosting add-on"]
    sample_positions["finance"].handoffs = ["legal: review draft agreement clause 12"]

    captured_prompts = []

    def mock_model(prompt: str) -> str:
        captured_prompts.append(prompt)
        return json.dumps({
            "decision_id": "DEC-TEST",
            "recommendation": "Adopt Option B conditionally.",
            "stance": "conditional",
            "confidence": 0.85,
            "conditions": ["Activate India hosting add-on"],
            "phased_plan": ["Phase 1"],
            "key_numbers": [
                {
                    "claim_id": "fin-1",
                    "text": "Option B saves 14.4 lakh",
                    "evidence_ids": ["FIN-02"],
                    "kind": "fact",
                    "value": 14.4,
                    "unit": "INR lakh",
                }
            ],
            "blocking_findings": [
                {
                    "text": "Brevis violates data localisation",
                    "evidence_ids": ["SEC-01"],
                    "fix": "Order India hosting add-on",
                }
            ],
            "dissent": [],
            "assumptions": [],
            "numbers_to_watch": [],
            "evidence_index": ["FIN-02", "SEC-01"],
        })

    with patch("agents.scribe._call_reasoning_model", side_effect=mock_model):
        memo = write_memo(
            brief=sample_brief,
            positions=sample_positions,
            claim_verdicts=sample_verdicts,
            scorecard=sample_scorecard,
        )

    assert len(captured_prompts) == 1
    prompt = captured_prompts[0]

    # Verify adviser inputs passed into prompt
    assert "Selected adviser positions" in prompt
    assert "conditional" in prompt
    assert "Activate India hosting add-on" in prompt
    assert "Order India hosting add-on" in prompt
    assert "legal: review draft agreement clause 12" in prompt
