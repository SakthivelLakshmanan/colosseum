"""Score and select advisers using cosine similarity and routing rules."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any
import yaml

from core.ledger import save_routing
from core.registry import find_project_root, get_registry
from core.schemas import AdviserScore, DecisionBrief, RoutingDecision
from harness.embeddings import embed
from harness.routing_query import make_routing_query


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def load_routing_config() -> dict[str, Any]:
    """Load configuration from config/routing.yaml."""
    root = find_project_root()
    routing_file = root / "config" / "routing.yaml"
    if not routing_file.is_file():
        raise FileNotFoundError(f"Routing configuration not found: {routing_file}")
    with open(routing_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _extract_brief_search_text(brief_obj: DecisionBrief) -> str:
    """Combine all brief fields into a searchable string for keyword rules."""
    parts = [
        brief_obj.question,
        brief_obj.context,
    ]
    parts.extend(brief_obj.options.values())
    parts.extend(brief_obj.constraints)
    for cr in brief_obj.criteria:
        parts.append(cr.name)
        parts.append(cr.good_looks_like)
    return " ".join(parts).lower()


def route_decision(brief: DecisionBrief | dict[str, Any] | str) -> RoutingDecision:
    """Route a decision brief to the appropriate advisers.
    
    1. Turns the brief into a routing query (topics involved) using make_routing_query.
    2. Embeds the routing query (RETRIEVAL_QUERY) and each adviser's capabilities (RETRIEVAL_DOCUMENT).
    3. Calculates adviser score = max cosine similarity across its capability statements.
    4. Selects advisers with score >= (top score - relative_margin) or >= absolute_floor.
    5. Enforces min_advisers and max_advisers limits.
    6. Applies always_include keyword rules against the brief text (case-insensitive substring match).
    7. Assigns reasons: "within margin of top", "above floor", "always-include rule: <keyword>", "below floor".
    8. Records the decision via ledger.save_routing and returns RoutingDecision.
    """
    # 1. Parse or normalize brief
    if isinstance(brief, str):
        try:
            parsed = json.loads(brief)
            brief_obj = DecisionBrief.model_validate(parsed)
        except Exception:
            brief_obj = DecisionBrief(
                decision_id="DEC-CUSTOM",
                question=brief,
                options={},
                constraints=[],
                criteria=[],
                context="",
            )
    elif isinstance(brief, dict):
        brief_obj = DecisionBrief.model_validate(brief)
    else:
        brief_obj = brief

    routing_cfg = load_routing_config()
    selection_cfg = routing_cfg.get("selection", {})
    relative_margin = float(selection_cfg.get("relative_margin", 0.06))
    absolute_floor = float(selection_cfg.get("absolute_floor", 0.55))
    min_advisers = int(selection_cfg.get("min_advisers", 2))
    max_advisers = int(selection_cfg.get("max_advisers", 6))
    always_include = routing_cfg.get("always_include", {})

    # 2. Generate and embed routing query
    routing_query = make_routing_query(brief_obj)
    query_vectors = embed([routing_query], task_type="RETRIEVAL_QUERY")
    query_vector = query_vectors[0] if query_vectors else []

    # 3. Score each enabled adviser
    registry = get_registry()
    enabled_advisers = registry.list_enabled()

    # Pre-embed capability statements for all enabled advisers
    adviser_scores_raw: dict[str, dict[str, Any]] = {}

    for adv in enabled_advisers:
        if not adv.capabilities:
            adviser_scores_raw[adv.id] = {
                "similarity": 0.0,
                "best_matching_capability": "",
            }
            continue

        cap_vectors = embed(adv.capabilities, task_type="RETRIEVAL_DOCUMENT")
        best_sim = -1.0
        best_cap = adv.capabilities[0]

        for cap_text, cv in zip(adv.capabilities, cap_vectors):
            sim = cosine_similarity(query_vector, cv)
            if sim > best_sim:
                best_sim = sim
                best_cap = cap_text

        adviser_scores_raw[adv.id] = {
            "similarity": best_sim,
            "best_matching_capability": best_cap,
        }

    # 4. Selection thresholds
    top_score = max(
        (data["similarity"] for data in adviser_scores_raw.values()),
        default=0.0,
    )
    margin_threshold = top_score - relative_margin

    adviser_status: dict[str, dict[str, Any]] = {}
    for adv in enabled_advisers:
        sim = adviser_scores_raw[adv.id]["similarity"]
        if sim >= margin_threshold:
            selected = True
            reason = "within margin of top"
        elif sim >= absolute_floor:
            selected = True
            reason = "above floor"
        else:
            selected = False
            reason = "below floor"

        adviser_status[adv.id] = {
            "selected": selected,
            "reason": reason,
            "similarity": sim,
            "best_matching_capability": adviser_scores_raw[adv.id]["best_matching_capability"],
        }

    # 5. Enforce min_advisers and max_advisers (by score descending)
    sorted_advisers = sorted(
        enabled_advisers,
        key=lambda adv: adviser_status[adv.id]["similarity"],
        reverse=True,
    )

    selected_count = sum(1 for adv in enabled_advisers if adviser_status[adv.id]["selected"])

    # If below min_advisers, pick top unselected
    if selected_count < min_advisers:
        for adv in sorted_advisers:
            if not adviser_status[adv.id]["selected"]:
                adviser_status[adv.id]["selected"] = True
                selected_count += 1
                if selected_count >= min_advisers:
                    break

    # If above max_advisers, keep top max_advisers
    if selected_count > max_advisers:
        kept = 0
        for adv in sorted_advisers:
            if adviser_status[adv.id]["selected"]:
                if kept < max_advisers:
                    kept += 1
                else:
                    adviser_status[adv.id]["selected"] = False
                    adviser_status[adv.id]["reason"] = "below floor"

    # 6. Apply always_include keyword rules against the brief text
    brief_search_text = _extract_brief_search_text(brief_obj)

    for adv_id, keywords in always_include.items():
        if adv_id in adviser_status:
            for kw in keywords:
                if kw.lower() in brief_search_text:
                    adviser_status[adv_id]["selected"] = True
                    # If not within margin of top, specify the rule reason
                    if adviser_status[adv_id]["reason"] != "within margin of top":
                        adviser_status[adv_id]["reason"] = f"always-include rule: {kw}"
                    break

    # 7. Construct AdviserScore models
    scores_list: list[AdviserScore] = []
    selected_list: list[str] = []

    # Preserve order of score descending
    for adv in sorted_advisers:
        status = adviser_status[adv.id]
        score_obj = AdviserScore(
            adviser_id=adv.id,
            similarity=round(status["similarity"], 4),
            best_matching_capability=status["best_matching_capability"],
            selected=status["selected"],
            reason=status["reason"],
        )
        scores_list.append(score_obj)
        if status["selected"]:
            selected_list.append(adv.id)

    # 8. Create and persist RoutingDecision
    routing_decision = RoutingDecision(
        decision_id=brief_obj.decision_id,
        routing_query=routing_query,
        scores=scores_list,
        selected=selected_list,
        created_at=datetime.now(timezone.utc),
    )

    try:
        save_routing(routing_decision.decision_id, routing_decision)
    except Exception as exc:
        print(f"[router] Warning: could not save routing decision to ledger: {exc}")

    return routing_decision


# Aliases for convenience
route = route_decision
route_brief = route_decision
