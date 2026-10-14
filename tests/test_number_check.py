"""Unit tests for the number_matches check in the moderator (deterministic checks only)."""
import pytest

from agents.moderator import check_single_claim_deterministic
from core.schemas import Claim, ClaimStatus


def test_number_in_statement_passes():
    """Claim 'Migration will likely take about 8 weeks', kind fact, value 8, unit weeks, citing OPS-10.
    
    Passes because '8' appears in OPS-10's statement ('slip to about 8 weeks').
    """
    claim = Claim(
        claim_id="num-check-1",
        text="Migration will likely take about 8 weeks",
        kind="fact",
        value=8,
        unit="weeks",
        evidence_ids=["OPS-10"],
    )
    verdict = check_single_claim_deterministic(
        claim=claim,
        position=None,
        can_block=False,
        retrieved_doc_refs=set(),
    )
    assert verdict is None, "Claim should pass deterministic checks because 8 appears in OPS-10 statement"


def test_unit_conversion_passes():
    """Claim 'Brevis reported its 2025 incident 9 days late', kind fact, value 9, unit days, citing SEC-06.
    
    Passes because SEC-06 has 216 hours, and 216 hours = 9 days.
    """
    claim = Claim(
        claim_id="num-check-2",
        text="Brevis reported its 2025 incident 9 days late",
        kind="fact",
        value=9,
        unit="days",
        evidence_ids=["SEC-06"],
    )
    verdict = check_single_claim_deterministic(
        claim=claim,
        position=None,
        can_block=False,
        retrieved_doc_refs=set(),
    )
    assert verdict is None, "Claim should pass deterministic checks because 216 hours normalizes to 9 days"


def test_calculation_mismatch_fails():
    """Claim 'Gross saving ₹18 lakh a month', kind calculation, formula '120 - 105.6', value 18, citing FIN-02 and FIN-03.
    
    Fails with 'number mismatch' because 120 - 105.6 = 14.4 != 18.
    """
    claim = Claim(
        claim_id="num-check-3",
        text="Gross saving ₹18 lakh a month",
        kind="calculation",
        formula="120 - 105.6",
        value=18,
        evidence_ids=["FIN-02", "FIN-03"],
    )
    verdict = check_single_claim_deterministic(
        claim=claim,
        position=None,
        can_block=False,
        retrieved_doc_refs=set(),
    )
    assert verdict is not None, "Claim should fail deterministic number check"
    assert verdict.status == ClaimStatus.strike
    assert verdict.check == "number_matches"
    assert "number mismatch" in verdict.reason.lower()
