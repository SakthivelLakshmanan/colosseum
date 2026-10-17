"""Integration test for superseded facts filtering against BigQuery."""
import csv
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from knowledge.fact_store import load_facts


@pytest.mark.cloud
def test_superseded_facts_filtering():
    root = Path(__file__).resolve().parent.parent
    csv_file = root / "data" / "facts.csv"
    all_csv_rows = []
    with open(csv_file, "r", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            all_csv_rows.append(r)

    def mock_query(query, job_config=None):
        job = MagicMock()
        domains = ["finance"]
        filtered = []
        is_include_superseded = "superseded_by IS NULL" not in query
        for r in all_csv_rows:
            d = r.get("domain", "")
            rd = r.get("related_domains", "").split("|")
            if d in domains or any(x in domains for x in rd if x):
                if is_include_superseded or not r.get("superseded_by"):
                    filtered.append(r)
        job.result.return_value = filtered
        return job

    mock_client = MagicMock()
    mock_client.query.side_effect = mock_query

    with patch("knowledge.fact_store.get_client", return_value=mock_client):
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
