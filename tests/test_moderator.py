"""Unit tests for moderator deterministic claim checks."""
import pytest

from agents.moderator import check_claims, check_single_claim_deterministic
from core.schemas import AdviserPosition, Claim, ClaimStatus


def test_evidence_present():
    c = Claim(claim_id="t1", text="Uncited claim", evidence_ids=[])
    v = check_single_claim_deterministic(
        claim=c,
        position=None,
        can_block=False,
        retrieved_doc_refs=set(),
    )
    assert v is not None
    assert v.status == ClaimStatus.strike
    assert v.check == "evidence_present"


def test_evidence_exists_nonexistent():
    c = Claim(claim_id="t2", text="Fake fact", evidence_ids=["NONEXISTENT-999"])
    v = check_single_claim_deterministic(
        claim=c,
        position=None,
        can_block=False,
        retrieved_doc_refs=set(),
    )
    assert v is not None
    assert v.status == ClaimStatus.strike
    assert v.check == "evidence_exists"


def test_not_superseded_strikes():
    c = Claim(claim_id="t3", text="Brevis saves 18%", evidence_ids=["FIN-12"])
    v = check_single_claim_deterministic(
        claim=c,
        position=None,
        can_block=False,
        retrieved_doc_refs=set(),
    )
    assert v is not None
    assert v.status == ClaimStatus.strike
    assert v.check == "not_superseded"
    assert "superseded by FIN-03" in v.reason


def test_number_matches_calculation_mismatch():
    c = Claim(
        claim_id="t4",
        text="Gross saving is ₹18 lakh",
        evidence_ids=["FIN-02", "FIN-03"],
        kind="calculation",
        formula="120 - 105.6",
        value=18.0,
    )
    v = check_single_claim_deterministic(
        claim=c,
        position=None,
        can_block=False,
        retrieved_doc_refs=set(),
    )
    assert v is not None
    assert v.status == ClaimStatus.strike
    assert v.check == "number_matches"


def test_number_matches_calculation_success():
    c = Claim(
        claim_id="t5",
        text="Gross saving is ₹14.4 lakh",
        evidence_ids=["FIN-02", "FIN-03"],
        kind="calculation",
        formula="120 - 105.6",
        value=14.4,
    )
    v = check_single_claim_deterministic(
        claim=c,
        position=None,
        can_block=False,
        retrieved_doc_refs=set(),
    )
    # Passes code checks -> None
    assert v is None


def test_low_reliability_flags():
    c = Claim(
        claim_id="t6",
        text="Migration takes under 4 weeks",
        evidence_ids=["OPS-13"],
        kind="fact",
    )
    v = check_single_claim_deterministic(
        claim=c,
        position=None,
        can_block=False,
        retrieved_doc_refs=set(),
    )
    assert v is not None
    assert v.status == ClaimStatus.flag
    assert v.check == "low_reliability"


def test_protect_blocking_finding_downgrades_to_flag():
    # If an adviser with can_block=True raises a blocking finding that fails a code check (e.g. calculation mismatch),
    # the STRIKE must be downgraded to FLAG and the reason preserved.
    c = Claim(
        claim_id="infosec-block-1",
        text="BLOCKING: Calculation error in finding",
        evidence_ids=["SEC-01", "SEC-02"],
        kind="calculation",
        formula="100 - 50",
        value=999.0,
    )
    pos = AdviserPosition(
        adviser_id="infosec",
        stance="conditional",
        summary="InfoSec stance",
        claims=[c],
        criterion_scores=[],
        confidence=0.9,
    )
    v = check_single_claim_deterministic(
        claim=c,
        position=pos,
        can_block=True,
        retrieved_doc_refs=set(),
    )
    assert v is not None
    assert v.status == ClaimStatus.flag, "Blocking findings from can_block advisers must be downgraded to FLAG"
    assert v.check == "number_matches"


def test_number_matches_in_statement_text():
    # BUS-11 has value_num=1200, but statement has 40,000 merchants
    c = Claim(
        claim_id="t7",
        text="Arvanta serves 40,000 merchants",
        evidence_ids=["BUS-11"],
        kind="fact",
        value=40000.0,
        unit="merchants",
    )
    v = check_single_claim_deterministic(
        claim=c,
        position=None,
        can_block=False,
        retrieved_doc_refs=set(),
    )
    assert v is None, "Should pass because 40,000 appears in BUS-11 statement text"


def test_number_matches_unit_normalization_time():
    # CUS-07 has value_num=3.0, unit="days". Claim asserts 72.0 hours.
    c = Claim(
        claim_id="t8",
        text="Refund turnaround takes 72 hours",
        evidence_ids=["CUS-07"],
        kind="fact",
        value=72.0,
        unit="hours",
    )
    v = check_single_claim_deterministic(
        claim=c,
        position=None,
        can_block=False,
        retrieved_doc_refs=set(),
    )
    assert v is None, "Should pass because 72 hours == 3 days"


