"""Cleanup script for Colosseum scale test resources.

Deletes:
1. BigQuery dataset `colosseum_scale` and all its tables.
2. Firestore scale test cache collections (`scale_test_cache_summaries`, `scale_test_cache_embeddings`, etc.).
"""
from __future__ import annotations

import logging
import sys

from google.cloud import bigquery

from core.ledger import get_db
from core.settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("scale_test_cleanup")


def cleanup_scale_test() -> None:
    settings = get_settings()

    # 1. Delete BigQuery dataset colosseum_scale
    dataset_name = "colosseum_scale"
    dataset_id = f"{settings.project_id}.{dataset_name}"
    bq_client = bigquery.Client(project=settings.project_id, location=settings.bigquery.location)

    try:
        bq_client.delete_dataset(dataset_id, delete_contents=True, not_found_ok=True)
        print(f"[cleanup] Deleted BigQuery dataset '{dataset_id}' (and all contained tables).")
    except Exception as e:
        print(f"[cleanup] Note during BigQuery dataset deletion: {e}")

    # 2. Delete Firestore scale cache collections
    db = get_db()
    collections_to_clean = [
        "scale_test_cache_summaries",
        "scale_test_cache_embeddings",
        "scale_test_base_cache_summaries",
        "scale_test_base_cache_embeddings",
    ]

    total_deleted_docs = 0
    for col_name in collections_to_clean:
        try:
            col_ref = db.collection(col_name)
            docs = list(col_ref.stream())
            if not docs:
                continue

            batch_size = 100
            for i in range(0, len(docs), batch_size):
                batch = db.batch()
                for doc in docs[i : i + batch_size]:
                    batch.delete(doc.reference)
                batch.commit()

            total_deleted_docs += len(docs)
            print(f"[cleanup] Deleted {len(docs)} documents from Firestore collection '{col_name}'.")
        except Exception as e:
            print(f"[cleanup] Note during Firestore collection '{col_name}' deletion: {e}")

    print(f"[cleanup] Scale test cleanup complete. Total Firestore documents removed: {total_deleted_docs}.")


if __name__ == "__main__":
    cleanup_scale_test()
