"""FastAPI application for Colosseum API endpoints and static web hosting."""
from __future__ import annotations

from datetime import datetime, timezone
import hmac
import json
import logging
import os
from pathlib import Path
import threading
from typing import Any, Optional
import uuid

from fastapi import Depends, FastAPI, HTTPException, Query, Request
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
from knowledge.fact_store import get_fact

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


_active_runs_lock = threading.Lock()
_active_runs: set[str] = set()


def _get_max_active_runs() -> int:
    val = os.getenv("MAX_ACTIVE_RUNS", "1").strip()
    try:
        return max(1, int(val))
    except (ValueError, TypeError):
        return 1


def _try_acquire_run_slot(run_id: str) -> bool:
    max_active = _get_max_active_runs()
    with _active_runs_lock:
        if len(_active_runs) >= max_active:
            return False
        _active_runs.add(run_id)
        return True


def _release_run_slot(run_id: str) -> None:
    with _active_runs_lock:
        _active_runs.discard(run_id)


def get_active_runs() -> set[str]:
    with _active_runs_lock:
        return set(_active_runs)


def reset_active_runs() -> None:
    """Reset active runs tracking (useful in tests)."""
    with _active_runs_lock:
        _active_runs.clear()


def _is_access_code_required() -> bool:
    code = os.getenv("ACCESS_CODE", "").strip()
    return bool(code)


def verify_access_code(request: Request) -> None:
    code = os.getenv("ACCESS_CODE", "").strip()
    if not code:
        return
    provided = (request.headers.get("x-access-code") or "").strip()
    if not provided or not hmac.compare_digest(provided, code):
        raise HTTPException(status_code=401, detail="access code required")


@app.get("/health")
def health_check() -> dict[str, Any]:
    """Health check endpoint."""
    return {
        "status": "ok",
        "access_code_required": _is_access_code_required(),
    }


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
    finally:
        _release_run_slot(run_id)


def start_background_run(run_id: str, brief: DecisionBrief) -> None:
    """Start the decision pipeline in a background thread."""
    bg_thread = threading.Thread(
        target=_execute_pipeline_background,
        args=(brief, run_id),
        name=f"pipeline-{run_id}",
        daemon=True,
    )
    bg_thread.start()


