"""Unit tests for FastAPI endpoints in api/main.py."""
import json
from datetime import date
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from api.main import app
from core.schemas import DecisionBrief, Fact, Reliability

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_advisers_endpoint():
    response = client.get("/advisers")
    assert response.status_code == 200
    advisers = response.json()
    assert isinstance(advisers, list)
    assert len(advisers) >= 6
    for adv in advisers:
        assert "id" in adv
        assert "name" in adv
        assert "criteria" in adv
        assert "can_block" in adv
        assert isinstance(adv["criteria"], list)
        assert isinstance(adv["can_block"], bool)


def test_briefs_sample_endpoint():
    response = client.get("/briefs/sample")
    assert response.status_code == 200
    data = response.json()
    assert data["decision_id"] == "DEC-001"
    assert "Arvanta" in data["question"]
    assert "Brevis" in data["question"]
    assert "options" in data
    assert "criteria" in data


def test_create_decision_run_and_background_dispatch():
    brief_data = {
        "decision_id": "DEC-TEST-API",
        "question": "Should we migrate systems?",
        "options": {"A": "Keep existing", "B": "Migrate"},
        "constraints": ["India localisation"],
        "criteria": [
            {
                "name": "Cost",
                "weight": 1.0,
                "owners": ["finance"],
                "good_looks_like": "Lower cost",
            }
        ],
    }

    with patch("api.main.create_run") as mock_create_run, \
         patch("api.main.start_background_run") as mock_start_bg:
        response = client.post("/decisions", json=brief_data)
        assert response.status_code in (200, 202)
        result = response.json()
        assert "run_id" in result
        run_id = result["run_id"]
        assert run_id.startswith("run_DEC-TEST-API_")
        mock_create_run.assert_called_once()
        assert mock_create_run.call_args[1].get("initial_status") == "queued"
        mock_start_bg.assert_called_once()
        called_run_id = (
            mock_start_bg.call_args[0][0]
            if mock_start_bg.call_args[0]
            else mock_start_bg.call_args[1].get("run_id")
        )
        assert called_run_id == run_id


def test_get_run_details_not_found():
    with patch("core.ledger.get_run", return_value={}):
        response = client.get("/runs/nonexistent_run_12345")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


def test_get_run_details_success():
    fake_run = {
        "run_id": "run_test_123",
        "status": "done",
        "routing": {"selected": ["finance", "ops"]},
        "positions": [],
        "claim_verdicts": [],
        "scorecard": {"rows": [], "weighted_totals": {}},
        "memo_versions": [{"recommendation": "Adopt Option B"}],
        "final_verdicts": [{"verdict": "PASS"}],
    }
    with patch("core.ledger.get_run", return_value=fake_run):
        response = client.get("/runs/run_test_123")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run_test_123"
        assert data["status"] == "done"
        assert data["memo"]["recommendation"] == "Adopt Option B"
        assert data["final_verdict"]["verdict"] == "PASS"


def test_get_run_details_guaranteed_shape_and_fallback_mapping():
    expected_keys = {
        "run_id",
        "status",
        "created_at",
        "brief",
        "routing",
        "positions",
        "adviser_status",
        "positions_live",
        "claim_verdicts",
        "scorecard",
        "memo",
        "memo_revisions",
        "final_verdict",
        "human_decisions",
        "error",
    }

    # Case 1: Fresh queued run with no sections produced yet
    queued_run = {
        "run_id": "run_queued_1",
        "status": "queued",
        "created_at": "2026-10-14T20:00:00Z",
    }
    with patch("core.ledger.get_run", return_value=queued_run):
        response = client.get("/runs/run_queued_1")
        assert response.status_code == 200
        data = response.json()
        assert set(data.keys()) == expected_keys
        assert data["run_id"] == "run_queued_1"
        assert data["status"] == "queued"
        assert data["created_at"] == "2026-10-14T20:00:00Z"
        assert data["brief"] is None
        assert data["routing"] is None
        assert data["positions"] == []
        assert data["adviser_status"] == {}
        assert data["positions_live"] == {}
        assert data["claim_verdicts"] == []
        assert data["scorecard"] is None
        assert data["memo"] is None
        assert data["memo_revisions"] == []
        assert data["final_verdict"] is None
        assert data["human_decisions"] == []
        assert data["error"] is None

    # Case 2: Run like run_DEC-001_20261014_101635 with memo_versions and final_verdicts
    legacy_run = {
        "run_id": "run_DEC-001_20261014_101635",
        "status": "done",
        "created_at": "2026-10-14T10:16:35Z",
        "memo_versions": [
            {"recommendation": "Phase 1 move 15%", "stance": "conditional"}
        ],
        "final_verdicts": [
            {"verdict": "PASS", "revision_number": 0}
        ],
        "human_decision": {
            "action": "approve",
            "reason": "OK",
            "decided_by": "CRO",
        },
    }
    with patch("core.ledger.get_run", return_value=legacy_run):
        response = client.get("/runs/run_DEC-001_20261014_101635")
        assert response.status_code == 200
        data = response.json()
        assert set(data.keys()) == expected_keys
        assert data["memo"] == {"recommendation": "Phase 1 move 15%", "stance": "conditional"}
        assert data["final_verdict"] == {"verdict": "PASS", "revision_number": 0}
        assert len(data["memo_revisions"]) == 1
        assert data["memo_revisions"][0]["revision"] == 0
        assert data["memo_revisions"][0]["memo"] == {"recommendation": "Phase 1 move 15%", "stance": "conditional"}
        assert data["memo_revisions"][0]["verdict"] == {"verdict": "PASS", "revision_number": 0}
        assert data["human_decisions"] == [
            {"action": "approve", "reason": "OK", "decided_by": "CRO"}
        ]


