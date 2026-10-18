"""Fact set aggregation and compression for large evidence packs."""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from core.gemini import generate, generate_content, get_genai_client
from core.ledger import get_db
from core.schemas import EvidencePack, Fact, TopicSummary
from core.settings import get_settings

logger = logging.getLogger(__name__)


def compute_summary_cache_key(adviser_id: str, topic: str, fact_ids: list[str]) -> str:
    """Compute cache key from adviser + topic + hash of fact_ids."""
    sorted_ids = ",".join(sorted(fact_ids))
    ids_hash = hashlib.sha256(sorted_ids.encode("utf-8")).hexdigest()[:16]
    clean_topic = re.sub(r"[^a-zA-Z0-9_-]", "_", topic.strip().lower())
    clean_adviser = re.sub(r"[^a-zA-Z0-9_-]", "_", adviser_id.strip().lower())
    return f"{clean_adviser}_{clean_topic}_{ids_hash}"


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


def build_code_fallback_summary(topic: str, facts: list[Fact]) -> str:
    """Build a deterministic summary in code when LLM call fails.
    
    Contains:
    - the topic name
    - the number of facts
    - the latest value for each entity and metric with its fact_id
    - at most 8 lines total
    Never raises.
    """
    try:
        if not facts:
            return f"{topic} (0 facts)"

        # Group by (entity, metric) and keep the latest fact
        latest_map: dict[tuple[str, str], Fact] = {}
        for f in facts:
            key = (str(f.entity or "").strip().lower(), str(f.metric or "").strip().lower())
            if key not in latest_map:
                latest_map[key] = f
            else:
                existing = latest_map[key]
                f_rel = getattr(f.reliability, "value", str(f.reliability))
                ex_rel = getattr(existing.reliability, "value", str(existing.reliability))
                if (f.as_of, f_rel) > (existing.as_of, ex_rel):
                    latest_map[key] = f

        # Topic name and number of facts
        lines: list[str] = [f"{topic} ({len(facts)} facts):"]

        # Sort items cleanly
        sorted_items = sorted(
            latest_map.values(),
            key=lambda x: (str(x.entity or "").lower(), str(x.metric or "").lower()),
        )

        for item in sorted_items[:7]:
            val = item.value_num if item.value_num is not None else (item.value_text or "")
            unit_str = f" {item.unit}" if item.unit else ""
            ent_str = f"{item.entity}: " if item.entity else ""
            metric_str = f"{item.metric} = " if item.metric else ""
            val_str = f"{val}{unit_str}".strip()
            if not val_str and item.statement:
                val_str = item.statement[:40]
            lines.append(f"- {item.fact_id}: {ent_str}{metric_str}{val_str}".strip())

        return "\n".join(lines[:8])
    except Exception as exc:
        logger.warning("Error building code fallback summary: %s", exc)
        fact_ids = [f.fact_id for f in facts]
        return f"{topic} ({len(facts)} facts): {', '.join(fact_ids[:5])}"


def summarize_topic_facts(
    topic: str,
    facts: list[Fact],
    adviser_id: str = "aggregator",
) -> TopicSummary:
    """Generate a summary for facts under topic using adviser model, or code fallback.
    
    Routes through shared Gemini wrapper with 30s timeout and 1 retry.
    Returns TopicSummary with summary_source='llm' or 'code'.
    Never raises.
    """
    settings = get_settings()
    caller_name = f"aggregator_{adviser_id}" if adviser_id != "aggregator" else "aggregator"
    client = get_genai_client(caller=caller_name)

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
        response = generate(
            model=settings.models.adviser,
            contents=prompt,
            config={"temperature": 0.0},
            caller=caller_name,
            timeout_s=30.0,
            max_retries=1,
        )
        raw_text = response if isinstance(response, str) else (getattr(response, "text", "") or str(response))
        raw_text = raw_text.strip()
        first_line = raw_text.splitlines()[0].strip() if raw_text else ""
        first_line = re.sub(r"^```[^\n]*", "", first_line).strip()

        # Enforce that topic and all fact IDs are present in the summary line
        if not first_line.startswith(f"{topic} (") or not all(fid in first_line for fid in fact_ids):
            content = first_line.split(":", 1)[1].strip() if ":" in first_line else first_line
            first_line = f"{topic} ({fact_ids_str}): {content}"

        return TopicSummary(
            topic=topic,
            summary=first_line,
            fact_ids=fact_ids,
            summary_source="llm",
        )
    except Exception as exc:
        logger.warning("Error calling LLM for topic summary (%s): %s", topic, exc)
        print(f"[summaries] fallback=code topic={topic}", flush=True)
        code_summary = build_code_fallback_summary(topic, facts)
        return TopicSummary(
            topic=topic,
            summary=code_summary,
            fact_ids=fact_ids,
            summary_source="code",
        )


