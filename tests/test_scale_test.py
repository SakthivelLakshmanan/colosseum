"""Unit tests for conclusion generation and scale test reporting in scripts/scale_test.py."""
import pytest
from scripts.scale_test import generate_conclusion


def test_generate_conclusion_states_all_required_metrics():
    scale_summary = {
        "facts_in_store": 1325,
        "retention_pct": 71.3,
        "noise_admitted": 43,
        "latency_p50_s": 132.749,
        "latency_p95_s": 1546.747,
    }
    conclusion = generate_conclusion(scale_summary, pack_cap=25)

    # 1. State retention %
    assert "71.3%" in conclusion
    assert "retention" in conclusion.lower()

    # 2. State noise admitted
    assert "43" in conclusion
    assert "noise admitted" in conclusion.lower()

    # 3. State pack cap
    assert "25" in conclusion
    assert "pack cap" in conclusion.lower()

    # 4. State p50/p95 build time in seconds
    assert "132.749s" in conclusion
    assert "1546.747s" in conclusion
    assert "p50" in conclusion.lower()
    assert "p95" in conclusion.lower()
    assert "build time" in conclusion.lower()

    # 5. No adjectives like "successfully" or "sub-second"
    assert "successfully" not in conclusion.lower()
    assert "sub-second" not in conclusion.lower()

    # 6. Explains that latency includes first-time summary generation
    assert "first-time summary generation" in conclusion.lower()


def test_generate_conclusion_updates_with_different_numbers():
    """Verify conclusion is generated from measured numbers, not a fixed sentence."""
    scale_summary_1 = {
        "facts_in_store": 1200,
        "retention_pct": 85.0,
        "noise_admitted": 12,
        "latency_p50_s": 1.450,
        "latency_p95_s": 3.200,
    }
    scale_summary_2 = {
        "facts_in_store": 2400,
        "retention_pct": 55.5,
        "noise_admitted": 98,
        "latency_p50_s": 45.123,
        "latency_p95_s": 120.456,
    }
    c1 = generate_conclusion(scale_summary_1, pack_cap=20)
    c2 = generate_conclusion(scale_summary_2, pack_cap=30)

    assert "85.0%" in c1
    assert "12" in c1
    assert "20" in c1
    assert "1.450s" in c1
    assert "3.200s" in c1

    assert "55.5%" in c2
    assert "98" in c2
    assert "30" in c2
    assert "45.123s" in c2
    assert "120.456s" in c2

    assert c1 != c2
