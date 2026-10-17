"""Unit tests for live advisers, status contract, and real-time Firestore ledger writes."""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from api.main import app
from agents.adviser_factory import run_advisers, run_advisers_sync
from agents.pipeline import run_decision
from core import ledger
from core.schemas import (
    AdviserPosition,
    AdviserScore,
    DecisionBrief,
    EvidencePack,
    FinalVerdict,
    Memo,
    MemoVerdictType,
    RoutingDecision,
    Scorecard,
)


@pytest.fixture
def mock_brief():
    return DecisionBrief(
        decision_id="DEC-LIVE-001",
        question="Should we migrate to a new core banking provider?",
        options={"A": "Stay on legacy", "B": "Migrate to Brevis"},
        constraints=["Localisation in India"],
        criteria=[],
    )


@pytest.mark.asyncio
async def test_three_advisers_saved_in_finish_order(mock_brief):
    """Prove three advisers with different delays call on_adviser_done and save to Firestore in finish order."""
    delays = {
        "fast": 0.02,
        "mid": 0.06,
        "slow": 0.10,
    }

    async def fake_run_adviser_async(aid: str, brief: Any, pack: Any, on_first_slot: Any = None):
        if on_first_slot:
            on_first_slot()
        delay = delays.get(aid, 0.05)
        await asyncio.sleep(delay)
        return AdviserPosition(
            adviser_id=aid,
            stance="backs_b",
            summary=f"{aid} summary",
            claims=[],
            criterion_scores=[],
            confidence=0.85,
        )

    saved_calls: list[tuple[str, str, float]] = []

    import inspect
    _sig = inspect.signature(ledger.save_adviser_result_sync)

    def fake_save_sync(*args, **kwargs):
        a = _sig.bind_partial(*args, **kwargs).arguments
        error = a.get("error")
        state = "done" if not error else ("timeout" if error == "timeout" else "failed")
        saved_calls.append((a.get("adviser_id"), state, a.get("seconds") or 0.0))

    order_reported: list[str] = []

    async def on_done(aid: str, pos: AdviserPosition | None, err: str | None, secs: float):
        order_reported.append(aid)
        await ledger.save_adviser_result(
            run_id="run_live_123",
            adviser_id=aid,
            position=pos,
            error=err,
            seconds=secs,
        )

    with patch("agents.adviser_factory.run_adviser_async", side_effect=fake_run_adviser_async), \
         patch("agents.adviser_factory.build_pack", return_value=EvidencePack(adviser_id="x", facts=[], summaries=[], total_matching_facts=0)), \
         patch("agents.adviser_factory.repair_adviser_criterion_scores", side_effect=lambda aid, b, p: p), \
         patch("core.ledger.save_adviser_result_sync", side_effect=fake_save_sync):

        results = await run_advisers(
            adviser_ids=["slow", "fast", "mid"],
            brief=mock_brief,
            timeout_s=5.0,
            on_adviser_done=on_done,
        )

    # 1. Assert all 3 advisers finished and returned valid positions
    assert len(results) == 3
    for aid in ["fast", "mid", "slow"]:
        assert isinstance(results[aid], AdviserPosition)

    # 2. Assert callbacks fired in finish order (fast -> mid -> slow)
    assert order_reported == ["fast", "mid", "slow"], f"Expected ['fast', 'mid', 'slow'] but got {order_reported}"

    # 3. Assert Firestore ledger saved results in finish order with state 'done'
    assert [c[0] for c in saved_calls] == ["fast", "mid", "slow"]
    assert all(c[1] == "done" for c in saved_calls)


