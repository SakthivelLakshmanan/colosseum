"""Verification script for core/ledger.py with Firestore."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.ledger import create_run, delete_run, get_run, update_status
from core.schemas import DecisionBrief


def load_sample_brief() -> DecisionBrief:
    """Load sample decision brief from evals/brief_DEC-001.json if available, or create dummy."""
    brief_file = Path(__file__).resolve().parent.parent / "evals" / "brief_DEC-001.json"
    if brief_file.is_file():
        with open(brief_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return DecisionBrief.model_validate(data)
    return DecisionBrief(
        decision_id="DEC-001",
        question="Sample decision question",
        options={"A": "Option A", "B": "Option B"},
        constraints=["Constraint 1"],
        criteria=[],
        context="Sample context",
    )


def main():
    parser = argparse.ArgumentParser(description="Test Firestore ledger functionality.")
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the test run in Firestore instead of deleting it after verification.",
    )
    parser.add_argument(
        "--run-id",
        default="test-day3",
        help="Run ID to use for the test (default: test-day3).",
    )
    args = parser.parse_args()

    run_id = args.run_id
    print(f"1. Creating run '{run_id}'...")
    brief = load_sample_brief()
    create_run(run_id, brief)
    print("   Created successfully.")

    print(f"2. Updating status for '{run_id}' (1st update: routing)...")
    update_status(run_id, "routing", "Routing brief across advisers")
    print("   Updated status to 'routing'.")

    print(f"3. Updating status for '{run_id}' (2nd update: advisers)...")
    update_status(run_id, "advisers", "Advisers analyzing evidence in parallel")
    print("   Updated status to 'advisers'.")

    print(f"4. Reading run '{run_id}' back from Firestore...")
    record = get_run(run_id)
    print("\n--- Retrieved Run Document ---")
    print(json.dumps(record, indent=2, ensure_ascii=False))
    print("------------------------------\n")

    assert record.get("run_id") == run_id, f"Expected run_id={run_id}, got {record.get('run_id')}"
    assert record.get("status") == "advisers", f"Expected status='advisers', got {record.get('status')}"
    assert len(record.get("progress", [])) == 2, f"Expected 2 progress items, got {len(record.get('progress', []))}"

    if args.keep:
        print(f"Preserving run '{run_id}' in Firestore (--keep specified).")
    else:
        print(f"Cleaning up: deleting run '{run_id}' from Firestore...")
        delete_run(run_id)
        print("   Deleted successfully.")


if __name__ == "__main__":
    main()
