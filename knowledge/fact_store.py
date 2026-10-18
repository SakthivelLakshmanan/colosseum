"""Fact store reader and ranker backed by BigQuery."""
from __future__ import annotations

from datetime import date
import os
import re
from typing import Any, Optional

from google.cloud import bigquery

from core.schemas import Fact, Reliability
from core.settings import get_settings

_client: Optional[bigquery.Client] = None


def get_client() -> bigquery.Client:
    """Get or initialize BigQuery client."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = bigquery.Client(
            project=settings.project_id,
            location=settings.bigquery.location,
        )
    return _client


def _row_to_fact(row: Any) -> Fact:
    """Convert a BigQuery row or dict to a Fact schema object."""
    # Convert Row / dict access
    def get_val(key: str) -> Any:
        if isinstance(row, dict):
            return row.get(key)
        return getattr(row, key, None) if hasattr(row, key) else row.get(key)

    rel_domains_raw = get_val("related_domains")
    if rel_domains_raw:
        related_domains = [d.strip() for d in str(rel_domains_raw).split("|") if d.strip()]
    else:
        related_domains = []

    as_of = get_val("as_of")
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)

    rel_str = str(get_val("reliability") or "high").lower().strip()
    try:
        reliability = Reliability(rel_str)
    except ValueError:
        reliability = Reliability.high

    def _clean_str(val: Any) -> Optional[str]:
        if val is None:
            return None
        s = str(val).strip()
        return s if s else None

    val_num = get_val("value_num")
    if val_num is not None:
        try:
            val_num = float(val_num)
        except (ValueError, TypeError):
            val_num = None

    return Fact(
        fact_id=str(get_val("fact_id")),
        domain=str(get_val("domain")),
        related_domains=related_domains,
        topic=str(get_val("topic") or ""),
        statement=str(get_val("statement") or ""),
        entity=str(get_val("entity") or ""),
        metric=str(get_val("metric") or ""),
        value_num=val_num,
        value_text=_clean_str(get_val("value_text")),
        unit=str(get_val("unit") or ""),
        as_of=as_of,
        superseded_by=_clean_str(get_val("superseded_by")),
        source_type=str(get_val("source_type") or "table"),
        source_ref=str(get_val("source_ref") or ""),
        reliability=reliability,
    )


def clear_fact_cache() -> None:
    """Clear the in-memory fact cache."""
    global _fact_cache, _all_facts_loaded
    _fact_cache.clear()
    _all_facts_loaded = False


def load_facts(
    domains: list[str],
    include_superseded: bool = False,
    dataset: str | None = None,
) -> list[Fact]:
    """Return facts whose domain is in domains OR whose pipe-separated related_domains contains any of them.

    Unless include_superseded is True, excludes rows where superseded_by is not null and not empty.
    Uses query parameters (never string formatting for values).
    """
    if not domains:
        return []

    settings = get_settings()
    client = get_client()
    target_dataset = dataset or os.environ.get("COLOSSEUM_BQ_DATASET") or settings.bigquery.dataset
    table_id = f"`{settings.project_id}.{target_dataset}.facts`"

    superseded_clause = ""
    if not include_superseded:
        superseded_clause = "AND (superseded_by IS NULL OR TRIM(superseded_by) = '')"

    query = f"""
        SELECT *
        FROM {table_id}
        WHERE (
            domain IN UNNEST(@domains)
            OR EXISTS (
                SELECT 1
                FROM UNNEST(SPLIT(related_domains, '|')) AS d
                WHERE d IN UNNEST(@domains)
            )
        )
        {superseded_clause}
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("domains", "STRING", domains),
        ]
    )

    query_job = client.query(query, job_config=job_config)
    results = query_job.result()
    return [_row_to_fact(row) for row in results]


_fact_cache: dict[str, Fact] = {}
_all_facts_loaded: bool = False