@pytest.mark.asyncio
async def test_timed_out_adviser_gets_state_timeout(mock_brief):
    """Prove an adviser that times out gets error='timeout' and Firestore state='timeout'."""
    async def fake_slow_adviser(aid: str, brief: Any, pack: Any, on_first_slot: Any = None):
        if on_first_slot:
            on_first_slot()
        # Sleep longer than the 0.05s timeout
        await asyncio.sleep(0.5)
        return AdviserPosition(
            adviser_id=aid,
            stance="unsure",
            summary="Never reaches here",
            claims=[],
            criterion_scores=[],
            confidence=0.5,
        )

    reported_callbacks: list[tuple[str, Any, str | None]] = []
    updated_firestore_data: list[dict[str, Any]] = []

    mock_doc_ref = MagicMock()
    mock_doc_ref.update.side_effect = lambda data: updated_firestore_data.append(data)

    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref

    async def on_done(aid: str, pos: AdviserPosition | None, err: str | None, secs: float):
        reported_callbacks.append((aid, pos, err))
        await ledger.save_adviser_result(
            run_id="run_timeout_123",
            adviser_id=aid,
            position=pos,
            error=err,
            seconds=secs,
        )

    with patch("agents.adviser_factory.run_adviser_async", side_effect=fake_slow_adviser), \
         patch("agents.adviser_factory.build_pack", return_value=EvidencePack(adviser_id="legal", facts=[], summaries=[], total_matching_facts=0)), \
         patch("agents.adviser_factory._request_final_position_without_tools", side_effect=TimeoutError("Graceful timeout expired")), \
         patch("core.ledger.get_db", return_value=mock_db):

        results = await run_advisers(
            adviser_ids=["legal"],
            brief=mock_brief,
            timeout_s=0.05,
            on_adviser_done=on_done,
        )

    # 1. Result should indicate timeout
    assert "legal" in results
    assert "Timeout after" in str(results["legal"])

    # 2. Callback received error="timeout" and position=None
    assert len(reported_callbacks) == 1
    aid, pos, err = reported_callbacks[0]
    assert aid == "legal"
    assert pos is None
    assert err == "timeout"

    # 3. Firestore update received state: 'timeout' with dotted path
    assert len(updated_firestore_data) == 1
    update_data = updated_firestore_data[0]
    assert "adviser_status.legal" in update_data
    status_entry = update_data["adviser_status.legal"]
    assert status_entry["state"] == "timeout"
    assert status_entry["error"] == "timeout"
    # Live position map should NOT have an entry for a timed-out adviser
    assert "positions_live.legal" not in update_data


def test_api_fills_positions_from_positions_live():
    """Prove GET /runs/{id} returns positions_live and fills positions ordered by routing score when positions is empty."""
    client = TestClient(app)

    live_positions = {
        "ops": {
            "adviser_id": "ops",
            "stance": "unsure",
            "summary": "Ops evaluation",
            "claims": [],
            "criterion_scores": [],
            "confidence": 0.6,
        },
        "finance": {
            "adviser_id": "finance",
            "stance": "backs_b",
            "summary": "Finance evaluation",
            "claims": [],
            "criterion_scores": [],
            "confidence": 0.9,
        },
        "legal": {
            "adviser_id": "legal",
            "stance": "conditional",
            "summary": "Legal evaluation",
            "claims": [],
            "criterion_scores": [],
            "confidence": 0.95,
        },
    }

    live_adviser_status = {
        "ops": {"state": "done", "seconds": 3.2, "error": None},
        "finance": {"state": "done", "seconds": 2.1, "error": None},
        "legal": {"state": "done", "seconds": 4.5, "error": None},
    }

    run_doc = {
        "run_id": "run_live_test_456",
        "status": "advisers",
        "created_at": "2026-10-17T10:00:00Z",
        "brief": {"decision_id": "DEC-TEST", "question": "Test?"},
        "routing": {
            "selected": ["legal", "finance", "ops"],
            "scores": [
                {"adviser_id": "legal", "similarity": 0.95},
                {"adviser_id": "finance", "similarity": 0.88},
                {"adviser_id": "ops", "similarity": 0.52},
            ],
        },
        "positions": [],  # Empty while stage is running
        "positions_live": live_positions,
        "adviser_status": live_adviser_status,
    }

    with patch("core.ledger.get_run", return_value=run_doc):
        response = client.get("/runs/run_live_test_456")
        assert response.status_code == 200
        data = response.json()

        # Both live fields returned
        assert "adviser_status" in data
        assert data["adviser_status"]["finance"]["state"] == "done"
        assert "positions_live" in data
        assert "legal" in data["positions_live"]

        # positions is filled from positions_live ordered by similarity: legal (0.95) -> finance (0.88) -> ops (0.52)
        assert len(data["positions"]) == 3
        pos_ids = [p["adviser_id"] for p in data["positions"]]
        assert pos_ids == ["legal", "finance", "ops"]


def test_init_adviser_statuses():
    """Prove init_adviser_statuses writes 'working' state for all selected advisers and empty positions_live."""
    mock_doc_ref = MagicMock()
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref

    with patch("core.ledger.get_db", return_value=mock_db):
        ledger.init_adviser_statuses("run_test_init", ["finance", "infosec"])

    mock_doc_ref.set.assert_called_once()
    saved_doc = mock_doc_ref.set.call_args[0][0]
    assert "adviser_status" in saved_doc
    assert set(saved_doc["adviser_status"].keys()) == {"finance", "infosec"}
    for aid in ["finance", "infosec"]:
        st = saved_doc["adviser_status"][aid]
        assert st["state"] == "working"
        assert st["finished_at"] is None
        assert st["error"] is None
        assert "started_at" in st
    assert saved_doc["positions_live"] == {}


