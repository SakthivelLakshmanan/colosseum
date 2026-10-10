"""Integration test for superseded facts filtering against BigQuery."""
import pytest
from knowledge.fact_store import load_facts


@pytest.mark.cloud
def test_superseded_facts_filtering():
    # 1. By default, superseded facts must never be included
    active_facts = load_facts(["finance"], include_superseded=False)
    active_ids = {f.fact_id for f in active_facts}
    assert "FIN-12" not in active_ids, "FIN-12 is superseded and must not appear in active facts"

    # 2. When include_superseded=True, superseded facts must be present
    all_facts = load_facts(["finance"], include_superseded=True)
    all_ids = {f.fact_id for f in all_facts}
    assert "FIN-12" in all_ids, "FIN-12 must appear when include_superseded=True"

    # Verify that FIN-12 has superseded_by set (to FIN-03)
    fin_12 = next(f for f in all_facts if f.fact_id == "FIN-12")
    assert fin_12.superseded_by == "FIN-03"