def load_all_facts(include_superseded: bool = True, dataset: str | None = None) -> list[Fact]:
    """Retrieve all facts from the fact store and populate cache."""
    global _fact_cache, _all_facts_loaded
    settings = get_settings()
    client = get_client()
    target_dataset = dataset or os.environ.get("COLOSSEUM_BQ_DATASET") or settings.bigquery.dataset
    table_id = f"`{settings.project_id}.{target_dataset}.facts`"

    superseded_clause = ""
    if not include_superseded:
        superseded_clause = "WHERE (superseded_by IS NULL OR TRIM(superseded_by) = '')"

    query = f"""
        SELECT *
        FROM {table_id}
        {superseded_clause}
    """
    try:
        query_job = client.query(query)
        facts = [_row_to_fact(row) for row in query_job.result()]
        for f in facts:
            _fact_cache[f.fact_id] = f
        _all_facts_loaded = True
        return facts
    except Exception:
        # Fallback to local CSV if BQ fails
        from pathlib import Path
        import csv
        root = Path(__file__).resolve().parent.parent
        csv_file = root / "data" / "facts.csv"
        if csv_file.is_file():
            facts = []
            with open(csv_file, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    fact = _row_to_fact(row)
                    _fact_cache[fact.fact_id] = fact
                    if include_superseded or not fact.superseded_by:
                        facts.append(fact)
            _all_facts_loaded = True
            return facts
        return []


def get_fact(fact_id: str, dataset: str | None = None) -> Fact | None:
    """Retrieve a single fact by fact_id from the fact store (with in-memory cache)."""
    if not fact_id:
        return None

    clean_id = str(fact_id).strip()
    if clean_id in _fact_cache:
        return _fact_cache[clean_id]

    if _all_facts_loaded:
        return None

    # If cache is empty, load all facts to avoid repeated network round-trips
    if not _fact_cache:
        load_all_facts(include_superseded=True, dataset=dataset)
        if clean_id in _fact_cache:
            return _fact_cache[clean_id]
        if _all_facts_loaded:
            return None

    settings = get_settings()
    client = get_client()
    target_dataset = dataset or os.environ.get("COLOSSEUM_BQ_DATASET") or settings.bigquery.dataset
    table_id = f"`{settings.project_id}.{target_dataset}.facts`"

    query = f"""
        SELECT *
        FROM {table_id}
        WHERE fact_id = @fact_id
        LIMIT 1
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("fact_id", "STRING", clean_id),
        ]
    )

    try:
        query_job = client.query(query, job_config=job_config)
        results = list(query_job.result())
        if not results:
            return None
        fact = _row_to_fact(results[0])
        _fact_cache[clean_id] = fact
        return fact
    except Exception:
        # Check local CSV fallback
        from pathlib import Path
        import csv
        root = Path(__file__).resolve().parent.parent
        csv_file = root / "data" / "facts.csv"
        if csv_file.is_file():
            with open(csv_file, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    f_obj = _row_to_fact(row)
                    _fact_cache[f_obj.fact_id] = f_obj
            return _fact_cache.get(clean_id)
        return None


def rank_facts(
    facts: list[Fact],
    query_text: str,
    top_k: int = 10,
    entity_names: list[str] | None = None,
) -> list[Fact]:
    """Pure Python keyword ranking with optional entity-name boost.

    Primary sort key: entity_hit (1 if any entity_name appears in statement/topic/metric, else 0).
    Secondary: keyword overlap score (count of query words appearing in statement + topic + metric).
    Tertiary: reliability (high > medium > low).
    Quaternary: newest as_of.

    The entity boost ensures that facts explicitly naming a principal in the brief
    (e.g. "Arvanta", "Brevis", "Legion") surface above generic background facts
    that happen to match query keywords.
    """
    words = [w.lower() for w in re.findall(r"[a-zA-Z0-9_-]+", query_text) if len(w) >= 3]
    entity_lower = [e.lower() for e in (entity_names or [])]
    reliability_order = {
        Reliability.high: 3,
        Reliability.medium: 2,
        Reliability.low: 1,
    }

    def score_fact(f: Fact) -> tuple[int, int, int, date]:
        target = f"{f.statement} {f.topic} {f.metric}".lower()

        # Entity-name hit: 1 if any brief entity is mentioned, 0 otherwise
        entity_hit = 1 if entity_lower and any(e in target for e in entity_lower) else 0

        # Keyword overlap score
        kw_score = sum(1 for w in words if w in target) if words else 0

        rel_val = reliability_order.get(f.reliability, 0)
        return (entity_hit, kw_score, rel_val, f.as_of)

    ranked = sorted(facts, key=score_fact, reverse=True)
    return ranked[:top_k] if top_k is not None else ranked
