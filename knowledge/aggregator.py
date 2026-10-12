"""Fact set aggregation and compression for large evidence packs."""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from google import genai

from core.schemas import EvidencePack, Fact
from core.settings import get_settings

logger = logging.getLogger(__name__)

_genai_client: genai.Client | None = None


def get_genai_client() -> genai.Client:
    """Initialize or return the google-genai Vertex AI client."""
    global _genai_client
    if _genai_client is None:
        settings = get_settings()
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
        _genai_client = genai.Client(
            vertexai=True,
            project=settings.project_id,
            location=settings.models_location,
        )
    return _genai_client


def deduplicate_latest_per_metric(facts: list[Fact]) -> list[Fact]:
    """Keep only the latest fact per (entity, metric).
    
    If facts share non-empty entity and metric, retains only the fact with the
    latest as_of date (tie-breaking by higher reliability). Preserves relative rank order.
    """
    latest_by_key: dict[tuple[str, str], Fact] = {}
    for f in facts:
        if f.entity and f.metric:
            key = (f.entity.strip().lower(), f.metric.strip().lower())
            if key not in latest_by_key:
                latest_by_key[key] = f
            else:
                existing = latest_by_key[key]
                if (f.as_of, f.reliability.value) > (existing.as_of, existing.reliability.value):
                    latest_by_key[key] = f

    deduped: list[Fact] = []
    seen_keys: set[tuple[str, str]] = set()
    for f in facts:
        if f.entity and f.metric:
            key = (f.entity.strip().lower(), f.metric.strip().lower())
            if latest_by_key[key].fact_id == f.fact_id and key not in seen_keys:
                deduped.append(f)
                seen_keys.add(key)
        else:
            deduped.append(f)
    return deduped


def summarize_topic_facts(topic: str, facts: list[Fact]) -> str:
    """Generate a single summary line for a group of overflow facts under topic using the adviser model."""
    settings = get_settings()
    client = get_genai_client()

    fact_ids = [f.fact_id for f in facts]
    fact_ids_str = ", ".join(fact_ids)

    fact_descriptions = "\n".join(
        f"- {f.fact_id}: {f.statement} (value: {f.value_num if f.value_num is not None else f.value_text} {f.unit}, as of {f.as_of})"
        for f in facts
    )

    prompt = f"""You are an evidence aggregator for Colosseum.
Summarize the following background facts for the topic "{topic}" into exactly ONE single concise line.
The summary MUST start with: "{topic} ({fact_ids_str}): " followed by semicolon-separated key metrics and facts.

Facts to summarize:
{fact_descriptions}

Output ONLY the single summary line:"""

    try:
        response = client.models.generate_content(
            model=settings.models.adviser,
            contents=prompt,
            config={"temperature": 0.0},
        )
        raw_text = (response.text or "").strip()
        first_line = raw_text.splitlines()[0].strip() if raw_text else ""
        first_line = re.sub(r"^```[^\n]*", "", first_line).strip()

        # Enforce that topic and all fact IDs are present in the summary line
        if not first_line.startswith(f"{topic} (") or not all(fid in first_line for fid in fact_ids):
            content = first_line.split(":", 1)[1].strip() if ":" in first_line else first_line
            return f"{topic} ({fact_ids_str}): {content}"
        return first_line
    except Exception as exc:
        logger.warning(f"Error calling LLM for topic summary: {exc}")
        items = [f"{f.entity} {f.metric} {f.value_num or f.value_text or ''} {f.unit}".strip() for f in facts]
        return f"{topic} ({fact_ids_str}): {'; '.join(items)}"


def aggregate_facts(
    adviser_id: str,
    ranked_facts: list[Fact],
    max_facts: int = 25,
    total_matching: int | None = None,
) -> EvidencePack:
    """Aggregate a ranked list of candidate facts exceeding max_facts.
    
    1. Keeps only the latest fact per (entity, metric).
    2. Puts the top max_facts in pack.facts.
    3. Groups the remaining facts by topic and produces one summary line per topic.
    4. Sets total_matching_facts and truncated flag.
    """
    total_count = total_matching if total_matching is not None else len(ranked_facts)

    # 1. Deduplicate latest per (entity, metric)
    deduped = deduplicate_latest_per_metric(ranked_facts)

    # 2. Top max_facts
    top_facts = deduped[:max_facts]
    overflow_facts = deduped[max_facts:]

    # 3. Summaries for overflow facts
    summaries: list[str] = []
    if overflow_facts:
        topics: dict[str, list[Fact]] = {}
        for f in overflow_facts:
            topic_name = f.topic.strip() if f.topic else "General"
            topics.setdefault(topic_name, []).append(f)

        for topic_name, topic_facts in topics.items():
            summary_line = summarize_topic_facts(topic_name, topic_facts)
            summaries.append(summary_line)

    truncated = len(deduped) > max_facts or total_count > len(top_facts)

    return EvidencePack(
        adviser_id=adviser_id,
        facts=top_facts,
        summaries=summaries,
        total_matching_facts=total_count,
        truncated=truncated,
    )