def test_list_recent_runs():
    fake_doc_1 = MagicMock()
    fake_doc_1.id = "run_1"
    fake_doc_1.to_dict.return_value = {
        "run_id": "run_1",
        "status": "done",
        "created_at": "2026-10-14T10:00:00Z",
        "final_verdicts": [{"verdict": "PASS"}],
    }

    fake_doc_2 = MagicMock()
    fake_doc_2.id = "run_2"
    fake_doc_2.to_dict.return_value = {
        "run_id": "run_2",
        "status": "routing",
        "created_at": "2026-10-14T09:00:00Z",
    }

    mock_db = MagicMock()
    mock_db.collection.return_value.order_by.return_value.limit.return_value.stream.return_value = [
        fake_doc_1,
        fake_doc_2,
    ]

    with patch("core.ledger.get_db", return_value=mock_db):
        response = client.get("/runs?limit=2")
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 2
        assert items[0]["run_id"] == "run_1"
        assert items[0]["status"] == "done"
        assert items[0]["final_verdict"] == "PASS"
        assert items[1]["run_id"] == "run_2"
        assert items[1]["status"] == "routing"
        assert items[1]["final_verdict"] is None


def test_human_decision_approve():
    fake_run = {"run_id": "run_123", "human_decisions": []}
    stored_decision_docs = {}

    def mock_save_decision(doc_id, decision_data, **kwargs):
        stored_decision_docs[doc_id] = decision_data
        return doc_id

    def mock_save_human_decision_to_run(run_id, data):
        fake_run["human_decision"] = data
        fake_run.setdefault("human_decisions", []).append(data)

    with patch("core.ledger.get_run", return_value=fake_run), \
         patch("core.ledger.save_decision", side_effect=mock_save_decision) as mock_save_dec, \
         patch("core.ledger.save_section") as mock_save_sec, \
         patch("core.ledger.save_human_decision_to_run", side_effect=mock_save_human_decision_to_run):

        # Post first decision (approve)
        payload_1 = {
            "action": "approve",
            "reason": "Risk acceptable",
            "decided_by": "Chief Risk Officer",
        }
        res_1 = client.post("/runs/run_123/decision", json=payload_1)
        assert res_1.status_code == 200
        data_1 = res_1.json()
        assert data_1["status"] == "ok"
        assert data_1["decision"]["action"] == "approve"
        assert data_1["decision"]["decided_by"] == "Chief Risk Officer"

        # Post second decision for the same run (override)
        payload_2 = {
            "action": "override",
            "reason": "Executive board override with updated terms",
            "decided_by": "CEO",
        }
        res_2 = client.post("/runs/run_123/decision", json=payload_2)
        assert res_2.status_code == 200
        data_2 = res_2.json()
        assert data_2["status"] == "ok"
        assert data_2["decision"]["action"] == "override"
        assert data_2["decision"]["decided_by"] == "CEO"

        # Check that both documents exist in human_decisions and neither was overwritten
        assert len(stored_decision_docs) == 2, "Both decision documents must exist in human_decisions"
        doc_ids = list(stored_decision_docs.keys())
        assert doc_ids[0] != doc_ids[1], "Each decision must have a distinct doc_id"
        for did in doc_ids:
            assert did.startswith("run_123_"), f"Doc id '{did}' must match {{run_id}}_{{timestamp}}"

        assert stored_decision_docs[doc_ids[0]]["action"] == "approve"
        assert stored_decision_docs[doc_ids[1]]["action"] == "override"

        # Check run document: human_decisions list has both appended, human_decision has latest
        assert len(fake_run["human_decisions"]) == 2
        assert fake_run["human_decisions"][0]["action"] == "approve"
        assert fake_run["human_decisions"][1]["action"] == "override"
        assert fake_run["human_decision"]["action"] == "override"
        assert fake_run["human_decision"]["decided_by"] == "CEO"


def test_human_decision_override_requires_reason():
    payload = {
        "action": "override",
        "reason": "",
        "decided_by": "CEO",
    }
    response = client.post("/runs/run_123/decision", json=payload)
    assert response.status_code == 400
    assert "reason is required" in response.json()["detail"].lower()


def test_static_html_serving():
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "Colosseum" in response.text


def test_get_fact_by_id_success():
    fake_fact = Fact(
        fact_id="FIN-01",
        domain="finance",
        topic="volume",
        statement="Legion Capital processed 10.0 million payment transactions worth about ₹1,500 crore in September 2026.",
        entity="Legion Capital",
        metric="monthly_payment_volume",
        value_num=1500.0,
        unit="INR crore / month",
        as_of=date(2026, 9, 30),
        source_type="table",
        source_ref="transactions_monthly",
        reliability=Reliability.high,
    )
    with patch("api.main.get_fact", return_value=fake_fact):
        response = client.get("/facts/FIN-01")
        assert response.status_code == 200
        data = response.json()
        assert data["fact_id"] == "FIN-01"
        assert data["domain"] == "finance"
        assert data["value_num"] == 1500.0
        assert "Legion Capital" in data["statement"]


def test_get_fact_by_id_not_found():
    with patch("api.main.get_fact", return_value=None):
        response = client.get("/facts/NONEXISTENT-99")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


def test_get_fact_dataset_colosseum_only():
    fake_settings = MagicMock()
    fake_settings.bigquery.dataset = "colosseum_eval"
    with patch("api.main.get_settings", return_value=fake_settings):
        response = client.get("/facts/FIN-01")
        assert response.status_code == 403
        assert "never colosseum_eval" in response.json()["detail"].lower()
