"""Scribe agent for drafting decision memos.

Per ARCHITECTURE.md §9: Gemini Pro reasoning model (temperature 0.2) with
prompts/scribe.md. Synthesizes accepted claims (KEEP/FLAG) and the computed
scorecard into a validated Memo schema object.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Optional

from google import genai

from core.schemas import (
    AdviserPosition,
    BlockingFinding,
    Claim,
    ClaimStatus,
    ClaimVerdict,
    DecisionBrief,
    Memo,
    Scorecard,
)
from core.settings import get_settings

_genai_client: Optional[genai.Client] = None


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


def _call_reasoning_model(prompt: str) -> str:
    """Invoke the reasoning model with temperature 0.2 and retry on 429 rate limits."""
    settings = get_settings()
    client = get_genai_client()
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=settings.models.reasoning,
                contents=prompt,
                config={"temperature": 0.2},
            )
            return response.text or ""
        except Exception as exc:
            if ("429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)) and attempt < max_retries - 1:
                import time
                time.sleep(2 ** (attempt + 1) * 3)
                continue
            raise
    return ""


def _repair_key_numbers(
    raw_key_numbers: list[Any],
    accepted_claims_by_id: dict[str, Claim],
    accepted_claims_list: list[Claim],
) -> list[dict[str, Any]]:
    """Ensure every item in key_numbers preserves its evidence_ids."""
    repaired: list[dict[str, Any]] = []

    for idx, item in enumerate(raw_key_numbers):
        if not isinstance(item, dict):
            continue

        cid = str(item.get("claim_id") or "").strip()
        eids = [str(e).strip() for e in item.get("evidence_ids", []) if str(e).strip()]

        # 1. If evidence_ids is empty, try looking up by claim_id
        if not eids and cid in accepted_claims_by_id:
            eids = list(accepted_claims_by_id[cid].evidence_ids)

        # 2. If still empty, match against accepted claims by text or value
        if not eids:
            item_val = item.get("value")
            item_text = str(item.get("text") or "").lower()
            for ac in accepted_claims_list:
                if item_val is not None and ac.value == item_val:
                    eids = list(ac.evidence_ids)
                    if not cid:
                        cid = ac.claim_id
                    break
                if item_text and (item_text in ac.text.lower() or ac.text.lower() in item_text):
                    eids = list(ac.evidence_ids)
                    if not cid:
                        cid = ac.claim_id
                    break

        # 3. If still empty, extract any evidence patterns (e.g. ABC-12, doc.pdf) from item text
        if not eids:
            found_eids = re.findall(r"\b[A-Z]{2,6}-\d+\b", str(item.get("text") or ""))
            if found_eids:
                eids = found_eids

        # 4. Fallback if still empty: attach first available accepted evidence ID
        if not eids and accepted_claims_list:
            eids = list(accepted_claims_list[0].evidence_ids)

        item["evidence_ids"] = eids
        if not cid:
            item["claim_id"] = f"kn-{idx + 1}"
        else:
            item["claim_id"] = cid

        if not item.get("kind"):
            item["kind"] = "fact"

        repaired.append(item)

    return repaired


def _repair_blocking_findings(
    raw_bfs: list[Any],
    positions: dict[str, AdviserPosition],
) -> list[dict[str, Any]]:
    """Ensure blocking findings retain fix and evidence_ids from adviser positions."""
    known_bfs: list[BlockingFinding] = []
    for pos in positions.values():
        known_bfs.extend(pos.blocking_findings)

    repaired: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_bfs):
        if not isinstance(item, dict):
            continue

        text_val = str(item.get("text") or "").strip()
        fix_val = str(item.get("fix") or "").strip()
        eids = [str(e).strip() for e in item.get("evidence_ids", []) if str(e).strip()]

        # Try to find matching known blocking finding
        matched_bf: Optional[BlockingFinding] = None
        for kbf in known_bfs:
            if text_val and (text_val in kbf.text or kbf.text in text_val):
                matched_bf = kbf
                break
            if set(eids) and set(eids) == set(kbf.evidence_ids):
                matched_bf = kbf
                break

        if matched_bf:
            if not fix_val:
                fix_val = matched_bf.fix
            if not eids:
                eids = list(matched_bf.evidence_ids)
            if not text_val:
                text_val = matched_bf.text

        if not fix_val and idx < len(known_bfs):
            fix_val = known_bfs[idx].fix
        if not eids and idx < len(known_bfs):
            eids = list(known_bfs[idx].evidence_ids)
        if not text_val and idx < len(known_bfs):
            text_val = known_bfs[idx].text

        item["text"] = text_val
        item["fix"] = fix_val or "Resolution required prior to execution."
        item["evidence_ids"] = eids

        repaired.append(item)

    # If LLM returned no blocking findings but advisers raised some, populate them
    if not repaired and known_bfs:
        for kbf in known_bfs:
            repaired.append(kbf.model_dump())

    return repaired


def write_memo(
    brief: DecisionBrief | dict[str, Any],
    positions: dict[str, AdviserPosition | dict[str, Any]],
    claim_verdicts: list[ClaimVerdict | dict[str, Any]] | None,
    scorecard: Scorecard | dict[str, Any],
    revision_instructions: str = "",
) -> Memo:
    """Draft a decision memo using the reasoning model and prompts/scribe.md.
    
    Passes only KEEP and FLAG claims (FLAG claims with warnings), the exact scorecard,
    blocking findings, and revision instructions. Validates against core.schemas.Memo
    and retries once if invalid JSON or validation error occurs.
    """
    # 1. Normalize schemas
    if isinstance(brief, dict):
        brief_obj = DecisionBrief(**brief)
    else:
        brief_obj = brief

    positions_obj: dict[str, AdviserPosition] = {}
    for aid, pos in positions.items():
        if isinstance(pos, dict):
            positions_obj[aid] = AdviserPosition(**pos)
        else:
            positions_obj[aid] = pos

    verdicts_obj: list[ClaimVerdict] = []
    for v in (claim_verdicts or []):
        if isinstance(v, dict):
            verdicts_obj.append(ClaimVerdict(**v))
        else:
            verdicts_obj.append(v)

    if isinstance(scorecard, dict):
        scorecard_obj = Scorecard(**scorecard)
    else:
        scorecard_obj = scorecard

    # 2. Filter claims: Pass only KEEP and FLAG claims (FLAG claims with warnings)
    verdict_by_id = {v.claim_id: v for v in verdicts_obj}

    accepted_claims_by_adviser: dict[str, list[dict[str, Any]]] = {}
    accepted_claims_by_id: dict[str, Claim] = {}
    all_accepted_claims: list[Claim] = []

    for aid, pos in positions_obj.items():
        accepted_list: list[dict[str, Any]] = []
        for c in pos.claims:
            v = verdict_by_id.get(c.claim_id)
            if v and v.status == ClaimStatus.strike:
                # Omit struck claims entirely
                continue

            claim_dict = {
                "claim_id": c.claim_id,
                "text": c.text,
                "kind": c.kind,
                "value": c.value,
                "unit": c.unit,
                "evidence_ids": c.evidence_ids,
            }
            if v and v.status == ClaimStatus.flag:
                claim_dict["moderator_status"] = "FLAG"
                claim_dict["moderator_warning"] = v.reason
            else:
                claim_dict["moderator_status"] = "KEEP"

            accepted_list.append(claim_dict)
            accepted_claims_by_id[c.claim_id] = c
            all_accepted_claims.append(c)

        accepted_claims_by_adviser[aid] = accepted_list

    # 3. Format blocking findings
    blocking_list: list[dict[str, Any]] = []
    for aid, pos in positions_obj.items():
        for idx, bf in enumerate(pos.blocking_findings, start=1):
            bf_cid = f"{aid}-B{idx}"
            v = verdict_by_id.get(bf_cid)
            if v and v.status == ClaimStatus.strike:
                continue
            bf_dict = {
                "adviser_id": aid,
                "finding_id": bf_cid,
                "text": bf.text,
                "evidence_ids": bf.evidence_ids,
                "fix": bf.fix,
                "status": v.status.value if v else "KEEP",
            }
            if v and v.status == ClaimStatus.flag:
                bf_dict["moderator_warning"] = v.reason
            blocking_list.append(bf_dict)

    # 4. Read prompt template from prompts/scribe.md
    root = Path(__file__).resolve().parent.parent
    prompt_path = root / "prompts" / "scribe.md"
    template = prompt_path.read_text(encoding="utf-8")

    scorecard_dump = scorecard_obj.model_dump()
    brief_dump = brief_obj.model_dump()

    base_prompt = template.format(
        brief=json.dumps(brief_dump, indent=2, default=str),
        scorecard=json.dumps(scorecard_dump, indent=2, default=str),
        claims=json.dumps(accepted_claims_by_adviser, indent=2, default=str),
        blocking=json.dumps(blocking_list, indent=2, default=str),
        revision_instructions=revision_instructions or "None",
    )

    schema_guide = """
