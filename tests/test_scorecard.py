"""Unit tests for agents/scorecard.py."""
import json
from pathlib import Path
import pytest

from agents.scorecard import build_scorecard
from core.schemas import (
    AdviserPosition,
    BlockingFinding,
    Claim,
    ClaimStatus,
    ClaimVerdict,
    Criterion,
    CriterionScore,
    DecisionBrief,
    Scorecard,
)


def test_brief_weights_sum_to_one():
    """Verify that criteria weights in the decision brief sum to 1.0."""
    brief_file = Path(__file__).resolve().parent.parent / "evals" / "brief_DEC-001.json"
    data = json.loads(brief_file.read_text())
    brief = DecisionBrief(**data)
    total_weight = sum(c.weight for c in brief.criteria)
    assert total_weight == pytest.approx(1.0, rel=1e-5)


def test_scorecard_averages_and_totals_handmade():
    """Verify that averages and weighted totals are correct on a hand-made example,
    including ignoring criterion scores where all evidence belongs to struck claims.
    """
    brief = DecisionBrief(
        decision_id="DEC-TEST",
        question="Which system to choose?",
        options={"A": "Vendor Alpha", "B": "Vendor Beta"},
        constraints=[],
        criteria=[
            Criterion(
                name="Cost",
                weight=0.6,
                owners=["finance"],
                good_looks_like="Lower cost",
            ),
            Criterion(
                name="Quality",
                weight=0.4,
                owners=["ops", "business"],
                good_looks_like="Higher quality",
            ),
        ],
    )

    # Struck claim citing FACT-BAD
    c_struck = Claim(
        claim_id="ops-1",
        text="Ops bad claim",
        evidence_ids=["FACT-BAD"],
        kind="fact",
    )
    # Valid claim citing FACT-GOOD
    c_valid = Claim(
        claim_id="ops-2",
        text="Ops good claim",
        evidence_ids=["FACT-GOOD"],
        kind="fact",
    )

    pos_finance = AdviserPosition(
        adviser_id="finance",
        stance="favours_A",
        summary="Finance summary",
        claims=[],
        criterion_scores=[
            CriterionScore(
                criterion="Cost",
                option="A",
                score=4,
                reason="Cost A",
                evidence_ids=["FIN-01"],
            ),
            CriterionScore(
                criterion="Cost",
                option="B",
                score=2,
                reason="Cost B",
                evidence_ids=["FIN-02"],
            ),
        ],
        confidence=0.9,
    )

    pos_ops = AdviserPosition(
        adviser_id="ops",
        stance="favours_B",
        summary="Ops summary",
        claims=[c_struck, c_valid],
        criterion_scores=[
            CriterionScore(
                criterion="Quality",
                option="A",
                score=2,
                reason="Quality A from ops",
                evidence_ids=["FACT-GOOD"],
            ),
            # This score for B only cites FACT-BAD from struck claim ops-1
            CriterionScore(
                criterion="Quality",
                option="B",
                score=1,
                reason="Quality B from ops based on struck fact",
                evidence_ids=["FACT-BAD"],
            ),
        ],
        confidence=0.9,
    )

    pos_business = AdviserPosition(
        adviser_id="business",
        stance="favours_B",
        summary="Business summary",
        claims=[],
        criterion_scores=[
            CriterionScore(
                criterion="Quality",
                option="A",
                score=4,
                reason="Quality A from business",
                evidence_ids=["BUS-01"],
            ),
            CriterionScore(
                criterion="Quality",
                option="B",
                score=5,
                reason="Quality B from business",
                evidence_ids=["BUS-02"],
            ),
        ],
        confidence=0.9,
    )

    verdicts = [
        ClaimVerdict(
            claim_id="ops-1",
            status=ClaimStatus.strike,
            check="unsupported",
            reason="Struck claim",
        ),
        ClaimVerdict(
            claim_id="ops-2",
            status=ClaimStatus.keep,
            check="supported",
            reason="Kept claim",
        ),
    ]

    positions = {
        "finance": pos_finance,
        "ops": pos_ops,
        "business": pos_business,
    }

    scorecard = build_scorecard(brief, positions, claim_verdicts=verdicts)

    # Cost: only finance owns it
    # Option A: 4.0
    # Option B: 2.0
    cost_row = next(r for r in scorecard.rows if r.criterion == "Cost")
    assert cost_row.scores["A"] == 4.0
    assert cost_row.scores["B"] == 2.0

    # Quality: owned by ops and business
    # Option A: ops gave 2, business gave 4 -> avg = (2 + 4) / 2 = 3.0
    # Option B: ops gave 1 citing FACT-BAD (which belongs to struck claim ops-1) -> ignored!
    #           only business gave 5 -> avg = 5.0
    quality_row = next(r for r in scorecard.rows if r.criterion == "Quality")
    assert quality_row.scores["A"] == 3.0
    assert quality_row.scores["B"] == 5.0

    # Weighted totals:
    # Option A: 0.6 * 4.0 + 0.4 * 3.0 = 2.4 + 1.2 = 3.6
    # Option B: 0.6 * 2.0 + 0.4 * 5.0 = 1.2 + 2.0 = 3.2
    assert scorecard.weighted_totals["A"] == pytest.approx(3.6)
    assert scorecard.weighted_totals["B"] == pytest.approx(3.2)


def test_blocking_finding_in_blocked_options():
    """Verify that an unresolved blocking finding appears in blocked_options."""
    brief = DecisionBrief(
        decision_id="DEC-001",
        question="Should Legion Capital move 40% of payment volume from Arvanta to Brevis Pay?",
        options={
            "A": "Stay with Arvanta Payments for 100% of volume",
            "B": "Move 40% of volume to Brevis Pay",
        },
        constraints=["Data localisation in India"],
        criteria=[
            Criterion(
                name="Security & compliance",
                weight=1.0,
                owners=["infosec"],
                good_looks_like="Complies with regulations",
            ),
        ],
    )

    bf = BlockingFinding(
        text="Brevis Pay default configuration exports cardholder data to Singapore",
        evidence_ids=["SEC-01"],
        fix="Order India hosting add-on",
    )

    pos_infosec = AdviserPosition(
        adviser_id="infosec",
        stance="object",
        summary="Infosec objection",
        claims=[],
        criterion_scores=[
            CriterionScore(
                criterion="Security & compliance",
                option="A",
                score=4,
                reason="Arvanta ok",
                evidence_ids=["SEC-02"],
            ),
            CriterionScore(
                criterion="Security & compliance",
                option="B",
                score=1,
                reason="Brevis blocked",
                evidence_ids=["SEC-01"],
            ),
        ],
        blocking_findings=[bf],
        confidence=0.9,
    )

    # The finding is unresolved (verdict is FLAG, not STRIKE)
    verdicts = [
        ClaimVerdict(
            claim_id="infosec-B1",
            status=ClaimStatus.flag,
            check="blocking_finding",
            reason="Downgraded strike to flag",
        ),
    ]

    scorecard = build_scorecard(brief, {"infosec": pos_infosec}, claim_verdicts=verdicts)

    assert "B" in scorecard.blocked_options
    assert len(scorecard.blocked_options["B"]) == 1
    assert "Brevis Pay default configuration exports cardholder data" in scorecard.blocked_options["B"][0]
    assert "A" not in scorecard.blocked_options