@pytest.mark.asyncio
async def test_save_adviser_result_error_resilience():
    """Prove Firestore write failure logs a warning and does not crash the run."""
    mock_doc_ref = MagicMock()
    mock_doc_ref.update.side_effect = RuntimeError("Firestore unavailable")
    mock_db = MagicMock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref

    with patch("core.ledger.get_db", return_value=mock_db):
        # Must not raise RuntimeError
        await ledger.save_adviser_result(
            run_id="run_err_test",
            adviser_id="finance",
            position=None,
            error=None,
            seconds=1.2,
        )


def test_pipeline_status_contract(mock_brief):
    """Prove pipeline transitions strictly adhere to the allowed status values contract:
    routing, advisers, moderating, scoring, writing, checking, then done or failed.
    """
    routing = RoutingDecision(
        decision_id="DEC-TEST",
        routing_query="pricing",
        scores=[
            AdviserScore(
                adviser_id="finance",
                similarity=0.9,
                best_matching_capability="Costs",
                selected=True,
                reason="above margin",
            )
        ],
        selected=["finance"],
        created_at="2026-10-14T12:00:00Z",
    )

    pack = EvidencePack(adviser_id="finance", facts=[], summaries=[], total_matching_facts=0)
    pos = AdviserPosition(
        adviser_id="finance",
        stance="conditional",
        summary="Finance summary",
        claims=[],
        criterion_scores=[],
        confidence=0.9,
    )
    scorecard = Scorecard(rows=[], weighted_totals={}, blocked_options={})
    memo = Memo(
        decision_id="DEC-TEST",
        recommendation="Adopt B",
        stance="conditional",
        confidence=0.9,
        conditions=[],
        phased_plan=[],
        scorecard=scorecard,
        key_numbers=[],
        blocking_findings=[],
        dissent=[],
        assumptions=[],
        numbers_to_watch=[],
        evidence_index=[],
    )
    verdict = FinalVerdict(
        verdict=MemoVerdictType.pass_,
        issues=[],
        revision_instructions="",
        revision_number=0,
    )

    recorded_statuses: list[str] = []

    def fake_update_status(run_id, status, note=None):
        recorded_statuses.append(status)

    allowed_statuses = {
        "routing",
        "advisers",
        "moderating",
        "scoring",
        "writing",
        "checking",
        "done",
        "failed",
    }

    with patch("agents.pipeline.route_decision", return_value=routing), \
         patch("agents.pipeline.build_pack", return_value=pack), \
         patch("agents.pipeline.run_advisers_sync", return_value={"finance": pos}) as mock_adv_sync, \
         patch("agents.pipeline.check_claims", return_value=[]), \
         patch("agents.pipeline.build_scorecard", return_value=scorecard), \
         patch("agents.pipeline.write_memo", return_value=memo), \
         patch("agents.pipeline.check_memo", return_value=verdict), \
         patch("core.ledger.create_run"), \
         patch("core.ledger.update_status", side_effect=fake_update_status), \
         patch("core.ledger.init_adviser_statuses") as mock_init_statuses, \
         patch("core.ledger.save_adviser_result"), \
         patch("core.ledger.save_routing"), \
         patch("core.ledger.save_memo"), \
         patch("core.ledger.save_final_verdict"), \
         patch("core.ledger.save_memo_revisions"), \
         patch("core.ledger.save_section"):

        run_decision(brief=mock_brief, run_id="run_contract_check")

    # Verify every recorded status is in the allowed set
    for st in recorded_statuses:
        assert st in allowed_statuses, f"Status '{st}' is not in allowed contract {allowed_statuses}"

    # Confirm sequence of stages
    assert recorded_statuses == [
        "routing",
        "advisers",
        "moderating",
        "scoring",
        "writing",
        "checking",
        "done",
    ]

    # Confirm init_adviser_statuses was called with selected advisers
    mock_init_statuses.assert_called_once_with("run_contract_check", ["finance"])

    # Confirm on_adviser_done callback was passed to run_advisers_sync
    assert "on_adviser_done" in mock_adv_sync.call_args[1]
    assert callable(mock_adv_sync.call_args[1]["on_adviser_done"])
