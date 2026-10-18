"""Unit tests for the synthetic fact generator (offline, no network)."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
import pytest

from core.registry import find_project_root
from core.schemas import Fact, Reliability
from scripts.scale_generator import DOMAINS, generate_synthetic_facts
from scripts.scale_test import generate_synthetic_facts as scale_test_gen


def test_generator_deterministic_with_seed():
    """Verify that the generator produces identical facts given the same seed."""
    assert scale_test_gen is generate_synthetic_facts

    facts_run1 = generate_synthetic_facts(n=1200, seed=7)
    facts_run2 = generate_synthetic_facts(n=1200, seed=7)

    assert len(facts_run1) == 1200
    assert len(facts_run2) == 1200

    for f1, f2 in zip(facts_run1, facts_run2):
        assert f1.model_dump() == f2.model_dump(), f"Mismatch between identical runs: {f1} vs {f2}"

    # Different seed produces different facts
    facts_diff_seed = generate_synthetic_facts(n=1200, seed=99)
    assert len(facts_diff_seed) == 1200
    differences = sum(
        1 for f1, f2 in zip(facts_run1, facts_diff_seed) if f1.statement != f2.statement
    )
    assert differences > 1000, "Different seed must produce different statements"


def test_generator_creates_valid_fact_objects():
    """Verify that every generated item is a valid Fact schema object."""
    facts = generate_synthetic_facts(n=1200, seed=7)

    for f in facts:
        assert isinstance(f, Fact)
        assert f.fact_id.startswith("SYN-")
        assert f.domain in DOMAINS
        assert isinstance(f.related_domains, list)
        assert len(f.statement) > 10
        assert f.entity
        assert f.metric
        assert isinstance(f.as_of, date)
        assert f.as_of >= date(2023, 1, 1)
        assert f.reliability in (Reliability.high, Reliability.medium, Reliability.low)
        assert f.superseded_by is None
        assert f.source_type in ("table", "document")
        assert f.source_ref


def test_generator_unique_ids():
    """Verify that all generated IDs are strictly unique."""
    facts = generate_synthetic_facts(n=1200, seed=7)
    ids = [f.fact_id for f in facts]
    assert len(ids) == len(set(ids)), f"Duplicate fact IDs detected in generated facts ({len(ids)} vs {len(set(ids))})"


def test_generator_no_collision_with_real_ids():
    """Verify that synthetic fact IDs never collide with real IDs from data/facts.csv."""
    root = find_project_root()
    csv_path = root / "data" / "facts.csv"
    assert csv_path.is_file(), "data/facts.csv must exist"

    real_ids: set[str] = set()
    with open(csv_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            real_ids.add(row["fact_id"].strip())

    assert len(real_ids) > 50, "Expected at least 50 real facts"

    synthetic_facts = generate_synthetic_facts(n=1200, seed=7)
    synthetic_ids = {f.fact_id for f in synthetic_facts}

    collision = synthetic_ids.intersection(real_ids)
    assert not collision, f"Collisions found between synthetic and real IDs: {collision}"
    assert all(fid.startswith("SYN-") for fid in synthetic_ids)


def test_generator_domain_distribution():
    """Verify that facts are evenly spread across all 6 adviser domains."""
    facts = generate_synthetic_facts(n=1200, seed=7)
    domain_counts: dict[str, int] = {}
    for f in facts:
        domain_counts[f.domain] = domain_counts.get(f.domain, 0) + 1

    assert set(domain_counts.keys()) == set(DOMAINS)
    for domain in DOMAINS:
        assert domain_counts[domain] == 200, f"Domain {domain} has {domain_counts[domain]} facts (expected 200)"

    # Test uneven division
    facts_125 = generate_synthetic_facts(n=125, seed=7)
    assert len(facts_125) == 125
    counts_125 = {d: sum(1 for f in facts_125 if f.domain == d) for d in DOMAINS}
    assert sum(counts_125.values()) == 125
    assert max(counts_125.values()) - min(counts_125.values()) <= 1


def test_generator_near_miss_distractors_and_fictional_names():
    """Verify ~15% near-miss distractors sharing payment keywords and no real entity names."""
    facts = generate_synthetic_facts(n=1200, seed=7)

    payment_keywords = ["payment", "gateway", "transaction", "settlement", "dispute", "fee", "refund", "card"]
    near_miss_count = sum(
        1 for f in facts if any(kw in f.statement.lower() for kw in payment_keywords)
    )

    ratio = near_miss_count / len(facts)
    # Expected around 15% (acceptable bounds 10% - 25%)
    assert 0.10 <= ratio <= 0.25, f"Near-miss ratio was {ratio:.2%}, expected ~15%"

    # Fictional names check: synthetic facts must NEVER reference real entities from the brief
    forbidden_names = ["Arvanta", "Brevis", "Legion Capital"]
    for f in facts:
        for name in forbidden_names:
            assert name.lower() not in f.statement.lower(), (
                f"Synthetic fact {f.fact_id} mentioned forbidden entity '{name}': {f.statement}"
            )
            assert name.lower() not in f.entity.lower(), (
                f"Synthetic fact {f.fact_id} has forbidden entity name '{name}'"
            )
