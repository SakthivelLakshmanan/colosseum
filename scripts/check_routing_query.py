"""Check routing query generation and embeddings caching against Firestore."""
from __future__ import annotations

import json
from pathlib import Path
import sys

# Ensure project root is in sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from core.registry import get_registry
from core.schemas import DecisionBrief
from harness.embeddings import embed
from harness.routing_query import make_routing_query


def main():
    brief_file = root_dir / "evals" / "brief_DEC-001.json"
    if not brief_file.is_file():
        raise FileNotFoundError(f"Brief file not found: {brief_file}")

    with open(brief_file, "r", encoding="utf-8") as f:
        brief_data = json.load(f)
    brief = DecisionBrief.model_validate(brief_data)

    print("=" * 80)
    print(f"CHECK ROUTING QUERY FOR DECISION: {brief.decision_id}")
    print("=" * 80)

    # 1. Generate routing query
    print("\n[1] Generating routing query via Gemini Flash...")
    routing_query = make_routing_query(brief)
    print("\n--- Routing Query (Topic Lines) ---")
    print(routing_query)
    print("-----------------------------------")

    # 2. Embed the routing query with task_type RETRIEVAL_QUERY
    print("\n[2] Embedding routing query (RETRIEVAL_QUERY)...")
    query_vectors = embed([routing_query], task_type="RETRIEVAL_QUERY")
    query_vector_len = len(query_vectors[0]) if query_vectors else 0
    print(f"Routing query vector length: {query_vector_len}")

    # 3. Load all adviser capability statements and embed with RETRIEVAL_DOCUMENT
    print("\n[3] Collecting adviser capability statements...")
    registry = get_registry()
    capability_statements: list[str] = []
    for adv in registry.list_enabled():
        capability_statements.extend(adv.capabilities)

    print(f"Total capability statements collected: {len(capability_statements)}")

    print("\n[4] Embedding capability statements (RETRIEVAL_DOCUMENT)...")
    cap_vectors = embed(capability_statements, task_type="RETRIEVAL_DOCUMENT")
    cap_vector_len = len(cap_vectors[0]) if cap_vectors else 0
    print(f"Capability vector length: {cap_vector_len}")

    print("\n" + "=" * 80)
    print("CHECK COMPLETED")
    print("=" * 80)


if __name__ == "__main__":
    main()