def test_number_matches_unit_normalization_currency():
    # OPS-104 has value_num=1.4, unit="INR crore / month". Claim asserts 140 lakh.
    c = Claim(
        claim_id="t9",
        text="Cloud spend is 140 lakh a month",
        evidence_ids=["OPS-104"],
        kind="fact",
        value=140.0,
        unit="INR lakh / month",
    )
    v = check_single_claim_deterministic(
        claim=c,
        position=None,
        can_block=False,
        retrieved_doc_refs=set(),
    )
    assert v is None, "Should pass because 140 lakh == 1.4 crore"


def test_number_matches_no_comparable_skips():
    # SEC-02 is a policy fact with no numbers. Claim asserts 99.9%.
    # Should skip rather than striking.
    c = Claim(
        claim_id="t10",
        text="Compliance score is 99.9%",
        evidence_ids=["SEC-02"],
        kind="fact",
        value=99.9,
        unit="%",
    )
    v = check_single_claim_deterministic(
        claim=c,
        position=None,
        can_block=False,
        retrieved_doc_refs=set(),
    )
    assert v is None, "Should skip check rather than striking when no cited fact has comparable number"


def test_related_facts_strictly_same_entity_and_metric():
    from agents.moderator import _build_related_facts_text
    from core.schemas import Fact, Reliability
    from datetime import date

    f1 = Fact(
        fact_id="TEST-01",
        domain="finance",
        topic="test",
        statement="Entity X has metric Y of 10",
        entity="Entity X",
        metric="metric_y",
        source_type="table",
        source_ref="test",
        as_of=date(2026, 1, 1),
    )
    f_same_both = Fact(
        fact_id="TEST-02",
        domain="finance",
        topic="test",
        statement="Entity X has updated metric Y of 12",
        entity="Entity X",
        metric="metric_y",
        source_type="table",
        source_ref="test",
        as_of=date(2026, 2, 1),
    )
    f_same_entity_diff_metric = Fact(
        fact_id="TEST-03",
        domain="finance",
        topic="test",
        statement="Entity X has metric Z of 50",
        entity="Entity X",
        metric="metric_z",
        source_type="table",
        source_ref="test",
        as_of=date(2026, 1, 1),
    )
    f_diff_entity_same_metric = Fact(
        fact_id="TEST-04",
        domain="finance",
        topic="test",
        statement="Entity W has metric Y of 10",
        entity="Entity W",
        metric="metric_y",
        source_type="table",
        source_ref="test",
        as_of=date(2026, 1, 1),
    )

    all_facts = [f1, f_same_both, f_same_entity_diff_metric, f_diff_entity_same_metric]
    claim = Claim(claim_id="c1", text="Test claim", evidence_ids=["TEST-01"])

    # Mock get_fact for TEST-01
    from knowledge import fact_store
    fact_store._fact_cache["TEST-01"] = f1

    result = _build_related_facts_text(claim, all_facts)
    assert "TEST-02" in result, "Fact with same entity AND metric must be included"
    assert "TEST-03" not in result, "Fact with same entity but different metric must NOT be included"
    assert "TEST-04" not in result, "Fact with different entity must NOT be included"


def test_check_claims_blocking_finding_can_block_true():
    from core.schemas import BlockingFinding

    bf = BlockingFinding(
        text="Critical infosec finding",
        evidence_ids=["NONEXISTENT-999"],
        fix="Ensure encryption at rest is enabled",
    )
    pos = AdviserPosition(
        adviser_id="infosec",
        stance="object",
        summary="InfoSec objection",
        claims=[],
        criterion_scores=[],
        confidence=0.9,
        blocking_findings=[bf],
    )
    verdicts = check_claims({"infosec": pos})
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v.claim_id == "infosec-B1"
    assert v.status == ClaimStatus.flag, "Should downgrade STRIKE to FLAG for can_block=True"
    assert v.check == "evidence_exists"


def test_check_claims_blocking_finding_can_block_false():
    from core.schemas import BlockingFinding

    bf = BlockingFinding(
        text="Finance blocking finding without block authority",
        evidence_ids=["NONEXISTENT-999"],
        fix="Review financial assumptions",
    )
    pos = AdviserPosition(
        adviser_id="finance",
        stance="object",
        summary="Finance objection",
        claims=[],
        criterion_scores=[],
        confidence=0.9,
        blocking_findings=[bf],
    )
    verdicts = check_claims({"finance": pos})
    assert len(verdicts) == 1
    v = verdicts[0]
    assert v.claim_id == "finance-B1"
    assert v.status == ClaimStatus.strike, "Should remain STRIKE for can_block=False adviser"
    assert v.check == "evidence_exists"


