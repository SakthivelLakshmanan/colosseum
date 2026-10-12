"""Build bounded evidence packs for each selected adviser."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.registry import find_project_root
from core.schemas import DecisionBrief, EvidencePack
from core.settings import get_settings
from harness.routing_query import make_routing_query
from knowledge.aggregator import aggregate_facts
from knowledge.fact_store import load_facts, rank_facts


def _extract_entity_names(brief: DecisionBrief) -> list[str]:
    """Extract named entities from the brief for use as ranking boosters.

    Returns every token that starts with an uppercase letter and is at least
    4 characters long, found in the question and options text.  This reliably
    picks up company / product names (e.g. "Arvanta", "Brevis", "Legion")
    without requiring a dedicated NER step.
    """
    combined = brief.question + " " + " ".join(brief.options.values())
    tokens = re.findall(r"[A-Z][a-zA-Z0-9]{3,}", combined)
    # Deduplicate while preserving order
    seen: set[str] = set()
    entities: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            entities.append(t)
    return entities


def build_pack(
    adviser_id: str,
    brief: DecisionBrief | dict[str, Any] | None = None,
    routing_query: str = "",
    max_facts: int | None = None,
) -> EvidencePack:
    """Build an evidence pack for an adviser.
    
    1. Loads candidates using load_facts([adviser_id]) (includes related domains, drops superseded).
    2. Ranks candidates with rank_facts using brief question + options + routing_query as query text.
       Facts whose statement/topic/metric mention a principal named in the brief (e.g. Arvanta,
       Brevis, Legion) are boosted to the top tier so they always outrank generic background facts.
    3. If len(candidates) <= max_facts: returns all facts directly.
    4. Otherwise triggers the aggregator to deduplicate latest per metric, keep top max_facts,
       and summarize the rest by topic.
    """
    settings = get_settings()
    if max_facts is None:
        max_facts = settings.retrieval.max_facts_per_adviser

    if brief is None:
        root = find_project_root()
        brief_file = root / "evals" / "brief_DEC-001.json"
        with open(brief_file, "r", encoding="utf-8") as f:
            brief = DecisionBrief.model_validate(json.load(f))
    elif isinstance(brief, dict):
        brief = DecisionBrief.model_validate(brief)

    # 1. Load candidates (includes related domains, excludes superseded)
    candidates = load_facts([adviser_id], include_superseded=False)
    total_matching = len(candidates)

    # 2. Build ranking query text: question + options + routing query
    options_text = " ".join(brief.options.values()) if brief.options else ""
    query_parts = [brief.question, options_text]
    if routing_query:
        query_parts.append(routing_query)
    query_text = " ".join(part for part in query_parts if part)

    # Extract entity names from the brief (e.g. "Arvanta", "Brevis", "Legion")
    entity_names = _extract_entity_names(brief)

    # Rank all candidate facts, boosting entity-named facts to the top tier
    ranked_facts = rank_facts(candidates, query_text, top_k=None, entity_names=entity_names)

    # 3. If <= max_facts, return directly
    if len(ranked_facts) <= max_facts:
        return EvidencePack(
            adviser_id=adviser_id,
            facts=ranked_facts,
            summaries=[],
            total_matching_facts=total_matching,
            truncated=False,
        )

    # 4. Otherwise call aggregator
    return aggregate_facts(
        adviser_id=adviser_id,
        ranked_facts=ranked_facts,
        max_facts=max_facts,
        total_matching=total_matching,
    )

