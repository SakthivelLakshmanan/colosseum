"""Factory for building and executing Google ADK adviser agents."""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Callable, Optional
import uuid

from google.adk import Runner
from google.adk.agents import LlmAgent
from google.adk.sessions import InMemorySessionService
from google.genai import types

from agents.tools import calculate, get_facts, query_table, search_documents
from core.gemini import (
    ColosseumGemini,
    generate,
    get_caller_stats,
    get_genai_client,
    reset_caller_stats,
)
from core.registry import find_project_root, get_registry
from core.schemas import AdviserPosition, Claim, DecisionBrief, EvidencePack
from core.settings import get_settings
from harness.context_builder import build_pack
from knowledge.fact_store import load_facts, rank_facts

logger = logging.getLogger(__name__)


class ToolCallLimiter:
    """Limits tool calls to max_calls per adviser run and disables tools when budget reached."""

    def __init__(self, adviser_id: str, max_calls: int = 4, agent: Optional[Any] = None):
        self.adviser_id = adviser_id
        self.max_calls = max_calls
        self.count = 0
        self.agent = agent

    def disable_tools(self) -> None:
        """Turn off tools on the adviser agent."""
        if self.agent and hasattr(self.agent, "tools"):
            self.agent.tools = []

    def before_tool_callback(
        self, tool: Any, args: Any, tool_context: Any = None
    ) -> Optional[dict[str, Any]]:
        self.count += 1
        if self.count > self.max_calls:
            self.disable_tools()
            logger.info(
                "Adviser '%s' reached tool call limit (%d calls); disabling tools and instructing model to write position.",
                self.adviser_id,
                self.max_calls,
            )
            return {
                "error": "Tool limit reached",
                "message": (
                    f"Tool limit reached ({self.max_calls} calls). Tools are now turned off. "
                    "Do not make any further tool calls. "
                    "Write your position now from the evidence you have as a JSON object adhering to the AdviserPosition schema."
                ),
            }
        if self.count >= self.max_calls:
            self.disable_tools()
        return None


def build_adviser(
    adviser_id: str,
    tool_limiter: Optional[ToolCallLimiter] = None,
    on_first_slot: Optional[Callable[[], None]] = None,
) -> LlmAgent:
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

    client = get_genai_client(caller=f"{adv_cfg.id}_adviser")
    model = ColosseumGemini(
        caller=f"{adv_cfg.id}_adviser",
        model=settings.models.adviser,
        client=client,
        on_first_slot=on_first_slot,
    )

    before_tool_cb = tool_limiter.before_tool_callback if tool_limiter else None

    agent = LlmAgent(
        name=f"{adv_cfg.id}_adviser",
        model=model,
        instruction=full_instruction,
        tools=tools,
        before_tool_callback=before_tool_cb,
    )
    if tool_limiter:
        tool_limiter.agent = agent
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