@app.post("/decisions", status_code=200, dependencies=[Depends(verify_access_code)])
@app.post("/runs", status_code=200, dependencies=[Depends(verify_access_code)])
def create_decision_run(brief: DecisionBrief) -> dict[str, str]:
    """Create a new run in Firestore with status 'queued', start pipeline in background thread, and return run_id."""
    now_utc = datetime.now(timezone.utc)
    now_str = now_utc.strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:6]
    run_id = f"run_{brief.decision_id}_{now_str}_{suffix}"

    if not _try_acquire_run_slot(run_id):
        raise HTTPException(
            status_code=429,
            detail="a debate is already running, try again in a few minutes",
        )

    # Create run with status 'queued' in Firestore
    try:
        create_run(run_id=run_id, brief=brief, initial_status="queued")
    except Exception as exc:
        _release_run_slot(run_id)
        logger.error("Failed to create queued run %s in ledger: %s", run_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to create run in ledger: {exc}")

    # Launch pipeline in background thread
    try:
        start_background_run(run_id, brief)
    except Exception as exc:
        _release_run_slot(run_id)
        logger.error("Failed to start background thread for run %s: %s", run_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to start run: {exc}")

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
    """Return the run document with guaranteed shape:
    run_id, status, created_at, brief, routing, positions, claim_verdicts,
    scorecard, memo, memo_revisions, final_verdict, human_decisions, error.
    Use null or empty list when a part does not exist yet.
    """
    run_doc = ledger.get_run(run_id)
    if not run_doc or not run_doc.get("run_id"):
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    # 1. Resolve memo (from memo, memo_versions, or memo_revisions)
    memo = run_doc.get("memo")
    if not memo:
        memo_versions = run_doc.get("memo_versions")
        if isinstance(memo_versions, list) and memo_versions:
            memo = memo_versions[-1]
        elif isinstance(run_doc.get("memo_revisions"), list) and run_doc["memo_revisions"]:
            last_rev = run_doc["memo_revisions"][-1]
            if isinstance(last_rev, dict) and "memo" in last_rev:
                memo = last_rev["memo"]
            else:
                memo = last_rev
        elif run_doc.get("final_memo"):
            memo = run_doc.get("final_memo")
        elif run_doc.get("scribe_memo"):
            memo = run_doc.get("scribe_memo")
    if not isinstance(memo, dict):
        memo = None

    # 2. Resolve final_verdict (from final_verdict, final_verdicts, or verdict)
    final_verdict = run_doc.get("final_verdict")
    if not final_verdict:
        final_verdicts = run_doc.get("final_verdicts")
        if isinstance(final_verdicts, list) and final_verdicts:
            final_verdict = final_verdicts[-1]
        elif run_doc.get("verdict"):
            final_verdict = run_doc.get("verdict")
    if isinstance(final_verdict, str):
        final_verdict = {"verdict": final_verdict}
    elif not isinstance(final_verdict, dict):
        final_verdict = None

    # 3. Resolve memo_revisions (from memo_revisions or reconstructed from versions + verdicts)
    memo_revisions = run_doc.get("memo_revisions")
    if not isinstance(memo_revisions, list) or not memo_revisions:
        memo_versions = run_doc.get("memo_versions")
        final_verdicts = run_doc.get("final_verdicts")
        if isinstance(memo_versions, list) and memo_versions:
            reconstructed = []
            v_list = final_verdicts if isinstance(final_verdicts, list) else []
            for idx, m in enumerate(memo_versions):
                v = v_list[idx] if idx < len(v_list) else None
                reconstructed.append({
                    "revision": idx,
                    "memo": m,
                    "verdict": v,
                })
            memo_revisions = reconstructed
        else:
            memo_revisions = []

    # 4. Resolve human_decisions
    human_decisions = run_doc.get("human_decisions")
    if not isinstance(human_decisions, list):
        if run_doc.get("human_decision") and isinstance(run_doc["human_decision"], dict):
            human_decisions = [run_doc["human_decision"]]
        else:
            human_decisions = []

    # 5. Resolve lists and live maps
    positions = run_doc.get("positions") if isinstance(run_doc.get("positions"), list) else []
    claim_verdicts = run_doc.get("claim_verdicts") if isinstance(run_doc.get("claim_verdicts"), list) else []
    adviser_status = run_doc.get("adviser_status") if isinstance(run_doc.get("adviser_status"), dict) else {}
    positions_live = run_doc.get("positions_live") if isinstance(run_doc.get("positions_live"), dict) else {}

    # 6. Resolve dicts or nulls
    brief = run_doc.get("brief") if isinstance(run_doc.get("brief"), dict) else None
    routing = run_doc.get("routing") if isinstance(run_doc.get("routing"), dict) else None
    scorecard = run_doc.get("scorecard") if isinstance(run_doc.get("scorecard"), dict) else None

    # If positions is empty but positions_live has entries, return positions ordered by routing score
    if not positions and positions_live:
        score_map: dict[str, float] = {}
        if routing and isinstance(routing, dict):
            scores_list = routing.get("scores") or []
            if isinstance(scores_list, list):
                for s in scores_list:
                    if isinstance(s, dict) and "adviser_id" in s:
                        aid = str(s["adviser_id"])
                        sim = s.get("similarity")
                        if sim is None:
                            sim = s.get("score", 0.0)
                        try:
                            score_map[aid] = float(sim)
                        except (ValueError, TypeError):
                            score_map[aid] = 0.0
        live_list = list(positions_live.values())
        live_list.sort(
            key=lambda p: score_map.get(
                p.get("adviser_id") if isinstance(p, dict) else getattr(p, "adviser_id", ""),
                0.0,
            ),
            reverse=True,
        )
        positions = live_list

    # 7. Resolve error
    error = str(run_doc["error"]) if run_doc.get("error") is not None else None

    return {
        "run_id": str(run_doc.get("run_id") or run_id),
        "status": run_doc.get("status"),
        "created_at": run_doc.get("created_at"),
        "brief": brief,
        "routing": routing,
        "positions": positions,
        "adviser_status": adviser_status,
        "positions_live": positions_live,
        "claim_verdicts": claim_verdicts,
        "scorecard": scorecard,
        "memo": memo,
        "memo_revisions": memo_revisions,
        "final_verdict": final_verdict,
        "human_decisions": human_decisions,
        "error": error,
    }


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


@app.post("/runs/{run_id}/decision", dependencies=[Depends(verify_access_code)])
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
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()
    now_ts = now_utc.strftime("%Y%m%d_%H%M%S_%f")
    doc_id = f"{run_id}_{now_ts}"

    decision_record = {
        "run_id": run_id,
        "action": action,
        "reason": reason,
        "note": reason,
        "decided_by": decided_by,
        "decided_at": now_iso,
    }

    try:
        ledger.save_decision(doc_id, decision_record)
        ledger.save_section(run_id, "human_decision", decision_record)
        ledger.save_human_decision_to_run(run_id, decision_record)
    except Exception as exc:
        logger.error("Failed to save human decision for run %s: %s", run_id, exc)
        raise HTTPException(status_code=500, detail=f"Failed to record decision: {exc}")

    return {"status": "ok", "decision": decision_record}


@app.get("/facts/{fact_id}")
def get_fact_by_id(fact_id: str) -> dict[str, Any]:
    """Retrieve fact metadata by fact_id from the colosseum dataset only.

    Never reads from colosseum_eval. Returns 404 if not found.
    """
    settings = get_settings()
    dataset = getattr(settings.bigquery, "dataset", "colosseum")
    if dataset == "colosseum_eval" or "eval" in dataset.lower():
        raise HTTPException(
            status_code=403,
            detail="Forbidden: facts endpoint reads only from the colosseum dataset, never colosseum_eval.",
        )
    fact = get_fact(fact_id)
    if not fact:
        raise HTTPException(status_code=404, detail=f"Fact '{fact_id}' not found")
    return fact.model_dump(mode="json")


# Mount static folder web/ at root '/' so the web dashboard and API deploy together
_root_dir = find_project_root()
_web_dir = _root_dir / "web"
if not _web_dir.is_dir():
    _web_dir.mkdir(parents=True, exist_ok=True)

app.mount("/", StaticFiles(directory=str(_web_dir), html=True), name="static")
