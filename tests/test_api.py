"""Unit tests for FastAPI endpoints in api/main.py."""
import json
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from api.main import app
from core.schemas import DecisionBrief

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
    with patch("core.ledger.get_run", return_value={"run_id": "run_123"}), \
         patch("core.ledger.save_decision") as mock_save_dec, \
         patch("core.ledger.save_section") as mock_save_sec:

        payload = {
            "action": "approve",
            "reason": "Risk acceptable",
            "decided_by": "Chief Risk Officer",
        }
        response = client.post("/runs/run_123/decision", json=payload)
        assert response.status_code == 200
        res = response.json()
        assert res["status"] == "ok"
        assert res["decision"]["action"] == "approve"
        assert res["decision"]["decided_by"] == "Chief Risk Officer"
        mock_save_dec.assert_called_once()
        mock_save_sec.assert_called_once()


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
