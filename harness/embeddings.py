"""Embedding generation with Firestore caching using gemini-embedding-001."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
import os
from typing import Any

from core.gemini import acquire_gemini_slot_sync, get_genai_client, release_gemini_slot
from core.ledger import get_db
from core.settings import get_settings


def _compute_cache_key(model: str, task_type: str, text: str) -> str:
    """Compute sha256 key from model + task_type + text."""
    key_str = f"{model}{task_type}{text}"
    return hashlib.sha256(key_str.encode("utf-8")).hexdigest()


def embed(texts: list[str], task_type: str = "RETRIEVAL_QUERY") -> list[list[float]]:
    """Embed a list of texts using google-genai on Vertex AI with Firestore caching.
    
    Vectors are cached in the Firestore collection `cache_embeddings` keyed by sha256 of
    model + task_type + text. Only cache misses are sent to Vertex AI in batch.
    Logs cache hit and cache miss counts.
    """
    if not texts:
        return []

    settings = get_settings()
    model = settings.models.embedding

    db = get_db()
    col = db.collection("cache_embeddings")

    # 1. Compute cache keys for all texts
    keys = [_compute_cache_key(model, task_type, t) for t in texts]

    # 2. Check Firestore cache in batch
    cache_map: dict[str, list[float]] = {}
    try:
        doc_refs = [col.document(k) for k in keys]
        snapshots = db.get_all(doc_refs)
        for snap in snapshots:
            if snap.exists:
                data = snap.to_dict() or {}
                emb = data.get("embedding")
                if isinstance(emb, list):
                    cache_map[snap.id] = emb
    except Exception as exc:
        logger.warning(f"Failed to read embeddings from Firestore cache: {exc}")

    # 3. Calculate hits and misses and log
    cache_hits = sum(1 for k in keys if k in cache_map)
    cache_misses = len(texts) - cache_hits
    logger.info("Embeddings: %d cache hit(s), %d cache miss(es)", cache_hits, cache_misses)
    print(f"[embeddings] Cache check: {cache_hits} cache hit(s), {cache_misses} cache miss(es)")

    # 4. Batch embed missing items via Vertex AI
    missing_indices = [i for i, k in enumerate(keys) if k not in cache_map]
    if missing_indices:
        client = get_genai_client()
        missing_texts = [texts[i] for i in missing_indices]
        missing_keys = [keys[i] for i in missing_indices]

        chunk_size = 100
        newly_cached: dict[str, tuple[str, list[float]]] = {}

        for start_idx in range(0, len(missing_texts), chunk_size):
            chunk_texts = missing_texts[start_idx : start_idx + chunk_size]
            chunk_keys = missing_keys[start_idx : start_idx + chunk_size]

            acquire_gemini_slot_sync()
            try:
                resp = client.models.embed_content(
                    model=model,
                    contents=chunk_texts,
                    config={"task_type": task_type},
                )
            finally:
                release_gemini_slot()

            for k, text_val, emb_obj in zip(chunk_keys, chunk_texts, resp.embeddings):
                vector = list(emb_obj.values)
                cache_map[k] = vector
                newly_cached[k] = (text_val, vector)

        # 5. Save misses to Firestore cache_embeddings in batches
        try:
            write_items = list(newly_cached.items())
            batch_limit = 10
            for start_idx in range(0, len(write_items), batch_limit):
                batch = db.batch()
                for key_id, (txt, vec) in write_items[start_idx : start_idx + batch_limit]:
                    doc_ref = col.document(key_id)
                    batch.set(doc_ref, {
                        "model": model,
                        "task_type": task_type,
                        "text": txt,
                        "embedding": vec,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    })
                batch.commit()
        except Exception as exc:
            logger.warning(f"Failed to write embeddings to Firestore cache: {exc}")

    # 6. Return ordered embeddings matching the input texts
    return [cache_map[k] for k in keys]
