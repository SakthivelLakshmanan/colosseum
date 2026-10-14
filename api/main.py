"""FastAPI application for Colosseum API endpoints and static web hosting."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import threading
from typing import Any, Optional
import uuid

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from google.cloud import firestore

from agents.pipeline import run_decision
from core import ledger
from core.ledger import create_run
from core.registry import find_project_root, get_registry
from core.schemas import DecisionBrief
from core.settings import get_settings

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Colosseum — Decision Intelligence",
    description="API for the Colosseum Decision Intelligence Harness and web dashboard.",
    version="1.0.0",
)

# Enable CORS for local development and browser clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class DecisionRequest(BaseModel):
    action: str = Field(description="'approve' or 'override'")
    reason: Optional[str] = Field(default="", description="Required for override")
    decided_by: Optional[str] = Field(default="", description="Name or role of decider")


@app.get("/health")
def health_check() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/advisers")
def list_advisers() -> list[dict[str, Any]]:
    """Return id, name, criteria, and can_block for each adviser in the registry."""
    registry = get_registry()
    result = []
    for adv in registry.advisers:
        result.append({
            "id": adv.id,
            "name": adv.name,
            "criteria": adv.criteria_owned,
            "can_block": adv.can_block,
        })
    return result


@app.get("/briefs/sample")
def get_sample_brief() -> dict[str, Any]:
    """Return the sample decision brief from data/briefs/DEC-001.json.
    
    The application reads only from data/ and never reads from evals/.
    """
    root = find_project_root()
    brief_file = root / "data" / "briefs" / "DEC-001.json"
    if not brief_file.is_file():
        raise HTTPException(
            status_code=404,
            detail="Sample brief not found in data/briefs/DEC-001.json",
        )
    try:
        content = brief_file.read_text(encoding="utf-8")
        return json.loads(content)
    except Exception as exc:
        logger.error("Failed to read sample brief: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to read sample brief: {exc}")


def _execute_pipeline_background(brief: DecisionBrief, run_id: str) -> None:
    """Execute the decision pipeline in a background worker thread."""
    try:
        run_decision(brief=brief, run_id=run_id)
    except Exception as exc:
        logger.exception("Background execution failed for run %s: %s", run_id, exc)
        try:
            ledger.update_status(run_id, "failed", f"Failed: {exc}")
            ledger.save_section(run_id, "error", str(exc))
        except Exception as log_err:
            logger.error("Failed to update status to failed in ledger for run %s: %s", run_id, log_err)


def start_background_run(run_id: str, brief: DecisionBrief) -> None:
    """Start the decision pipeline in a background thread."""
    bg_thread = threading.Thread(
        target=_execute_pipeline_background,
        args=(brief, run_id),
        name=f"pipeline-{run_id}",
        daemon=True,
    )
    bg_thread.start()


@app.post("/decisions", status_code=200)
@app.post("/runs", status_code=200)
def create_decision_run(brief: DecisionBrief) -> dict[str, str]:
    """Create a new run in Firestore with status 'queued', start pipeline in background thread, and return run_id."""
    now_utc = datetime.now(timezone.utc)
    now_str = now_utc.strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    run_id = f"run_{brief.decision_id}_{now_str}_{suffix}"

    # Create run with status 'queued' in Firestore
    try:
        create_run(run_id=run_id, brief=brief, initial_status="queued")
    except Exception as exc:
        logger.error("Failed to create queued run %s in ledger: %s", run_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to create run in ledger: {exc}")

    # Launch pipeline in background thread
    start_background_run(run_id, brief)

    return {"run_id": run_id}


def _extract_final_verdict(run_doc: dict[str, Any]) -> Optional[str]:
    """Extract a simple string verdict value from a run document."""
    fv = run_doc.get("final_verdict")
    if fv:
        if isinstance(fv, dict):
            return str(fv.get("verdict") or "")
        return str(fv)
    fvs = run_doc.get("final_verdicts")
    if isinstance(fvs, list) and fvs:
        last_item = fvs[-1]
        if isinstance(last_item, dict):
            return str(last_item.get("verdict") or "")
        return str(last_item)
    return None


@app.get("/runs/{run_id}")
def get_run_details(run_id: str) -> dict[str, Any]:
    """Return the run document (status, routing, positions, claim verdicts, scorecard, memo, final verdict)."""
    run_doc = ledger.get_run(run_id)
    if not run_doc or not run_doc.get("run_id"):
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    # Populate memo and final_verdict convenience fields if versions exist
    if not run_doc.get("memo") and run_doc.get("memo_versions"):
        memos = run_doc["memo_versions"]
        if isinstance(memos, list) and memos:
            run_doc["memo"] = memos[-1]

    if not run_doc.get("final_verdict") and run_doc.get("final_verdicts"):
        verdicts = run_doc["final_verdicts"]
        if isinstance(verdicts, list) and verdicts:
            run_doc["final_verdict"] = verdicts[-1]

    return run_doc


@app.get("/runs")
def list_recent_runs(limit: int = Query(default=10, ge=1, le=100)) -> list[dict[str, Any]]:
    """Return recent runs, newest first: run_id, status, created_at, final_verdict."""
    db = ledger.get_db()
    settings = get_settings()
    collection_name = settings.firestore.collections.runs
    col = db.collection(collection_name)

    results: list[dict[str, Any]] = []
    try:
        docs = col.order_by("created_at", direction=firestore.Query.DESCENDING).limit(limit).stream()
        for doc in docs:
            d = doc.to_dict() or {}
            results.append({
                "run_id": d.get("run_id") or doc.id,
                "status": d.get("status", ""),
                "created_at": d.get("created_at", ""),
                "final_verdict": _extract_final_verdict(d),
            })
    except Exception as exc:
        logger.warning("Firestore order_by query failed, sorting in memory: %s", exc)
        docs = col.limit(limit * 3).stream()
        for doc in docs:
            d = doc.to_dict() or {}
            results.append({
                "run_id": d.get("run_id") or doc.id,
                "status": d.get("status", ""),
                "created_at": d.get("created_at", ""),
                "final_verdict": _extract_final_verdict(d),
            })
        results.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        results = results[:limit]

    return results


@app.post("/runs/{run_id}/decision")
def record_human_decision(run_id: str, payload: DecisionRequest) -> dict[str, Any]:
    """Save approve/override decision to human_decisions collection. A reason is required for override."""
    action = payload.action.strip().lower()
    if action not in ("approve", "override"):
        raise HTTPException(status_code=400, detail="Action must be 'approve' or 'override'")

    reason = (payload.reason or "").strip()
    if action == "override" and not reason:
        raise HTTPException(status_code=400, detail="A reason is required for override.")

    run_doc = ledger.get_run(run_id)
    if not run_doc or not run_doc.get("run_id"):
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    decided_by = (payload.decided_by or "").strip() or "human_approver"
    now_iso = datetime.now(timezone.utc).isoformat()

    decision_record = {
        "run_id": run_id,
        "action": action,
        "reason": reason,
        "note": reason,
        "decided_by": decided_by,
        "decided_at": now_iso,
    }

    try:
        ledger.save_decision(run_id, decision_record)
        ledger.save_section(run_id, "human_decision", decision_record)
    except Exception as exc:
        logger.error("Failed to save human decision for run %s: %s", run_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to record decision: {exc}")

    return {"status": "ok", "decision": decision_record}


# Mount static folder web/ at root '/' so the web dashboard and API deploy together
_root_dir = find_project_root()
_web_dir = _root_dir / "web"
if not _web_dir.is_dir():
    _web_dir.mkdir(parents=True, exist_ok=True)

app.mount("/", StaticFiles(directory=str(_web_dir), html=True), name="static")
