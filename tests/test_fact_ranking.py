"""Unit tests for in-memory keyword ranking of Fact objects."""
from datetime import date
from core.schemas import Fact, Reliability
from knowledge.fact_store import rank_facts


def test_fact_ranking_best_keyword_match():
    # Fact 1 has two matching keywords ("fees", "brevis")
    f1 = Fact(
        fact_id="F-01",
        domain="finance",
        topic="fees",
        statement="Brevis fees are lower than competitors.",
        entity="Brevis",
        metric="blended_fee",
        as_of=date(2026, 9, 1),
        source_type="document",
        source_ref="ref1.pdf",
        reliability=Reliability.high,
    )
    # Fact 2 has one matching keyword ("brevis")
    f2 = Fact(
        fact_id="F-02",
        domain="finance",
        topic="volume",
        statement="Brevis processes monthly loan repayments.",
        entity="Brevis",
        metric="monthly_volume",
        as_of=date(2026, 9, 1),
        source_type="document",
        source_ref="ref2.pdf",
        reliability=Reliability.high,
    )
    # Fact 3 has zero matching keywords
    f3 = Fact(
        fact_id="F-03",
        domain="ops",
        topic="uptime",
        statement="Gateway uptime was stable throughout the quarter.",
        entity="Arvanta",
        metric="uptime_pct",
        as_of=date(2026, 9, 1),
        source_type="table",
        source_ref="health",
        reliability=Reliability.high,
    )

    ranked = rank_facts([f3, f2, f1], query_text="brevis fees analysis", top_k=3)
    assert [f.fact_id for f in ranked] == ["F-01", "F-02", "F-03"]


def test_fact_ranking_tiebreak_by_reliability():
    # Same keyword match, but different reliability
    f_high = Fact(
        fact_id="REL-HIGH",
        domain="finance",
        topic="fees",
        statement="Blended fee is 0.176%",
        entity="Brevis",
        metric="fee_pct",
        as_of=date(2026, 5, 1),
        source_type="document",
        source_ref="doc.pdf",
        reliability=Reliability.high,
    )
    f_med = Fact(
        fact_id="REL-MED",
        domain="finance",
        topic="fees",
        statement="Blended fee is 0.176%",
        entity="Brevis",
        metric="fee_pct",
        as_of=date(2026, 5, 1),
        source_type="document",
        source_ref="doc.pdf",
        reliability=Reliability.medium,
    )
    f_low = Fact(
        fact_id="REL-LOW",
        domain="finance",
        topic="fees",
        statement="Blended fee is 0.176%",
        entity="Brevis",
        metric="fee_pct",
        as_of=date(2026, 5, 1),
        source_type="document",
        source_ref="doc.pdf",
        reliability=Reliability.low,
    )

    ranked = rank_facts([f_low, f_high, f_med], query_text="blended fee", top_k=3)
    assert [f.fact_id for f in ranked] == ["REL-HIGH", "REL-MED", "REL-LOW"]


def test_fact_ranking_tiebreak_by_recency():
    # Same keyword match and same reliability, but different as_of
    f_newer = Fact(
        fact_id="NEWER",
        domain="finance",
        topic="fees",
        statement="Dispute charge delta",
        entity="Brevis",
        metric="dispute_fee",
        as_of=date(2026, 9, 15),
        source_type="document",
        source_ref="doc.pdf",
        reliability=Reliability.high,
    )
    f_older = Fact(
        fact_id="OLDER",
        domain="finance",
        topic="fees",
        statement="Dispute charge delta",
        entity="Brevis",
        metric="dispute_fee",
        as_of=date(2025, 4, 1),
        source_type="document",
        source_ref="doc.pdf",
        reliability=Reliability.high,
    )

    ranked = rank_facts([f_older, f_newer], query_text="dispute charge", top_k=2)
    assert [f.fact_id for f in ranked] == ["NEWER", "OLDER"]
