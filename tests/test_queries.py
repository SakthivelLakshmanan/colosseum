"""Cloud integration tests for named BigQuery queries."""
import pytest
from knowledge.queries import run_query


@pytest.mark.cloud
def test_success_rate_overall():
    results = run_query("success_rate_overall")
    rates = {r["gateway"]: round(float(r["success_pct"]), 1) for r in results}

    assert "Arvanta Payments" in rates
    assert "Brevis Pay" in rates
    assert rates["Arvanta Payments"] == 97.8
    assert rates["Brevis Pay"] == 97.6


@pytest.mark.cloud
def test_unknown_query_rejected():
    with pytest.raises(KeyError, match="Unknown query"):
        run_query("unknown_query_name_foo")
