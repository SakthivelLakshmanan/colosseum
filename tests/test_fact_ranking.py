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


def test_entity_name_boost_overrides_keyword_score():
    """A fact with fewer keyword matches but an entity-name hit must outrank
    a fact with more keyword matches that doesn't mention any entity."""
    # Generic fact: 3 keyword hits ("payment", "gateway", "volume"), no entity mention
    f_generic = Fact(
        fact_id="GEN-01",
        domain="ops",
        topic="payment gateway volume",
        statement="Payment gateway volume increased across all gateways this quarter.",
        entity="Industry",
        metric="volume",
        as_of=date(2026, 9, 1),
        source_type="table",
        source_ref="ops_table",
        reliability=Reliability.high,
    )
    # Entity fact: 1 keyword hit ("payment"), but mentions "Arvanta" — should win
    f_entity = Fact(
        fact_id="ENT-01",
        domain="ops",
        topic="payment uptime",
        statement="Arvanta payment uptime dropped to 97.3% in Q3 2026.",
        entity="Arvanta",
        metric="uptime_pct",
        as_of=date(2026, 9, 1),
        source_type="table",
        source_ref="health_table",
        reliability=Reliability.high,
    )

    ranked = rank_facts(
        [f_generic, f_entity],
        query_text="payment gateway volume",
        top_k=2,
        entity_names=["Arvanta", "Brevis", "Legion"],
    )
    assert ranked[0].fact_id == "ENT-01", "Entity-named fact should rank first"
    assert ranked[1].fact_id == "GEN-01"


def test_entity_boost_absent_when_no_entity_names():
    """Without entity_names the old keyword-only ordering is preserved."""
    # Generic fact: 3 keyword hits
    f_generic = Fact(
        fact_id="GEN-02",
        domain="ops",
        topic="payment gateway volume",
        statement="Payment gateway volume increased across all gateways this quarter.",
        entity="Industry",
        metric="volume",
        as_of=date(2026, 9, 1),
        source_type="table",
        source_ref="ops_table",
        reliability=Reliability.high,
    )
    # Entity fact: 1 keyword hit only
    f_entity = Fact(
        fact_id="ENT-02",
        domain="ops",
        topic="payment uptime",
        statement="Arvanta payment uptime dropped to 97.3% in Q3 2026.",
        entity="Arvanta",
        metric="uptime_pct",
        as_of=date(2026, 9, 1),
        source_type="table",
        source_ref="health_table",
        reliability=Reliability.high,
    )

    ranked = rank_facts(
        [f_entity, f_generic],
        query_text="payment gateway volume",
        top_k=2,
        # No entity_names → entity_hit is always 0 for all facts
    )
    assert ranked[0].fact_id == "GEN-02", "Without entity_names generic keyword winner should rank first"
    assert ranked[1].fact_id == "ENT-02"


def test_entity_boost_multiple_entities():
    """Facts mentioning different entities all get boosted over non-entity facts."""
    f_arvanta = Fact(
        fact_id="ARV-01",
        domain="finance",
        topic="fees",
        statement="Arvanta blended fee is 0.195%.",
        entity="Arvanta",
        metric="blended_fee",
        as_of=date(2026, 9, 1),
        source_type="document",
        source_ref="doc.pdf",
        reliability=Reliability.medium,
    )
    f_brevis = Fact(
        fact_id="BRV-01",
        domain="finance",
        topic="fees",
        statement="Brevis blended fee is 0.17%.",
        entity="Brevis",
        metric="blended_fee",
        as_of=date(2026, 9, 1),
        source_type="document",
        source_ref="doc.pdf",
        reliability=Reliability.medium,
    )
    f_background = Fact(
        fact_id="BG-01",
        domain="finance",
        topic="industry benchmarks",
        statement="Industry average blended fee is 0.21%.",
        entity="Industry",
        metric="blended_fee",
        as_of=date(2026, 9, 1),
        source_type="document",
        source_ref="benchmark.pdf",
        reliability=Reliability.high,  # Higher reliability but no entity hit
    )

    ranked = rank_facts(
        [f_background, f_brevis, f_arvanta],
        query_text="blended fee analysis",
        top_k=3,
        entity_names=["Arvanta", "Brevis", "Legion"],
    )
    fact_ids = [f.fact_id for f in ranked]
    # Both entity facts must come before the background fact
    assert fact_ids.index("BG-01") > fact_ids.index("ARV-01"), "Arvanta should outrank background"
    assert fact_ids.index("BG-01") > fact_ids.index("BRV-01"), "Brevis should outrank background"
