"""Firestore ledger for runs, routing logs, decisions, and cached embeddings."""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Optional

from google.cloud import firestore
from pydantic import BaseModel

from core.settings import get_settings

_db: Optional[firestore.Client] = None


def get_db() -> firestore.Client:
    """Get or initialize the Firestore client based on settings."""
    global _db
    if _db is None:
        settings = get_settings()
        _db = firestore.Client(
            project=settings.project_id,
            database=settings.firestore.database,
        )
    return _db


def _serialize(obj: Any) -> Any:
    """Recursively convert Pydantic models, dates, and enums into JSON/Firestore serializable types."""
    if obj is None:
        return None
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, list):
        return [_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    return obj


def create_run(run_id: str, brief: Any, initial_status: str = "started") -> None:
    """Create a new run document in the runs collection."""
    db = get_db()
    settings = get_settings()
    collection_name = settings.firestore.collections.runs
    doc_ref = db.collection(collection_name).document(run_id)

    snapshot = doc_ref.get()
    if snapshot.exists:
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    doc_data = {
        "run_id": run_id,
        "status": initial_status,
        "brief": _serialize(brief),
        "progress": [{"timestamp": now_iso, "status": initial_status, "note": f"Run {initial_status}"}],
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    doc_ref.set(doc_data)


def update_status(run_id: str, status: str, progress_note: str | None = None) -> None:
    """Update the status of a run and append a timestamped progress note."""
    db = get_db()
    settings = get_settings()
    collection_name = settings.firestore.collections.runs
    doc_ref = db.collection(collection_name).document(run_id)

    now_iso = datetime.now(timezone.utc).isoformat()
    progress_entry = {
        "timestamp": now_iso,
        "status": status,
        "note": progress_note or "",
    }

    doc_ref.set(
        {
            "status": status,
            "updated_at": now_iso,
            "progress": firestore.ArrayUnion([progress_entry]),
        },
        merge=True,
    )


def save_section(run_id: str, key: str, data: Any) -> None:
    """Store any section / Pydantic model (or list of models) under key in runs/{run_id}."""
    db = get_db()
    settings = get_settings()
    collection_name = settings.firestore.collections.runs
    doc_ref = db.collection(collection_name).document(run_id)

    now_iso = datetime.now(timezone.utc).isoformat()
    serialized = _serialize(data)

    doc_ref.set(
        {
            key: serialized,
            "updated_at": now_iso,
        },
        merge=True,
    )


def get_run(run_id: str) -> dict:
    """Retrieve the run document from Firestore."""
    db = get_db()
    settings = get_settings()
    collection_name = settings.firestore.collections.runs
    doc_ref = db.collection(collection_name).document(run_id)
    snapshot = doc_ref.get()

    if not snapshot.exists:
        return {}
    return snapshot.to_dict() or {}


def save_routing(run_id: str, routing: Any) -> None:
    """Write routing decision data to the routing collection."""
    db = get_db()
    settings = get_settings()
    collection_name = settings.firestore.collections.routing
    doc_ref = db.collection(collection_name).document(run_id)

    doc_ref.set(_serialize(routing))


def save_decision(run_id_or_doc_id: str, decision: Any, timestamp: str | None = None) -> str:
    """Write human decision data to the decisions collection using doc id {run_id}_{timestamp}."""
    db = get_db()
    settings = get_settings()
    collection_name = settings.firestore.collections.decisions

    if timestamp:
        doc_id = f"{run_id_or_doc_id}_{timestamp}"
    elif "_" in run_id_or_doc_id and any(c.isdigit() for c in run_id_or_doc_id.split("_")[-1]):
        # String already includes a timestamp suffix
        doc_id = run_id_or_doc_id
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        doc_id = f"{run_id_or_doc_id}_{ts}"

    doc_ref = db.collection(collection_name).document(doc_id)
    doc_ref.set(_serialize(decision))
    return doc_id


def save_memo(run_id: str, memo: Any) -> None:
    """Save the current drafted memo to the run document under 'memo'."""
    save_section(run_id, "memo", memo)


def save_final_verdict(run_id: str, verdict: Any) -> None:
    """Save the final check verdict to the run document under 'final_verdict'."""
    save_section(run_id, "final_verdict", verdict)


def save_memo_revisions(run_id: str, revisions: list[Any]) -> None:
    """Save memo revisions list to the run document under 'memo_revisions'."""
    save_section(run_id, "memo_revisions", revisions)


def save_human_decision_to_run(run_id: str, decision: Any) -> None:
    """Save human decision to the run document under human_decision and append to human_decisions."""
    db = get_db()
    settings = get_settings()
    collection_name = settings.firestore.collections.runs
    doc_ref = db.collection(collection_name).document(run_id)
    serialized = _serialize(decision)
    now_iso = datetime.now(timezone.utc).isoformat()
    doc_ref.set(
        {
            "human_decision": serialized,
            "human_decisions": firestore.ArrayUnion([serialized]),
            "updated_at": now_iso,
        },
        merge=True,
    )


def delete_run(run_id: str) -> None:
    """Delete the run and its associated documents across collections."""
    db = get_db()
    settings = get_settings()
    db.collection(settings.firestore.collections.runs).document(run_id).delete()
    db.collection(settings.firestore.collections.routing).document(run_id).delete()
    db.collection(settings.firestore.collections.decisions).document(run_id).delete()