async def run_adviser_async(
    adviser_id: str,
    brief: DecisionBrief,
    evidence_pack: EvidencePack,
    on_first_slot: Optional[Callable[[], None]] = None,
) -> AdviserPosition:
    """Run an adviser asynchronously using ADK's native runner.run_async.

    Caps tool calls at 8 via ToolCallLimiter and supports cancellation upon timeout.
    """
    settings = get_settings()
    max_tool_calls = int(getattr(getattr(settings, "advisers", None), "max_tool_calls", 4))
    limiter = ToolCallLimiter(adviser_id=adviser_id, max_calls=max_tool_calls)
    agent = build_adviser(adviser_id, tool_limiter=limiter, on_first_slot=on_first_slot)
    registry = get_registry()
    adv_cfg = registry.get(adviser_id)
    can_block = adv_cfg.can_block if adv_cfg else False

    session_service = InMemorySessionService()
    session_id = f"adviser_{adviser_id}_{uuid.uuid4().hex[:8]}"
    await session_service.create_session(
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
    events: list[Any] = []
    async for event in runner.run_async(
        user_id="adviser_user",
        session_id=session_id,
        new_message=content,
    ):
        events.append(event)
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
        retry_events: list[Any] = []
        async for event in runner.run_async(
            user_id="adviser_user",
            session_id=session_id,
            new_message=retry_content,
        ):
            retry_events.append(event)
        retry_reply_text = _extract_text_from_events(retry_events)
        try:
            retry_json_str = _extract_json_string(retry_reply_text)
            data = json.loads(retry_json_str)
            data = _normalize_position_dict(data, adviser_id)
            position = AdviserPosition.model_validate(data)
        except Exception as retry_err:
            logger.warning(
                "Adviser %s retry response invalid (%s). Requesting final position without tools...",
                adviser_id,
                retry_err,
            )
            position = await _request_final_position_without_tools(
                aid=adviser_id,
                brief=brief,
                pack=evidence_pack,
                timeout_s=30.0,
            )

    # Record tool call count for this adviser
    _last_adviser_tool_counts[adviser_id] = limiter.count

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


async def _request_final_position_without_tools(
    aid: str,
    brief: DecisionBrief,
    pack: EvidencePack,
    timeout_s: float,
) -> AdviserPosition:
    """Request final position JSON straight away from evidence pack, with tools turned off."""
    settings = get_settings()
    registry = get_registry()
    adv_cfg = registry.get(aid)
    can_block = adv_cfg.can_block if adv_cfg else False

    root = find_project_root()
    shared_path = root / "prompts" / "advisers" / "_shared.md"
    shared_template = shared_path.read_text(encoding="utf-8")
    if adv_cfg and adv_cfg.can_block:
        blocking_rule = (
            "You may raise blocking findings: only for a breach of a mandatory rule "
            "(policy, law or regulation). Cite the rule and the evidence, and give the fix."
        )
    else:
        blocking_rule = (
            "You can’t raise blocking findings. If you see a serious risk, state it "
            "as a claim and add a handoff."
        )
    criteria_str = ", ".join(adv_cfg.criteria_owned) if adv_cfg else ""
    shared_instruction = shared_template.format(
        name=adv_cfg.name if adv_cfg else aid,
        company="Legion Capital",
        lens=adv_cfg.lens if adv_cfg else aid,
        criteria=criteria_str,
        blocking_rule=blocking_rule,
        id=aid,
    )

    adviser_prompt_file = root / (adv_cfg.instructions if adv_cfg else "")
    adviser_prompt = (
        adviser_prompt_file.read_text(encoding="utf-8")
        if adviser_prompt_file.is_file()
        else (adv_cfg.instructions_content or "") if adv_cfg else ""
    )

    final_user_prompt = (
        f"{shared_instruction}\n\n---\n\n{adviser_prompt}\n\n---\n\n"
        "Here is the decision brief and your evidence pack:\n\n"
        f"## Decision Brief\n```json\n{brief.model_dump_json(indent=2)}\n```\n\n"
        f"## Evidence Pack\n```json\n{pack.model_dump_json(indent=2)}\n```\n\n"
        "Time/tool limit reached. Tools are now turned off. "
        "Return your final position now straight away from the evidence you already have, "
        "as a JSON object adhering exactly to the AdviserPosition schema."
    )

    resp = await asyncio.to_thread(
        generate,
        model=settings.models.adviser,
        contents=final_user_prompt,
        config={"temperature": 0.0},
        caller=f"{aid}_adviser",
        timeout_s=timeout_s,
    )
    raw_text = resp if isinstance(resp, str) else (getattr(resp, "text", "") or str(resp))
    json_str = _extract_json_string(raw_text)
    data = json.loads(json_str)
    data = _normalize_position_dict(data, aid)
    position = AdviserPosition.model_validate(data)

    if not can_block and position.blocking_findings:
        for idx, bf in enumerate(position.blocking_findings, 1):
            demoted_claim = Claim(
                claim_id=f"{aid}-claim-{len(position.claims) + idx}",
                text=f"{bf.text} (Fix: {bf.fix})",
                evidence_ids=bf.evidence_ids,
                kind="judgement",
            )
            position.claims.append(demoted_claim)
        position.blocking_findings = []

    return position


def run_adviser(
    adviser_id: str,
    brief: DecisionBrief,
    evidence_pack: EvidencePack,
    on_first_slot: Optional[Callable[[], None]] = None,
) -> AdviserPosition:
    """Run an adviser against a decision brief and evidence pack (sync wrapper)."""
    return asyncio.run(run_adviser_async(adviser_id, brief, evidence_pack, on_first_slot=on_first_slot))


# In-memory tracking of execution durations, tool counts, and slot wait times for the last run_advisers call
_last_adviser_durations: dict[str, float] = {}
_last_adviser_tool_counts: dict[str, int] = {}
_last_adviser_slot_waits: dict[str, float] = {}


def get_last_adviser_durations() -> dict[str, float]:
    """Return execution durations in seconds for the most recent run_advisers invocation."""
    return dict(_last_adviser_durations)


def get_last_adviser_tool_counts() -> dict[str, int]:
    """Return tool call counts for the most recent run_advisers invocation."""
    return dict(_last_adviser_tool_counts)


def get_last_adviser_slot_waits() -> dict[str, float]:
    """Return slot wait durations in seconds for the most recent run_advisers invocation."""
    return dict(_last_adviser_slot_waits)


async def run_advisers(
    adviser_ids: list[str],
    brief: DecisionBrief,
    packs: dict[str, EvidencePack] | list[EvidencePack] | None = None,
    timeout_s: Optional[float] = None,
) -> dict[str, AdviserPosition | str]:
    """Run multiple advisers concurrently with timeout clock starting when first slot is acquired.

    Enforces graceful deadline: at 75% of timeout, tools are turned off and the final position JSON
    is requested straight away from existing evidence. An adviser counts as failed only if that final call fails.
    """
    settings = get_settings()
    if timeout_s is not None:
        effective_timeout = float(timeout_s)
    elif hasattr(settings, "advisers") and hasattr(settings.advisers, "timeout_s"):
        effective_timeout = float(settings.advisers.timeout_s)
    else:
        effective_timeout = float(settings.run.adviser_timeout_s)

    # Normalize packs mapping
    if isinstance(packs, dict):
        pack_map = packs
    elif isinstance(packs, list):
        pack_map = {p.adviser_id: p for p in packs}
    else:
        pack_map = {}

    async def _execute_single(aid: str) -> tuple[str, AdviserPosition | str, float, float]:
        pack = pack_map.get(aid) or build_pack(aid, brief)
        launch_t = time.time()
        slot_event = asyncio.Event()
        slot_time: list[float] = []
        loop = asyncio.get_running_loop()

        reset_caller_stats(f"{aid}_adviser")

        def _on_slot():
            if not slot_time:
                slot_time.append(time.time())
                loop.call_soon_threadsafe(slot_event.set)

        adviser_task = asyncio.create_task(
            run_adviser_async(aid, brief, pack, on_first_slot=_on_slot)
        )
        wait_slot_task = asyncio.create_task(slot_event.wait())

        try:
            done, _ = await asyncio.wait(
                [adviser_task, wait_slot_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if adviser_task in done:
                position = await adviser_task
            else:
                # First slot acquired; timeout clock starts now
                # Phase 1 runs up to 75% of timeout
                phase1_limit = 0.75 * effective_timeout
                try:
                    position = await asyncio.wait_for(
                        adviser_task,
                        timeout=phase1_limit,
                    )
                except (asyncio.TimeoutError, TimeoutError):
                    # 75% of timeout reached: turn off tools and ask it to return final position JSON straight away
                    logger.info("Adviser '%s' reached 75%% timeout; requesting final position with tools disabled.", aid)
                    if not adviser_task.done():
                        adviser_task.cancel()

                    elapsed_after_slot = time.time() - slot_time[0]
                    remaining_t = max(0.1, effective_timeout - elapsed_after_slot)
                    try:
                        position = await asyncio.wait_for(
                            _request_final_position_without_tools(
                                aid=aid,
                                brief=brief,
                                pack=pack,
                                timeout_s=remaining_t,
                            ),
                            timeout=remaining_t,
                        )
                    except Exception as final_exc:
                        raise TimeoutError(f"Timeout after {effective_timeout:.0f}s: final call failed ({final_exc})") from final_exc

            total_t = time.time() - launch_t
            waited_s = (slot_time[0] - launch_t) if slot_time else 0.0
            call_count, total_gemini_s, slowest_s = get_caller_stats(f"{aid}_adviser")

            log_msg = (
                f"[adviser] {aid} done in {int(round(total_t))}s, "
                f"gemini_calls={call_count}, slowest={int(round(slowest_s))}s"
            )
            print(log_msg, flush=True)
            logger.info(log_msg)

            return aid, position, total_t, waited_s

        except (asyncio.TimeoutError, TimeoutError):
            if not adviser_task.done():
                adviser_task.cancel()
            total_t = time.time() - launch_t
            waited_s = (slot_time[0] - launch_t) if slot_time else 0.0
            call_count, total_gemini_s, slowest_s = get_caller_stats(f"{aid}_adviser")
            msg = f"Timeout after {effective_timeout:.0f}s"
            logger.warning(
                "Adviser '%s' timed out after %.2fs (waited %.2fs, gemini_calls=%d, slowest=%.1fs)",
                aid,
                total_t,
                waited_s,
                call_count,
                slowest_s,
            )
            return aid, msg, total_t, waited_s
        except Exception as exc:
            if not adviser_task.done():
                adviser_task.cancel()
            total_t = time.time() - launch_t
            waited_s = (slot_time[0] - launch_t) if slot_time else 0.0
            call_count, total_gemini_s, slowest_s = get_caller_stats(f"{aid}_adviser")
            msg = f"Error: {exc}"
            logger.error("Adviser '%s' encountered error: %s", aid, exc)
            return aid, msg, total_t, waited_s
        finally:
            if not wait_slot_task.done():
                wait_slot_task.cancel()

    tasks = [_execute_single(aid) for aid in adviser_ids]
    completed = await asyncio.gather(*tasks)

    results: dict[str, AdviserPosition | str] = {}
    _last_adviser_durations.clear()
    _last_adviser_slot_waits.clear()

    for aid, pos_or_err, total_t, waited_s in completed:
        results[aid] = pos_or_err
        _last_adviser_durations[aid] = total_t
        _last_adviser_slot_waits[aid] = waited_s

    return results


def run_advisers_sync(
    adviser_ids: list[str],
    brief: DecisionBrief,
    packs: dict[str, EvidencePack] | list[EvidencePack] | None = None,
    timeout_s: Optional[float] = None,
) -> dict[str, AdviserPosition | str]:
    """Synchronous convenience wrapper for run_advisers."""
    return asyncio.run(run_advisers(adviser_ids, brief, packs, timeout_s))

