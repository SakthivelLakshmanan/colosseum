"""Fact store reader and ranker backed by BigQuery."""
from __future__ import annotations

from datetime import date
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


def load_facts(domains: list[str], include_superseded: bool = False) -> list[Fact]:
    """Return facts whose domain is in domains OR whose pipe-separated related_domains contains any of them.

    Unless include_superseded is True, excludes rows where superseded_by is not null and not empty.
    Uses query parameters (never string formatting for values).
    """
    if not domains:
        return []

    settings = get_settings()
    client = get_client()
    table_id = f"`{settings.project_id}.{settings.bigquery.dataset}.facts`"

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


def get_fact(fact_id: str) -> Fact | None:
    """Retrieve a single fact by fact_id from the fact store."""
    if not fact_id:
        return None

    settings = get_settings()
    client = get_client()
    table_id = f"`{settings.project_id}.{settings.bigquery.dataset}.facts`"

    query = f"""
        SELECT *
        FROM {table_id}
        WHERE fact_id = @fact_id
        LIMIT 1
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("fact_id", "STRING", fact_id),
        ]
    )

    query_job = client.query(query, job_config=job_config)
    results = list(query_job.result())
    if not results:
        return None
    return _row_to_fact(results[0])


def rank_facts(facts: list[Fact], query_text: str, top_k: int = 10) -> list[Fact]:
    """Pure Python keyword ranking.

    Score = count of lowercase query words (ignoring words under 3 letters) appearing in statement + topic + metric.
    Tie-break by reliability (high > medium > low), then newest as_of.
    """
    words = [w.lower() for w in re.findall(r"[a-zA-Z0-9_-]+", query_text) if len(w) >= 3]
    reliability_order = {
        Reliability.high: 3,
        Reliability.medium: 2,
        Reliability.low: 1,
    }

    def score_fact(f: Fact) -> tuple[int, int, date]:
        if not words:
            score = 0
        else:
            target = f"{f.statement} {f.topic} {f.metric}".lower()
            score = sum(1 for w in words if w in target)
        rel_val = reliability_order.get(f.reliability, 0)
        return (score, rel_val, f.as_of)

    ranked = sorted(facts, key=score_fact, reverse=True)
    return ranked[:top_k] if top_k is not None else ranked