def aggregate_facts(
    adviser_id: str,
    ranked_facts: list[Fact],
    max_facts: int = 25,
    total_matching: int | None = None,
) -> EvidencePack:
    """Aggregate a ranked list of candidate facts exceeding max_facts.
    
    1. Keeps only the latest fact per (entity, metric).
    2. Puts the top max_facts in pack.facts.
    3. Groups the remaining facts by topic and produces one summary per topic.
    4. Caches LLM summaries in Firestore (collection cache_summaries) keyed by adviser + topic + hash of fact_ids.
       Code fallback summaries are NOT cached.
    5. Sets total_matching_facts and truncated flag.
    """
    total_count = total_matching if total_matching is not None else len(ranked_facts)

    # 1. Deduplicate latest per (entity, metric)
    deduped = deduplicate_latest_per_metric(ranked_facts)

    # 2. Top max_facts
    top_facts = deduped[:max_facts]
    overflow_facts = deduped[max_facts:]

    # 3. Summaries for overflow facts with Firestore caching
    summaries: list[TopicSummary] = []
    if overflow_facts:
        topics: dict[str, list[Fact]] = {}
        for f in overflow_facts:
            topic_name = f.topic.strip() if f.topic else "General"
            topics.setdefault(topic_name, []).append(f)

        cache_hits = 0
        cache_misses = 0
        col = None
        try:
            db = get_db()
            col_name = os.environ.get("COLOSSEUM_CACHE_SUMMARIES_COLLECTION") or f"{os.environ.get('COLOSSEUM_CACHE_PREFIX', '')}cache_summaries"
            col = db.collection(col_name)
        except Exception as exc:
            logger.warning("Could not connect to Firestore summary cache: %s", exc)

        for topic_name, topic_facts in topics.items():
            fact_ids = [f.fact_id for f in topic_facts]
            key = compute_summary_cache_key(adviser_id, topic_name, fact_ids)
            cached_summary = None

            if col is not None:
                try:
                    doc = col.document(key).get()
                    if doc.exists:
                        data = doc.to_dict() or {}
                        cached_summary = data.get("summary")
                except Exception as exc:
                    logger.warning("Firestore summary cache read error: %s", exc)

            if cached_summary:
                cache_hits += 1
                summaries.append(
                    TopicSummary(
                        topic=topic_name,
                        summary=cached_summary,
                        fact_ids=fact_ids,
                        summary_source="llm",
                    )
                )
            else:
                cache_misses += 1
                summary_obj = summarize_topic_facts(
                    topic_name, topic_facts, adviser_id=adviser_id
                )
                summaries.append(summary_obj)
                # Cache only LLM summaries
                if summary_obj.summary_source == "llm" and col is not None:
                    try:
                        col.document(key).set({
                            "adviser_id": adviser_id,
                            "topic": topic_name,
                            "fact_ids": sorted(fact_ids),
                            "summary": summary_obj.summary,
                            "summary_source": "llm",
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        })
                    except Exception as exc:
                        logger.warning("Firestore summary cache write error: %s", exc)

        logger.info("Summaries (%s): %d cache hit(s), %d cache miss(es)", adviser_id, cache_hits, cache_misses)
        print(f"[summaries] Cache check ({adviser_id}): {cache_hits} cache hit(s), {cache_misses} cache miss(es)")

    truncated = len(deduped) > max_facts or total_count > len(top_facts)

    return EvidencePack(
        adviser_id=adviser_id,
        facts=top_facts,
        summaries=summaries,
        total_matching_facts=total_count,
        truncated=truncated,
    )
