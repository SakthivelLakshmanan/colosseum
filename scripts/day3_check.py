"""Day 3 check script for Colosseum knowledge & tool layers."""
from __future__ import annotations

from pathlib import Path
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.tools import calculate, get_facts, query_table, search_documents
from core.registry import get_registry


def main():
    print("=" * 70)
    print("COLOSSEUM — DAY 3 VERIFICATION")
    print("=" * 70)

    # 1. The six adviser names
    registry = get_registry()
    adviser_names = [a.name for a in registry.advisers]
    print("\n1. Six Registered Advisers:")
    for i, name in enumerate(adviser_names, 1):
        print(f"   {i}. {name}")

    # 2. Top 5 finance facts for 'fees saving'
    print("\n2. Top 5 Finance Facts for 'fees saving':")
    facts = get_facts("fees saving", domains=["finance"], top_k=5)
    for i, f in enumerate(facts, 1):
        print(f"   {i}. [{f['fact_id']}] ({f['reliability'].upper()}) {f['statement']}")
        if f.get("value_num") is not None:
            print(f"      Metric: {f['metric']} = {f['value_num']} {f['unit']}")

    # 3. Success rate by bank
    print("\n3. Success Rate by Bank:")
    bank_rates = query_table("success_rate_by_bank")
    for r in bank_rates:
        print(f"   * {r['gateway']} via {r['issuer_bank']}: {r['success_pct']}% (Total: {r['txns']:,} txns)")

    # 4. First document hit for 'Arvanta early exit fee'
    print("\n4. First Document Hit for 'Arvanta early exit fee':")
    hits = search_documents("Arvanta early exit fee", top_k=1)
    if hits:
        hit = hits[0]
        print(f"   * Document Reference: {hit['doc_ref']}")
        print(f"     Document Title:     {hit['title']}")
        print("     Snippet Excerpt:")
        snippet_lines = hit["snippet"].strip().split("\n")
        for line in snippet_lines[:4]:
            print(f"       {line}")
        if len(snippet_lines) > 4:
            print("       ...")
    else:
        print("   No document hit returned.")

    # 5. calculate('(40+25)/10.4')
    calc_expr = "(40+25)/10.4"
    calc_result = calculate(calc_expr)
    print(f"\n5. Calculation result for '{calc_expr}':")
    print(f"   {calc_result}")

    print("\n" + "=" * 70)
    print("DAY 3 VERIFICATION COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
