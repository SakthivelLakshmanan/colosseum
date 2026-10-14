"""Generate routing queries (topic phrases) from decision briefs using Gemini Flash."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any

from core.gemini import generate, generate_content, get_genai_client
from core.registry import find_project_root
from core.schemas import DecisionBrief
from core.settings import get_settings


def _format_brief_readable(brief: DecisionBrief | dict[str, Any] | str) -> str:
    """Format a decision brief into structured plain text for the prompt."""
    if isinstance(brief, str):
        try:
            parsed = json.loads(brief)
            brief_obj = DecisionBrief.model_validate(parsed)
        except Exception:
            return brief
    elif isinstance(brief, dict):
        brief_obj = DecisionBrief.model_validate(brief)
    else:
        brief_obj = brief

    parts = [
        f"Decision ID: {brief_obj.decision_id}",
        f"Question: {brief_obj.question}",
        "Options:",
    ]
    for opt_key, opt_text in brief_obj.options.items():
        parts.append(f"  Option {opt_key}: {opt_text}")

    if brief_obj.constraints:
        parts.append("Constraints:")
        for c in brief_obj.constraints:
            parts.append(f"  - {c}")

    if brief_obj.criteria:
        parts.append("Criteria:")
        for cr in brief_obj.criteria:
            parts.append(f"  - {cr.name} (weight: {cr.weight}): {cr.good_looks_like}")

    if brief_obj.context:
        parts.append(f"Context:\n{brief_obj.context}")

    return "\n".join(parts)


def make_routing_query(brief: DecisionBrief | dict[str, Any] | str) -> str:
    """Generate topic lines for routing a decision brief against adviser capabilities.
    
    Uses the adviser model configured in settings and the prompt template from
    prompts/harness_routing.md, filling {brief} with the brief formatted as readable text.
    Returns the generated topic lines as a newline-separated string.
    """
    settings = get_settings()
    root = find_project_root()
    prompt_file = root / "prompts" / "harness_routing.md"

    if not prompt_file.is_file():
        raise FileNotFoundError(f"Routing prompt file not found: {prompt_file}")

    prompt_template = prompt_file.read_text(encoding="utf-8")
    readable_brief = _format_brief_readable(brief)
    full_prompt = prompt_template.replace("{brief}", readable_brief)

    response = generate(
        model=settings.models.adviser,
        contents=full_prompt,
        config={"temperature": 0.0},
        caller="router",
    )

    raw_text = response if isinstance(response, str) else (getattr(response, "text", "") or str(response))

    # Clean markdown fences or bullet markers
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[^\n]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)

    topic_lines: list[str] = []
    for line in cleaned.splitlines():
        line = line.strip()
        if not line:
            continue
        # Strip bullets like '- ', '* ', or numbered prefixes like '1. '
        line = re.sub(r"^(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if line:
            topic_lines.append(line)

    return "\n".join(topic_lines)
