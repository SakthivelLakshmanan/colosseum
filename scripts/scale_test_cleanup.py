"""Cleanup script for Colosseum scale test artifacts.

Deletes:
1. BigQuery dataset `colosseum_scale` (including all tables inside it).
2. Firestore scale test cache collections (`scale_cache_summaries`, `scale_cache_embeddings`).

Usage:
    python scripts/scale_test_cleanup.py
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from google.cloud import bigquery
from google.cloud import firestore

from core.ledger import get_db
from core.settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scale_test_cleanup")


def delete_firestore_collection(db: firestore.Client, col_name: str, batch_size: int = 100) -> int:
    """Recursively delete all documents in a Firestore collection in batches."""
    col_ref = db.collection(col_name)
    total_deleted = 0

    while True:
        docs = list(col_ref.limit(batch_size).stream())
        if not docs:
            break
        batch = db.batch()
        for doc in docs:
            batch.delete(doc.reference)
        batch.commit()
        total_deleted += len(docs)
        logger.info("Deleted %d documents from Firestore collection '%s'...", total_deleted, col_name)

    return total_deleted


def cleanup(scale_dataset: str = "colosseum_scale", cache_prefix: str = "scale_"):
    settings = get_settings()
    project_id = settings.project_id
    location = settings.bigquery.location

    logger.info("=== Starting Scale Test Cleanup ===")
    logger.info("Project: %s | Location: %s", project_id, location)
    logger.info("Scale Dataset to delete: %s", scale_dataset)

    # 1. Delete BigQuery colosseum_scale dataset
    bq_client = bigquery.Client(project=project_id, location=location)
    dataset_ref = bq_client.dataset(scale_dataset)

    try:
        bq_client.delete_dataset(dataset_ref, delete_contents=True, not_found_ok=True)
        logger.info("BigQuery dataset `%s.%s` successfully deleted.", project_id, scale_dataset)
        print(f"[BigQuery] Deleted dataset {project_id}.{scale_dataset}")
    except Exception as exc:
        logger.error("Failed to delete BigQuery dataset `%s.%s`: %s", project_id, scale_dataset, exc)

    # 2. Delete Firestore scale cache collections
    try:
        db = get_db()
        collections_to_clean = [
            f"{cache_prefix}cache_summaries",
            f"{cache_prefix}cache_embeddings",
        ]

        for col_name in collections_to_clean:
            deleted_count = delete_firestore_collection(db, col_name)
            logger.info("Deleted %d documents from Firestore collection '%s'.", deleted_count, col_name)
            print(f"[Firestore] Deleted {deleted_count} documents from {col_name}")

    except Exception as exc:
        logger.error("Failed to clean up Firestore scale caches: %s", exc)

    logger.info("=== Scale Test Cleanup Finished ===")


def main():
    parser = argparse.ArgumentParser(description="Cleanup Colosseum Scale Test Resources")
    parser.add_argument("--scale-dataset", type=str, default="colosseum_scale", help="Scale BigQuery dataset name to delete")
    parser.add_argument("--cache-prefix", type=str, default="scale_", help="Cache prefix for collections to delete")
    args = parser.parse_args()

    cleanup(scale_dataset=args.scale_dataset, cache_prefix=args.cache_prefix)


if __name__ == "__main__":
    main()
