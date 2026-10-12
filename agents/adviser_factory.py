"""Factory for building and executing Google ADK adviser agents."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Optional
import uuid

from google.adk import Runner
from google.adk.agents import LlmAgent
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.tools import calculate, get_facts, query_table, search_documents
from core.registry import find_project_root, get_registry
from core.schemas import AdviserPosition, Claim, DecisionBrief, EvidencePack
from core.settings import get_settings
from harness.context_builder import build_pack
from knowledge.fact_store import load_facts, rank_facts

logger = logging.getLogger(__name__)


def build_adviser(adviser_id: str) -> LlmAgent:
    """Build an ADK LlmAgent for the specified adviser.

    Configures model via Vertex AI with project and location from settings.
    Formats the shared instruction prompt with adviser metadata, lens, criteria,
    and blocking rule, followed by the adviser's own prompt instructions.
    """
    settings = get_settings()
    # Configure Vertex AI endpoint in-code
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
    os.environ["GOOGLE_CLOUD_PROJECT"] = settings.project_id
    os.environ["GOOGLE_CLOUD_LOCATION"] = settings.models_location

    registry = get_registry()
    adv_cfg = registry.get(adviser_id)
    if not adv_cfg:
        raise ValueError(f"Adviser '{adviser_id}' not found in registry")

    root = find_project_root()
    shared_path = root / "prompts" / "advisers" / "_shared.md"
    shared_template = shared_path.read_text(encoding="utf-8")

    if adv_cfg.can_block:
        blocking_rule = (
            "You may raise blocking findings: only for a breach of a mandatory rule "
            "(policy, law or regulation). Cite the rule and the evidence, and give the fix."
        )
    else:
        blocking_rule = (
            "You can’t raise blocking findings. If you see a serious risk, state it "
            "as a claim and add a handoff."
        )

    criteria_str = ", ".join(adv_cfg.criteria_owned)
    shared_instruction = shared_template.format(
        name=adv_cfg.name,
        company="Legion Capital",
        lens=adv_cfg.lens,
        criteria=criteria_str,
        blocking_rule=blocking_rule,
        id=adv_cfg.id,
    )

    adviser_prompt_file = root / adv_cfg.instructions
    adviser_prompt = (
        adviser_prompt_file.read_text(encoding="utf-8")
        if adviser_prompt_file.is_file()
        else (adv_cfg.instructions_content or "")
    )

    full_instruction = f"{shared_instruction}\n\n---\n\n{adviser_prompt}"
    tools = [get_facts, query_table, search_documents, calculate]

    agent = LlmAgent(
        name=f"{adv_cfg.id}_adviser",
        model=settings.models.adviser,
        instruction=full_instruction,
        tools=tools,
    )
    return agent


def _extract_text_from_events(events: list[Any]) -> str:
    """Extract the last text part from a stream of ADK events."""
    texts: list[str] = []
    for e in events:
        if hasattr(e, "content") and e.content:
            for p in e.content.parts:
                if getattr(p, "text", None):
                    texts.append(p.text)
    return texts[-1] if texts else ""


def _extract_json_string(text: str) -> str:
    """Extract JSON string, stripping markdown fences or preamble."""
    cleaned = text.strip()
    if "```json" in cleaned:
        start = cleaned.find("```json") + 7
        end = cleaned.find("```", start)
        if end != -1:
            return cleaned[start:end].strip()
        return cleaned[start:].strip()
    elif "```" in cleaned:
        start = cleaned.find("```") + 3
        end = cleaned.find("```", start)
        if end != -1:
            return cleaned[start:end].strip()
        return cleaned[start:].strip()

    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return cleaned[first_brace : last_brace + 1]

    return cleaned


def simple_pack(
    adviser_id: str,
    brief: Optional[DecisionBrief] = None,
) -> EvidencePack:
    """Construct an evidence pack for the given adviser using build_pack.

    Retained for backward compatibility.
    """
    return build_pack(adviser_id=adviser_id, brief=brief)


def _normalize_position_dict(data: Any, adviser_id: str) -> Any:
    """Normalize and repair minor LLM JSON variations (e.g. string blockers)."""
    if not isinstance(data, dict):
        return data

    if "adviser_id" not in data or not data["adviser_id"]:
        data["adviser_id"] = adviser_id

    # Normalize blocking_findings
    if isinstance(data.get("blocking_findings"), list):
        norm_bf = []
        for item in data["blocking_findings"]:
            if isinstance(item, str):
                import re
                extracted = re.findall(r"\b[A-Z]{3,4}-\d+\b", item)
                norm_bf.append({
                    "text": item,
                    "evidence_ids": extracted,
                    "fix": "Resolve breach in accordance with policy/regulation",
                })
            elif isinstance(item, dict):
                if "text" not in item:
                    item["text"] = str(item)
                if "evidence_ids" not in item or not item["evidence_ids"]:
                    import re
                    item["evidence_ids"] = re.findall(r"\b[A-Z]{3,4}-\d+\b", str(item["text"]))
                if "fix" not in item:
                    item["fix"] = "Remediate finding"
                norm_bf.append(item)
        data["blocking_findings"] = norm_bf

    # Normalize claims
    if isinstance(data.get("claims"), list):
        norm_claims = []
        for i, item in enumerate(data["claims"]):
            if isinstance(item, dict):
                if "claim_id" not in item:
                    item["claim_id"] = f"{adviser_id}-{i+1}"
                if "evidence_ids" not in item:
                    item["evidence_ids"] = []
                norm_claims.append(item)
            elif isinstance(item, str):
                norm_claims.append({
                    "claim_id": f"{adviser_id}-{i+1}",
                    "text": item,
                    "evidence_ids": [],
                    "kind": "judgement",
                })
        data["claims"] = norm_claims

    # Normalize confidence
    if "confidence" in data:
        try:
            data["confidence"] = max(0.0, min(1.0, float(data["confidence"])))
        except (ValueError, TypeError):
            data["confidence"] = 0.8
    else:
        data["confidence"] = 0.8

    return data


def run_adviser(
    adviser_id: str,
    brief: DecisionBrief,
    evidence_pack: EvidencePack,
) -> AdviserPosition:
    """Run an adviser against a decision brief and evidence pack.

    Sends brief and evidence pack JSON to the adviser agent, validates the JSON
    response against AdviserPosition, retries once on validation failure, and demotes
    any blocking findings if the adviser does not have blocking permissions.
    """
    agent = build_adviser(adviser_id)
    registry = get_registry()
    adv_cfg = registry.get(adviser_id)
    can_block = adv_cfg.can_block if adv_cfg else False

    session_service = InMemorySessionService()
    session_id = f"adviser_{adviser_id}_{uuid.uuid4().hex[:8]}"
    session_service.create_session_sync(
        app_name="colosseum",
        user_id="adviser_user",
        session_id=session_id,
    )
    runner = Runner(
        app_name="colosseum",
        agent=agent,
        session_service=session_service,
    )

    user_prompt = (
        "Here is the decision brief and your evidence pack:\n\n"
        f"## Decision Brief\n```json\n{brief.model_dump_json(indent=2)}\n```\n\n"
        f"## Evidence Pack\n```json\n{evidence_pack.model_dump_json(indent=2)}\n```\n\n"
        "Evaluate the decision through your lens and return your position as JSON adhering exactly to the schema."
    )

    content = types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])
    events = list(
        runner.run(
            user_id="adviser_user",
            session_id=session_id,
            new_message=content,
        )
    )
    reply_text = _extract_text_from_events(events)

    position: Optional[AdviserPosition] = None
    try:
        json_str = _extract_json_string(reply_text)
        data = json.loads(json_str)
        data = _normalize_position_dict(data, adviser_id)
        position = AdviserPosition.model_validate(data)
    except Exception as validation_err:
        logger.warning(
            "Adviser %s initial response validation failed: %s. Retrying once...",
            adviser_id,
            validation_err,
        )
        retry_prompt = (
            "Your previous response did not produce valid JSON conforming to the required AdviserPosition schema.\n"
            f"Validation error: {validation_err}\n\n"
            f"Your output was:\n{reply_text}\n\n"
            "Please fix the error and reply with ONLY the valid JSON object adhering to the schema."
        )
        retry_content = types.Content(role="user", parts=[types.Part.from_text(text=retry_prompt)])
        retry_events = list(
            runner.run(
                user_id="adviser_user",
                session_id=session_id,
                new_message=retry_content,
            )
        )
        retry_reply_text = _extract_text_from_events(retry_events)
        retry_json_str = _extract_json_string(retry_reply_text)
        data = json.loads(retry_json_str)
        data = _normalize_position_dict(data, adviser_id)
        position = AdviserPosition.model_validate(data)

    # If can_block is false, demote any blocking findings to claims
    if not can_block and position.blocking_findings:
        for idx, bf in enumerate(position.blocking_findings, 1):
            demoted_claim = Claim(
                claim_id=f"{adviser_id}-claim-{len(position.claims) + idx}",
                text=f"{bf.text} (Fix: {bf.fix})",
                evidence_ids=bf.evidence_ids,
                kind="judgement",
            )
            position.claims.append(demoted_claim)
        position.blocking_findings = []

    return position


# In-memory tracking of execution durations for the last run_advisers call
_last_adviser_durations: dict[str, float] = {}


def get_last_adviser_durations() -> dict[str, float]:
    """Return execution durations in seconds for the most recent run_advisers invocation."""
    return dict(_last_adviser_durations)


async def run_advisers(
    adviser_ids: list[str],
    brief: DecisionBrief,
    packs: dict[str, EvidencePack] | list[EvidencePack] | None = None,
    timeout_s: Optional[float] = None,
) -> dict[str, AdviserPosition | str]:
    """Run multiple advisers concurrently using asyncio, each with a configured timeout.

    A timeout or error is recorded as a string message for that adviser and never stops the others.

    Args:
        adviser_ids: List of adviser IDs to execute.
        brief: The decision brief being evaluated.
        packs: Mapping or list of EvidencePack objects for each adviser. If missing or None,
               simple_pack is built for each adviser.
        timeout_s: Optional timeout in seconds. Defaults to settings.run.adviser_timeout_s.

    Returns:
        Dictionary mapping adviser_id -> AdviserPosition (on success) or str (on timeout/error).
    """
    settings = get_settings()
    effective_timeout = (
        float(timeout_s)
        if timeout_s is not None
        else float(settings.run.adviser_timeout_s)
    )

    # Normalize packs mapping
    if isinstance(packs, dict):
        pack_map = packs
    elif isinstance(packs, list):
        pack_map = {p.adviser_id: p for p in packs}
    else:
        pack_map = {}

    async def _execute_single(aid: str) -> tuple[str, AdviserPosition | str, float]:
        pack = pack_map.get(aid) or build_pack(aid, brief)
        start_t = time.time()
        try:
            position = await asyncio.wait_for(
                asyncio.to_thread(run_adviser, aid, brief, pack),
                timeout=effective_timeout,
            )
            elapsed = time.time() - start_t
            return aid, position, elapsed
        except (asyncio.TimeoutError, TimeoutError):
            elapsed = time.time() - start_t
            msg = f"Timeout after {effective_timeout:.0f}s"
            logger.warning("Adviser '%s' timed out after %.2fs", aid, elapsed)
            return aid, msg, elapsed
        except Exception as exc:
            elapsed = time.time() - start_t
            msg = f"Error: {exc}"
            logger.error("Adviser '%s' encountered error: %s", aid, exc)
            return aid, msg, elapsed

    tasks = [_execute_single(aid) for aid in adviser_ids]
    completed = await asyncio.gather(*tasks)

    results: dict[str, AdviserPosition | str] = {}
    _last_adviser_durations.clear()

    for aid, pos_or_err, elapsed in completed:
        results[aid] = pos_or_err
        _last_adviser_durations[aid] = elapsed

    return results


def run_advisers_sync(
    adviser_ids: list[str],
    brief: DecisionBrief,
    packs: dict[str, EvidencePack] | list[EvidencePack] | None = None,
    timeout_s: Optional[float] = None,
) -> dict[str, AdviserPosition | str]:
    """Synchronous convenience wrapper for run_advisers."""
    return asyncio.run(run_advisers(adviser_ids, brief, packs, timeout_s))

