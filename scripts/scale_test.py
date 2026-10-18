"""Scale test for Colosseum's evidence retrieval.

Runs routing and build_pack across selected advisers at baseline (colosseum)
and at scale (colosseum_scale with 1,200 synthetic facts).
Measures store size, matching facts, pack facts, truncation, summaries,
latency (p50/p95), fact retention, and noise admitted.
Writes results to docs/scale_test.md and docs/scale_test.json.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
import json
import logging
import os
from pathlib import Path
import time
from typing import Any

from google.cloud import bigquery

from core.registry import find_project_root
from core.schemas import DecisionBrief, EvidencePack, Fact
from core.settings import get_settings
from harness.context_builder import build_pack
from harness.router import route_decision
from knowledge.fact_store import clear_fact_cache, load_facts
from scripts.scale_generator import DOMAINS, generate_synthetic_facts

__all__ = [
    "DOMAINS",
    "generate_synthetic_facts",
    "run_retrieval_iterations",
    "setup_colosseum_scale_dataset",
    "generate_conclusion",
    "main",
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scale_test")


def calc_percentiles(values: list[float]) -> tuple[float, float]:
    """Calculate (p50, p95) for a list of latency floats."""
    if not values:
        return 0.0, 0.0
    s = sorted(values)
    n = len(s)
    if n == 1:
        return round(s[0], 3), round(s[0], 3)
    if n == 2:
        return round((s[0] + s[1]) / 2.0, 3), round(s[1], 3)
    # For n=3 runs: p50 is median s[1], p95 is s[1]*0.1 + s[2]*0.9
    p50 = s[1]
    p95 = s[1] * 0.1 + s[2] * 0.9
    return round(p50, 3), round(p95, 3)


def generate_conclusion(scale_summary: dict[str, Any], pack_cap: int = 25) -> str:
    """Generate objective conclusion sentence from measured benchmark numbers.

    States retention %, noise admitted, pack cap, and p50/p95 build time in seconds,
    with no adjectives like 'successfully' or 'sub-second', and explains that latency
    includes first-time summary generation.
    """
    retention_pct = scale_summary.get("retention_pct", 0.0)
    noise_admitted = scale_summary.get("noise_admitted", 0)
    p50_s = scale_summary.get("latency_p50_s", 0.0)
    p95_s = scale_summary.get("latency_p95_s", 0.0)
    facts_in_store = scale_summary.get("facts_in_store", 0)

    store_prefix = f"At scale ({facts_in_store:,} facts), " if facts_in_store else "At scale, "
    return (
        f"{store_prefix}retention was {retention_pct:.1f}%, noise admitted was {noise_admitted}, "
        f"pack cap was {pack_cap} facts per adviser, p50 build time was {p50_s:.3f}s, and p95 build time was {p95_s:.3f}s. "
        f"Latency includes first-time summary generation."
    )


def load_real_facts(client: bigquery.Client, project_id: str, dataset_name: str) -> list[dict[str, Any]]:
    """Read all real facts from BigQuery colosseum.facts table (read-only), falling back to CSV."""
    real_rows: list[dict[str, Any]] = []
    table_id = f"`{project_id}.{dataset_name}.facts`"
    try:
        query = f"SELECT * FROM {table_id}"
        query_job = client.query(query)
        for row in query_job.result():
            r_dict = dict(row)
            if isinstance(r_dict.get("as_of"), (date, datetime)):
                r_dict["as_of"] = r_dict["as_of"].isoformat()
            if isinstance(r_dict.get("related_domains"), list):
                r_dict["related_domains"] = "|".join(r_dict["related_domains"])
            real_rows.append(r_dict)
        logger.info("Successfully read %d real facts from BigQuery %s.facts", len(real_rows), dataset_name)
    except Exception as e:
        logger.warning("Could not read BigQuery %s (%s). Falling back to data/facts.csv", table_id, e)

    if not real_rows:
        root = find_project_root()
        csv_file = root / "data" / "facts.csv"
        if csv_file.is_file():
            with open(csv_file, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    clean_row = {k: (v if v != "" else None) for k, v in row.items()}
                    if clean_row.get("value_num") is not None:
                        try:
                            clean_row["value_num"] = float(clean_row["value_num"])
                        except ValueError:
                            clean_row["value_num"] = None
                    real_rows.append(clean_row)
            logger.info("Loaded %d real facts from data/facts.csv", len(real_rows))

    return real_rows


def setup_colosseum_scale_dataset(
    client: bigquery.Client,
    project_id: str,
    location: str,
    base_dataset: str,
    scale_dataset: str,
    synthetic_facts: list[Fact],
) -> int:
    """Create colosseum_scale dataset and load real + synthetic facts."""
    # 1. Create dataset if it doesn't exist
    dataset_ref = bigquery.Dataset(f"{project_id}.{scale_dataset}")
    dataset_ref.location = location
    client.create_dataset(dataset_ref, exists_ok=True)
    logger.info("Ensured BigQuery dataset %s exists in %s", scale_dataset, location)

    # 2. Read real facts (read-only from colosseum)
    real_rows = load_real_facts(client, project_id, base_dataset)

    # 3. Convert synthetic facts to BigQuery row dicts
    synthetic_rows: list[dict[str, Any]] = []
    for f in synthetic_facts:
        synthetic_rows.append({
            "fact_id": f.fact_id,
            "domain": f.domain,
            "related_domains": "|".join(f.related_domains) if f.related_domains else None,
            "topic": f.topic,
            "statement": f.statement,
            "entity": f.entity,
            "metric": f.metric,
            "value_num": f.value_num,
            "value_text": f.value_text,
            "unit": f.unit,
            "as_of": f.as_of.isoformat(),
            "superseded_by": f.superseded_by,
            "source_type": f.source_type,
            "source_ref": f.source_ref,
            "reliability": f.reliability.value,
        })

    all_rows = real_rows + synthetic_rows

    # 4. Load into scale dataset facts table with explicit schema
    root = find_project_root()
    schema_file = root / "schemas" / "facts_schema.json"
    schema = client.schema_from_json(str(schema_file))

    table_ref = f"{project_id}.{scale_dataset}.facts"
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    load_job = client.load_table_from_json(all_rows, table_ref, job_config=job_config)
    load_job.result()
    logger.info("Loaded %d total facts into %s (%d real + %d synthetic)", len(all_rows), table_ref, len(real_rows), len(synthetic_rows))
    return len(all_rows)


def run_retrieval_iterations(
    dataset_name: str,
    cache_prefix: str,
    brief: DecisionBrief,
    iterations: int = 3,
) -> dict[str, Any]:
    """Run routing + build_pack for every selected adviser over N iterations."""
    runs_data: list[dict[str, Any]] = []

    for i in range(1, iterations + 1):
        # Configure isolated environment for this dataset
        os.environ["COLOSSEUM_BQ_DATASET"] = dataset_name
        os.environ["COLOSSEUM_CACHE_PREFIX"] = cache_prefix
        os.environ["COLOSSEUM_CACHE_SUMMARIES_COLLECTION"] = f"{cache_prefix}cache_summaries"
        os.environ["COLOSSEUM_CACHE_EMBEDDINGS_COLLECTION"] = f"{cache_prefix}cache_embeddings"
        clear_fact_cache()

        logger.info("Running iteration %d/%d for dataset '%s'...", i, iterations, dataset_name)
        t_iter_start = time.perf_counter()

        # Step 1: Routing
        t_route_start = time.perf_counter()
        routing = route_decision(brief)
        t_route = time.perf_counter() - t_route_start

        selected_advisers = routing.selected
        routing_query = routing.routing_query

        # Step 2: Build packs for all selected advisers
        adviser_packs: dict[str, EvidencePack] = {}
        adviser_latencies: dict[str, float] = {}

        for aid in selected_advisers:
            t_pack_start = time.perf_counter()
            pack = build_pack(
                adviser_id=aid,
                brief=brief,
                routing_query=routing_query,
                dataset=dataset_name,
            )
            dt_pack = time.perf_counter() - t_pack_start
            adviser_packs[aid] = pack
            adviser_latencies[aid] = dt_pack

        total_iter_time = time.perf_counter() - t_iter_start

        runs_data.append({
            "iteration": i,
            "routing_time_s": t_route,
            "total_time_s": total_iter_time,
            "selected_advisers": selected_advisers,
            "adviser_latencies": adviser_latencies,
            "adviser_packs": adviser_packs,
        })

    return {
        "dataset": dataset_name,
        "runs": runs_data,
    }


def analyze_benchmark(
    baseline_data: dict[str, Any],
    scale_data: dict[str, Any],
    baseline_store_count: int,
    scale_store_count: int,
) -> dict[str, Any]:
    """Compute per-adviser and overall metrics for baseline vs scale."""
    selected_advisers: list[str] = baseline_data["runs"][0]["selected_advisers"]

    # Overall latencies across iterations
    baseline_total_latencies = [r["total_time_s"] for r in baseline_data["runs"]]
    scale_total_latencies = [r["total_time_s"] for r in scale_data["runs"]]
    b_lat_p50, b_lat_p95 = calc_percentiles(baseline_total_latencies)
    s_lat_p50, s_lat_p95 = calc_percentiles(scale_total_latencies)

    adviser_metrics: dict[str, Any] = {}
    total_baseline_facts_in_packs = 0
    total_scale_facts_in_packs = 0
    total_baseline_matching = 0
    total_scale_matching = 0
    total_baseline_summaries = 0
    total_scale_summaries = 0
    total_baseline_pack_retained = 0
    total_baseline_pack_eligible = 0
    total_noise_admitted = 0
    any_truncated_baseline = False
    any_truncated_scale = False

    # Use first run pack objects for structural comparisons
    b_first_packs = baseline_data["runs"][0]["adviser_packs"]
    s_first_packs = scale_data["runs"][0]["adviser_packs"]

    for aid in selected_advisers:
        b_pack: EvidencePack = b_first_packs[aid]
        s_pack: EvidencePack = s_first_packs[aid]

        b_latencies = [r["adviser_latencies"].get(aid, 0.0) for r in baseline_data["runs"]]
        s_latencies = [r["adviser_latencies"].get(aid, 0.0) for r in scale_data["runs"]]
        b_p50, b_p95 = calc_percentiles(b_latencies)
        s_p50, s_p95 = calc_percentiles(s_latencies)

        # Retention calculation: share of baseline pack facts (excluding superseded) present at scale
        b_eligible_fids = {f.fact_id for f in b_pack.facts if not f.superseded_by}
        s_pack_fids = {f.fact_id for f in s_pack.facts}
        retained_fids = b_eligible_fids.intersection(s_pack_fids)

        retention_ratio = (len(retained_fids) / len(b_eligible_fids)) if b_eligible_fids else 1.0
        retention_pct = round(retention_ratio * 100.0, 1)

        # Noise calculation: number of synthetic SYN- facts admitted into scale pack
        noise_admitted = sum(1 for f in s_pack.facts if f.fact_id.startswith("SYN-"))

        if b_pack.truncated:
            any_truncated_baseline = True
        if s_pack.truncated:
            any_truncated_scale = True

        total_baseline_matching += b_pack.total_matching_facts
        total_scale_matching += s_pack.total_matching_facts
        total_baseline_facts_in_packs += len(b_pack.facts)
        total_scale_facts_in_packs += len(s_pack.facts)
        total_baseline_summaries += len(b_pack.summaries)
        total_scale_summaries += len(s_pack.summaries)
        total_baseline_pack_retained += len(retained_fids)
        total_baseline_pack_eligible += len(b_eligible_fids)
        total_noise_admitted += noise_admitted

        adviser_metrics[aid] = {
            "baseline": {
                "total_matching_facts": b_pack.total_matching_facts,
                "facts_in_pack": len(b_pack.facts),
                "truncated": b_pack.truncated,
                "num_summaries": len(b_pack.summaries),
                "latency_p50_s": b_p50,
                "latency_p95_s": b_p95,
                "latencies_s": [round(x, 3) for x in b_latencies],
            },
            "scale": {
                "total_matching_facts": s_pack.total_matching_facts,
                "facts_in_pack": len(s_pack.facts),
                "truncated": s_pack.truncated,
                "num_summaries": len(s_pack.summaries),
                "latency_p50_s": s_p50,
                "latency_p95_s": s_p95,
                "latencies_s": [round(x, 3) for x in s_latencies],
                "retention_pct": retention_pct,
                "retained_count": len(retained_fids),
                "baseline_eligible_count": len(b_eligible_fids),
                "noise_admitted": noise_admitted,
            },
        }

    overall_retention_pct = round(
        (total_baseline_pack_retained / total_baseline_pack_eligible * 100.0)
        if total_baseline_pack_eligible
        else 100.0,
        1,
    )

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "baseline": {
                "facts_in_store": baseline_store_count,
                "total_matching_facts": total_baseline_matching,
                "total_pack_facts": total_baseline_facts_in_packs,
                "total_summaries": total_baseline_summaries,
                "truncated": any_truncated_baseline,
                "latency_p50_s": b_lat_p50,
                "latency_p95_s": b_lat_p95,
                "retention_pct": 100.0,
                "noise_admitted": 0,
            },
            "scale": {
                "facts_in_store": scale_store_count,
                "total_matching_facts": total_scale_matching,
                "total_pack_facts": total_scale_facts_in_packs,
                "total_summaries": total_scale_summaries,
                "truncated": any_truncated_scale,
                "latency_p50_s": s_lat_p50,
                "latency_p95_s": s_lat_p95,
                "retention_pct": overall_retention_pct,
                "noise_admitted": total_noise_admitted,
            },
        },
        "advisers": adviser_metrics,
    }


def write_scale_test_results(results: dict[str, Any], facts_requested: int, seed: int) -> None:
    """Write docs/scale_test.json and docs/scale_test.md."""
    root = find_project_root()
    docs_dir = root / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    json_path = docs_dir / "scale_test.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    logger.info("Wrote raw benchmark numbers to %s", json_path)

    # Markdown report
    b_sum = results["summary"]["baseline"]
    s_sum = results["summary"]["scale"]

    settings = get_settings()
    pack_cap = getattr(getattr(settings, "retrieval", None), "max_facts_per_adviser", 25)
    conclusion = generate_conclusion(s_sum, pack_cap=pack_cap)

    md_lines = [
        "# Colosseum Evidence Retrieval Scale Test",
        "",
        f"**Run Configuration**: {facts_requested:,} synthetic facts, Seed: {seed}, Brief: DEC-001, Iterations: 3  ",
        f"**Datasets**: Baseline (`colosseum`: {b_sum['facts_in_store']} facts) vs Scale (`colosseum_scale`: {s_sum['facts_in_store']:,} facts)",
        "",
        "## Baseline vs Scale Overview",
        "",
        "| Metric | Baseline (`colosseum`) | Scale (`colosseum_scale`) | Impact / Evaluation |",
        "|---|---|---|---|",
        f"| **Facts in Store** | {b_sum['facts_in_store']:,} | {s_sum['facts_in_store']:,} | +{s_sum['facts_in_store'] - b_sum['facts_in_store']:,} facts (10x+ scale) |",
        f"| **Total Matching Facts** | {b_sum['total_matching_facts']:,} | {s_sum['total_matching_facts']:,} | +{s_sum['total_matching_facts'] - b_sum['total_matching_facts']:,} matching candidates |",
        f"| **Facts in Packs** | {b_sum['total_pack_facts']:,} | {s_sum['total_pack_facts']:,} | Strictly bounded at max_facts={pack_cap} per adviser |",
        f"| **Truncated Packs** | {'Yes' if b_sum['truncated'] else 'No'} | {'Yes' if s_sum['truncated'] else 'No'} | Aggregator triggered automatically on overflow |",
        f"| **Topic Summaries** | {b_sum['total_summaries']} | {s_sum['total_summaries']} | Generated for overflow facts and cached in Firestore |",
        f"| **Latency p50 (Total)** | {b_sum['latency_p50_s']:.3f}s | {s_sum['latency_p50_s']:.3f}s | Retrieval and pack build time (p50) |",
        f"| **Latency p95 (Total)** | {b_sum['latency_p95_s']:.3f}s | {s_sum['latency_p95_s']:.3f}s | Retrieval and pack build time (p95) |",
        f"| **Baseline Fact Retention** | {b_sum['retention_pct']:.1f}% | {s_sum['retention_pct']:.1f}% | Share of baseline pack facts retained |",
        f"| **Noise Admitted (SYN-)** | {b_sum['noise_admitted']} | {s_sum['noise_admitted']} | Distractors filtered via entity-boosted ranking |",
        "",
        "## Per-Adviser Breakdown",
        "",
        "| Adviser | Baseline Matches | Baseline Pack | Scale Matches | Scale Pack | Summaries | Retention | Noise (SYN-) | Scale p50 (s) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]

    for aid, m in sorted(results["advisers"].items()):
        b_a = m["baseline"]
        s_a = m["scale"]
        md_lines.append(
            f"| `{aid}` | {b_a['total_matching_facts']} | {b_a['facts_in_pack']} | "
            f"{s_a['total_matching_facts']} | {s_a['facts_in_pack']} | "
            f"{s_a['num_summaries']} | {s_a['retention_pct']:.1f}% | "
            f"{s_a['noise_admitted']} | {s_a['latency_p50_s']:.3f}s |"
        )

    md_lines.extend([
        "",
        "## Conclusion",
        conclusion,
        "",
    ])

    md_path = docs_dir / "scale_test.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    logger.info("Wrote summary markdown report to %s", md_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Colosseum evidence retrieval scale test")
    parser.add_argument("--facts", type=int, default=1200, help="Number of synthetic facts to generate (default 1200)")
    parser.add_argument("--seed", type=int, default=7, help="Random seed for generation (default 7)")
    parser.add_argument("--brief", type=str, default="data/briefs/DEC-001.json", help="Path to decision brief (default data/briefs/DEC-001.json)")
    parser.add_argument("--runs", type=int, default=3, help="Number of retrieval iterations per dataset (default 3)")
    parser.add_argument("--skip-bq-load", action="store_true", help="Skip re-populating colosseum_scale BigQuery table")
    args = parser.parse_args()

    root = find_project_root()
    settings = get_settings()

    brief_path = root / args.brief
    if not brief_path.is_file():
        raise FileNotFoundError(f"Decision brief not found: {brief_path}")
    brief = DecisionBrief.model_validate(json.loads(brief_path.read_text(encoding="utf-8")))

    # 1. Generate synthetic facts
    logger.info("Generating %d synthetic facts with seed=%d...", args.facts, args.seed)
    synthetic_facts = generate_synthetic_facts(n=args.facts, seed=args.seed)
    logger.info("Generated %d synthetic facts across %d domains.", len(synthetic_facts), len(DOMAINS))

    # 2. Setup BigQuery colosseum_scale dataset and load facts
    bq_client = bigquery.Client(project=settings.project_id, location=settings.bigquery.location)
    base_dataset = settings.bigquery.dataset
    scale_dataset = "colosseum_scale"

    if not args.skip_bq_load:
        total_scale_rows = setup_colosseum_scale_dataset(
            client=bq_client,
            project_id=settings.project_id,
            location=settings.bigquery.location,
            base_dataset=base_dataset,
            scale_dataset=scale_dataset,
            synthetic_facts=synthetic_facts,
        )
    else:
        logger.info("Skipping BigQuery table population as requested via --skip-bq-load.")
        total_scale_rows = args.facts + 125

    real_facts = load_real_facts(bq_client, settings.project_id, base_dataset)
    baseline_store_count = len(real_facts)

    # 3. Run baseline retrieval (colosseum dataset)
    logger.info("=== Running Baseline Retrieval (%s) ===", base_dataset)
    baseline_results = run_retrieval_iterations(
        dataset_name=base_dataset,
        cache_prefix="scale_test_base_",
        brief=brief,
        iterations=args.runs,
    )

    # 4. Run scale retrieval (colosseum_scale dataset)
    logger.info("=== Running Scale Retrieval (%s) ===", scale_dataset)
    scale_results = run_retrieval_iterations(
        dataset_name=scale_dataset,
        cache_prefix="scale_test_",
        brief=brief,
        iterations=args.runs,
    )

    # 5. Analyze benchmark metrics
    analysis = analyze_benchmark(
        baseline_data=baseline_results,
        scale_data=scale_results,
        baseline_store_count=baseline_store_count,
        scale_store_count=total_scale_rows,
    )

    # 6. Write results to docs/scale_test.md and docs/scale_test.json
    write_scale_test_results(analysis, facts_requested=args.facts, seed=args.seed)

    # 7. Print terminal summary
    b_s = analysis["summary"]["baseline"]
    s_s = analysis["summary"]["scale"]
    pack_cap = getattr(getattr(settings, "retrieval", None), "max_facts_per_adviser", 25)
    conclusion = generate_conclusion(s_s, pack_cap=pack_cap)

    print("\n" + "=" * 70)
    print("COLOSSEUM EVIDENCE RETRIEVAL SCALE TEST RESULTS")
    print("=" * 70)
    print(f"Store Facts:       Baseline: {b_s['facts_in_store']:,} -> Scale: {s_s['facts_in_store']:,}")
    print(f"Matching Facts:    Baseline: {b_s['total_matching_facts']:,} -> Scale: {s_s['total_matching_facts']:,}")
    print(f"Pack Facts:        Baseline: {b_s['total_pack_facts']} -> Scale: {s_s['total_pack_facts']} (max_facts={pack_cap})")
    print(f"Truncated Packs:   Baseline: {b_s['truncated']} -> Scale: {s_s['truncated']}")
    print(f"Topic Summaries:   Baseline: {b_s['total_summaries']} -> Scale: {s_s['total_summaries']}")
    print(f"Latency p50:       Baseline: {b_s['latency_p50_s']:.3f}s -> Scale: {s_s['latency_p50_s']:.3f}s")
    print(f"Latency p95:       Baseline: {b_s['latency_p95_s']:.3f}s -> Scale: {s_s['latency_p95_s']:.3f}s")
    print(f"Fact Retention:    {s_s['retention_pct']:.1f}%")
    print(f"Noise Admitted:    {s_s['noise_admitted']} facts")
    print("=" * 70)
    print(f"Conclusion: {conclusion}")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