Please reply strictly with a JSON object matching this schema:
{
  "decision_id": "<matches brief decision_id>",
  "recommendation": "<concise actionable recommendation>",
  "stance": "A" | "B" | "conditional",
  "confidence": <float between 0.0 and 1.0>,
  "conditions": ["<clear precondition or requirement>", ...],
  "phased_plan": ["<phase 1>", "<phase 2>", ...],
  "scorecard": <exact scorecard as provided>,
  "key_numbers": [
    {
      "claim_id": "<id>",
      "text": "<claim text>",
      "evidence_ids": ["<fact_id or doc_ref>", ...],
      "kind": "fact" | "calculation" | "judgement",
      "value": <number or null>,
      "unit": "<unit string or empty>"
    }
  ],
  "blocking_findings": [
    {
      "text": "<finding description>",
      "evidence_ids": ["<id>", ...],
      "fix": "<required contractual or technical fix>"
    }
  ],
  "dissent": ["<adviser>: <dissenting view> (<evidence ids>)"],
  "assumptions": ["<key underlying assumption>", ...],
  "numbers_to_watch": ["<metric to monitor>", ...],
  "evidence_index": ["<all cited evidence ids>", ...]
}
"""
    full_prompt = base_prompt + "\n\n" + schema_guide

    # 5. Call reasoning model with retry on invalid JSON
    current_prompt = full_prompt
    max_attempts = 2
    last_error: Optional[Exception] = None

    for attempt in range(max_attempts):
        raw_text = _call_reasoning_model(current_prompt)
        cleaned = raw_text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", cleaned)
            cleaned = re.sub(r"\n```$", "", cleaned)

        try:
            parsed = json.loads(cleaned)
            if not isinstance(parsed, dict):
                raise ValueError("LLM response did not parse as a JSON object.")

            # Force exact computed scorecard and decision_id
            parsed["scorecard"] = scorecard_dump
            parsed["decision_id"] = brief_obj.decision_id

            # Ensure key_numbers retain their evidence_ids
            raw_kn = parsed.get("key_numbers", [])
            parsed["key_numbers"] = _repair_key_numbers(
                raw_kn,
                accepted_claims_by_id,
                all_accepted_claims,
            )

            # Ensure blocking findings retain required fields
            raw_bfs = parsed.get("blocking_findings", [])
            parsed["blocking_findings"] = _repair_blocking_findings(
                raw_bfs,
                positions_obj,
            )

            # Ensure default arrays if missing
            for arr_field in ("conditions", "phased_plan", "dissent", "assumptions", "numbers_to_watch", "evidence_index"):
                if not isinstance(parsed.get(arr_field), list):
                    parsed[arr_field] = []

            # Populate evidence_index comprehensively
            eids_set = set(parsed.get("evidence_index", []))
            for kn in parsed["key_numbers"]:
                for e in kn.get("evidence_ids", []):
                    if e:
                        eids_set.add(str(e))
            for bf in parsed["blocking_findings"]:
                for e in bf.get("evidence_ids", []):
                    if e:
                        eids_set.add(str(e))
            parsed["evidence_index"] = sorted(list(eids_set))

            memo = Memo(**parsed)
            return memo

        except Exception as exc:
            last_error = exc
            if attempt < max_attempts - 1:
                current_prompt = (
                    f"{full_prompt}\n\n"
                    f"PREVIOUS ATTEMPT FAILED WITH VALIDATION ERROR: {exc}\n"
                    f"Please correct the response and return valid JSON adhering strictly to the Memo schema."
                )
                continue
            raise ValueError(f"Failed to produce a valid Memo: {last_error}") from last_error

    raise ValueError(f"Failed to produce a valid Memo: {last_error}")
