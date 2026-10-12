"""Check adviser routing decision and print formatted score table."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# Ensure project root is in sys.path
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from core.schemas import DecisionBrief
from harness.router import route_decision


def main():
    parser = argparse.ArgumentParser(description="Check routing decision for a brief")
    parser.add_argument(
        "brief_path",
        nargs="?",
        default="evals/brief_DEC-001.json",
        help="Path to brief JSON file (default: evals/brief_DEC-001.json)",
    )
    parser.add_argument(
        "--text",
        type=str,
        default=None,
        help="Brief question / text to route directly",
    )
    args = parser.parse_args()

    if args.text:
        print("=" * 115)
        print(f"CHECK ROUTING DECISION FOR TEXT: \"{args.text}\"")
        print("=" * 115)
        decision = route_decision(args.text)
    else:
        file_path = Path(args.brief_path)
        if not file_path.is_file():
            # Try relative to project root
            file_path = root_dir / args.brief_path
        if not file_path.is_file():
            raise FileNotFoundError(f"Brief file not found: {args.brief_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            brief_data = json.load(f)
        brief = DecisionBrief.model_validate(brief_data)

        print("=" * 115)
        print(f"CHECK ROUTING DECISION: {brief.decision_id} — {brief.question}")
        print("=" * 115)
        decision = route_decision(brief)

    # Print summary table
    print(f"\nRouting query generated:\n{decision.routing_query}\n")
    print("-" * 115)
    print(f"{'adviser':<10} | {'score':<6} | {'best matching capability':<60} | {'selected':<8} | reason")
    print("-" * 115)
    for s in decision.scores:
        sel_str = "True" if s.selected else "False"
        cap_str = s.best_matching_capability
        if len(cap_str) > 60:
            cap_str = cap_str[:57] + "..."
        print(f"{s.adviser_id:<10} | {s.similarity:.4f} | {cap_str:<60} | {sel_str:<8} | {s.reason}")
    print("-" * 115)
    print(f"\nSelected advisers ({len(decision.selected)}): {decision.selected}\n")


if __name__ == "__main__":
    main()
