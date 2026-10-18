"""Unit tests for synthetic fact generator used in Colosseum scale tests.

Completely offline: no network calls, runs under conftest.py sandbox guard.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
import pytest

from core.schemas import Fact, Reliability
from scripts.scale_generator import generate_synthetic_facts, DOMAINS


def test_generator_deterministic_with_seed():
    """Generating facts with the exact same seed returns identical facts."""
    facts_1 = generate_synthetic_facts(count=120, seed=7)
    facts_2 = generate_synthetic_facts(count=120, seed=7)

    assert len(facts_1) == 120
    assert len(facts_2) == 120
    for f1, f2 in zip(facts_1, facts_2):
        assert f1.model_dump() == f2.model_dump()


def test_generator_different_seed_different_facts():
    """Different seeds generate different sets of facts."""
    facts_a = generate_synthetic_facts(count=60, seed=7)
    facts_b = generate_synthetic_facts(count=60, seed=42)

    statements_a = [f.statement for f in facts_a]
    statements_b = [f.statement for f in facts_b]
    assert statements_a != statements_b


@pytest.mark.parametrize("requested_count", [6, 50, 120, 600, 1200])
def test_generator_returns_exact_count(requested_count: int):
    """Generator returns the exact number of facts requested."""
    facts = generate_synthetic_facts(count=requested_count, seed=7)
    assert len(facts) == requested_count


def test_generator_valid_fact_objects():
    """All generated facts are valid Fact pydantic models with expected fields."""
    facts = generate_synthetic_facts(count=120, seed=7)
    expected_domains = {d for d, _ in DOMAINS}

    for f in facts:
        assert isinstance(f, Fact)
        assert f.fact_id.startswith("SYN-")
        assert f.domain in expected_domains
        assert isinstance(f.related_domains, list)
        assert len(f.statement) > 10
        assert len(f.entity) > 2
        assert len(f.metric) > 2
        assert isinstance(f.as_of, date)
        assert f.reliability in [Reliability.high, Reliability.medium, Reliability.low]
        assert f.source_type in ["table", "document", "system"]
        assert f.source_ref


def test_generator_unique_ids():
    """Generated fact IDs are strictly unique within the set."""
    facts = generate_synthetic_facts(count=1200, seed=7)
    ids = [f.fact_id for f in facts]
    assert len(ids) == len(set(ids)), "Found duplicate fact IDs in generated facts"


def test_generator_no_real_id_collisions():
    """Synthetic fact IDs must never collide with real fact IDs from data/facts.csv."""
    root = Path(__file__).resolve().parent.parent
    facts_csv = root / "data" / "facts.csv"

    real_ids: set[str] = set()
    if facts_csv.is_file():
        with open(facts_csv, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                real_ids.add(row["fact_id"].strip())

    assert len(real_ids) > 0, "Real facts CSV must contain fact IDs"

    facts = generate_synthetic_facts(count=1200, seed=7)
    synth_ids = {f.fact_id for f in facts}

    collision = synth_ids & real_ids
    assert not collision, f"Synthetic IDs collided with real IDs: {collision}"
    assert all(fid.startswith("SYN-") for fid in synth_ids), "All synthetic IDs must begin with SYN-"


def test_generator_covers_all_domains():
    """Synthetic facts are distributed across all 6 adviser domains."""
    facts = generate_synthetic_facts(count=1200, seed=7)
    expected_domains = {"finance", "business", "customer", "legal", "infosec", "ops"}

    domain_counts: dict[str, int] = {}
    for f in facts:
        domain_counts[f.domain] = domain_counts.get(f.domain, 0) + 1

    assert set(domain_counts.keys()) == expected_domains
    for dom, cnt in domain_counts.items():
        assert cnt == 200, f"Domain {dom} expected 200 facts, got {cnt}"


def test_generator_near_miss_distractor_ratio():
    """About 15% of synthetic facts are near-miss payment distractors."""
    facts = generate_synthetic_facts(count=1200, seed=7)
    near_misses = [f for f in facts if f.topic == "payments_near_miss"]

    ratio = len(near_misses) / len(facts)
    assert 0.13 <= ratio <= 0.17, f"Expected near-miss ratio ~15%, got {ratio:.2%}"


def test_generator_entities_are_fictional():
    """Fictional entities must not include real decision principals (Arvanta, Brevis, Legion)."""
    facts = generate_synthetic_facts(count=1200, seed=7)
    principals = {"arvanta", "brevis", "legion"}

    for f in facts:
        entity_lower = f.entity.lower()
        for p in principals:
            assert p not in entity_lower, f"Synthetic fact entity '{f.entity}' matches real principal '{p}'"
