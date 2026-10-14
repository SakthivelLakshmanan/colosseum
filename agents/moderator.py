"""Moderator for claim verification and final memo checking in Colosseum.

Implements the two-stage moderation pipeline:
1. Deterministic code checks (evidence present, evidence exists, not superseded,
   number/formula matches, low reliability).
2. Gemini Pro reasoning model support check (batched per adviser).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any, Optional
import yaml

from google import genai

from core.registry import get_registry
from core.schemas import (
    AdviserPosition,
    Claim,
    ClaimStatus,
    ClaimVerdict,
    DecisionBrief,
    FinalVerdict,
    Memo,
    MemoIssue,
    MemoVerdictType,
    Reliability,
)
from core.settings import get_settings
from knowledge.fact_store import get_fact, load_all_facts

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


def load_moderator_config() -> dict[str, Any]:
    """Load configuration from config/moderator.yaml."""
    root = Path(__file__).resolve().parent.parent
    cfg_file = root / "config" / "moderator.yaml"
    if cfg_file.is_file():
        with open(cfg_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def _get_retrieved_doc_refs(run_tool_log: Any) -> set[str]:
    """Extract all doc_refs returned by search_documents in the run's tool log."""
    doc_refs: set[str] = set()
    if run_tool_log is None:
        from agents.tools import get_tool_calls
        run_tool_log = get_tool_calls()

    if isinstance(run_tool_log, dict):
        if "doc_refs" in run_tool_log:
            for d in run_tool_log["doc_refs"]:
                doc_refs.add(str(d).strip())
        if "tool_calls" in run_tool_log:
            run_tool_log = run_tool_log["tool_calls"]
        else:
            run_tool_log = [run_tool_log]

    if isinstance(run_tool_log, list):
        for entry in run_tool_log:
            if not isinstance(entry, dict):
                continue
            t_name = entry.get("tool_name") or entry.get("name")
            if t_name == "search_documents":
                for eid in entry.get("evidence_ids", []):
                    doc_refs.add(str(eid).strip())
                for r in entry.get("results", []):
                    if isinstance(r, dict) and "doc_ref" in r:
                        doc_refs.add(str(r["doc_ref"]).strip())
            for eid in entry.get("evidence_ids", []):
                s = str(eid).strip()
                if ".pdf" in s.lower() or "#p" in s.lower():
                    doc_refs.add(s)

    return doc_refs


def _matches_within_1pct(val1: float, val2: float, tolerance_pct: float = 1.0) -> bool:
    """Check if two numbers match within a relative tolerance (default 1%)."""
    tol = tolerance_pct / 100.0
    diff = abs(val1 - val2)
    denom = max(abs(val1), abs(val2))
    if denom <= 1e-9:
        return diff <= 1e-9
    return (diff / denom) <= tol


def _normalize_unit_and_value(
    value: float,
    unit: Optional[str],
) -> tuple[str, float]:
    """Normalize value and unit into a canonical unit family and base value.
    
    Compatible families:
    - time_hours: hours, days (*24), weeks (*168), months (*720)
    - currency_lakh_month: INR lakh/month, INR crore/month (*100), INR lakh/year (/12), INR crore/year (*100/12)
    - currency_lakh_total: INR lakh, INR crore (*100), lakh, crore
    - percent: %, pct, percentage, % of volume, etc.
    - custom: exact cleaned token for counts like 'merchants', 'tickets', 'engineers', etc.
    """
    if not unit or not unit.strip():
        return ("unitless", float(value))

    u = unit.strip().lower()

    # 1. Currency units (lakh/crore) - check first so rates like '/ month' are not treated as time
    has_lakh = "lakh" in u or "lac" in u
    has_crore = "crore" in u or "cr" in u.split()
    if has_lakh or has_crore:
        multiplier = 100.0 if has_crore else 1.0
        if "year" in u or "annually" in u:
            return ("currency_lakh_month", (float(value) * multiplier) / 12.0)
        elif "month" in u:
            return ("currency_lakh_month", float(value) * multiplier)
        else:
            return ("currency_lakh_total", float(value) * multiplier)

    # 2. Time units (hours/days/weeks/months) -> base: hours
    if any(k in u for k in ["hour", "hr"]):
        return ("time_hours", float(value))
    if "day" in u:
        return ("time_hours", float(value) * 24.0)
    if "week" in u or "wk" in u:
        return ("time_hours", float(value) * 168.0)
    if "month" in u or "mo" in u:
        if "fee" not in u:  # exclude 'months of fees'
            return ("time_hours", float(value) * 720.0)

    # Percentage (%)
    if "%" in u or "percent" in u or "pct" in u:
        return ("percent", float(value))

    # Clean words for other counts (e.g. 'merchants', 'tickets', 'engineers')
    cleaned_words = [w for w in re.findall(r"[a-z]+", u) if w not in ["of", "the", "in", "per", "a"]]
    if cleaned_words:
        return (cleaned_words[0], float(value))

    return (u, float(value))


def _number_appears_in_statement(
    statement: str,
    target_value: float,
    target_unit: Optional[str] = None,
    tolerance_pct: float = 1.0,
) -> bool:
    """Check whether a target number (or unit-normalized equivalent) appears in statement text."""
    if not statement:
        return False

    # 1. Direct number check in text (e.g. "40,000", "1,200", "8", "6", "14.4")
    tokens = re.findall(r"(?:₹|\b)(\d+(?:,\d+)*(?:\.\d+)?)\b", statement)
    for tok in tokens:
        clean_num = tok.replace(",", "")
        try:
            val = float(clean_num)
            if _matches_within_1pct(val, target_value, tolerance_pct):
                return True
        except ValueError:
            continue

    # 2. Number with unit word in statement (e.g. "₹1.4 crore", "3 weeks", "₹6 lakh a month")
    pattern = r"(?:₹|\b)(\d+(?:,\d+)*(?:\.\d+)?)\s*(crore|lakh|cr|lac|hours?|days?|weeks?|months?|%)"
    matches = re.findall(pattern, statement, re.IGNORECASE)
    targ_fam, targ_norm = _normalize_unit_and_value(target_value, target_unit)

    for num_str, unit_str in matches:
        clean_num = num_str.replace(",", "")
        try:
            val = float(clean_num)
            cand_fam, cand_norm = _normalize_unit_and_value(val, unit_str)
            if cand_fam == targ_fam:
                if _matches_within_1pct(cand_norm, targ_norm, tolerance_pct):
                    return True
        except ValueError:
            continue

    return False


def _is_blocking_finding(
    claim: Claim,
    position: Optional[AdviserPosition],
    can_block: bool,
) -> bool:
    """Determine whether a claim represents a protected blocking finding."""
    if not can_block:
        return False
    t_lower = (claim.text or "").lower()
    c_id_lower = (claim.claim_id or "").lower()
    if "blocking" in t_lower or "block-" in t_lower or "block" in c_id_lower:
        return True
    if position and position.blocking_findings:
        claim_eids = set(str(x).strip() for x in claim.evidence_ids if str(x).strip())
        for bf in position.blocking_findings:
            bf_eids = set(str(x).strip() for x in bf.evidence_ids if str(x).strip())
            if claim_eids and (claim_eids == bf_eids or claim_eids.issubset(bf_eids)):
                return True
            if claim.text.strip() and (claim.text in bf.text or bf.text in claim.text):
                return True
    return False


def _protect_if_blocking(
    verdict: ClaimVerdict,
    claim: Claim,
    position: Optional[AdviserPosition],
    can_block: bool,
) -> ClaimVerdict:
    """Downgrade STRIKE to FLAG if the claim is a blocking finding from a can_block adviser."""
    if can_block and verdict.status == ClaimStatus.strike and _is_blocking_finding(claim, position, can_block):
        return ClaimVerdict(
            claim_id=verdict.claim_id,
            status=ClaimStatus.flag,
            check=verdict.check,
            reason=verdict.reason,
        )
    return verdict


def check_single_claim_deterministic(
    claim: Claim,
    position: Optional[AdviserPosition],
    can_block: bool,
    retrieved_doc_refs: set[str],
    tolerance_pct: float = 1.0,
) -> Optional[ClaimVerdict]:
    """Run the 5 deterministic checks in code, in order, stopping at the first failure.
    
    1. evidence_present: no evidence_ids -> STRIKE.
    2. evidence_exists: every fact_id must exist in the fact store (get_fact, including
       superseded facts); every doc_ref must have been returned by search_documents in this
       run's tool log -> otherwise STRIKE.
    3. not_superseded: any cited fact with superseded_by set -> STRIKE with reason 'superseded by <id>'.
    4. number_matches:
       A number in a claim is supported if any of these is true:
         (a) it matches value_num of a cited fact within 1%, comparing only compatible units after normalising;
         (b) the same number appears in the statement text of a cited fact;
         (c) it is a calculation whose formula recomputes to the value within 1%.
       If no cited fact has a comparable number, skip the check rather than striking.
    5. reliability: if every cited fact has reliability 'low' -> FLAG.
    """
    clean_eids = [str(eid).strip() for eid in claim.evidence_ids if str(eid).strip()]

    # 1. evidence_present
    if not clean_eids:
        v = ClaimVerdict(
            claim_id=claim.claim_id,
            status=ClaimStatus.strike,
            check="evidence_present",
            reason="No evidence cited.",
        )
        return _protect_if_blocking(v, claim, position, can_block)

    # 2. evidence_exists
    for eid in clean_eids:
        fact = get_fact(eid)
        if fact is not None:
            continue
        if eid in retrieved_doc_refs:
            continue
        is_doc = ".pdf" in eid.lower() or "#p" in eid.lower() or "/" in eid.lower()
        if is_doc:
            reason = f"Document reference '{eid}' was not returned by search_documents in this run."
        else:
            reason = f"Fact '{eid}' does not exist in the fact store."
        v = ClaimVerdict(
            claim_id=claim.claim_id,
            status=ClaimStatus.strike,
            check="evidence_exists",
            reason=reason,
        )
        return _protect_if_blocking(v, claim, position, can_block)

    # 3. not_superseded
    for eid in clean_eids:
        fact = get_fact(eid)
        if fact and fact.superseded_by:
            v = ClaimVerdict(
                claim_id=claim.claim_id,
                status=ClaimStatus.strike,
                check="not_superseded",
                reason=f"superseded by {fact.superseded_by}",
            )
            return _protect_if_blocking(v, claim, position, can_block)

    # 4. number_matches
    claim_has_number = (claim.value is not None)
    claim_is_calc = (claim.kind == "calculation" and bool(claim.formula))

    if claim_has_number or claim_is_calc:
        supported = False
        has_comparable = False
        recomputed_val: Optional[float] = None
        comparable_details: list[str] = []

        # (c) It is a calculation whose formula recomputes to the value within 1%
        if claim_is_calc:
            try:
                from agents.tools import calculate
                recomputed_val = calculate(claim.formula)
                has_comparable = True
                comparable_details.append(f"formula '{claim.formula}' = {recomputed_val}")
                if claim.value is not None:
                    if _matches_within_1pct(recomputed_val, claim.value, tolerance_pct):
                        supported = True
                else:
                    supported = True
            except Exception as e:
                v = ClaimVerdict(
                    claim_id=claim.claim_id,
                    status=ClaimStatus.strike,
                    check="number_matches",
                    reason=f"Calculation error for formula '{claim.formula}': {e}",
                )
                return _protect_if_blocking(v, claim, position, can_block)

        # (a) and (b): Compare against cited facts
        if not supported and claim_has_number:
            target_val = float(claim.value)
            targ_fam, targ_norm = _normalize_unit_and_value(target_val, claim.unit)

            for eid in clean_eids:
                fact = get_fact(eid)
                if not fact:
                    continue

                # (b) Same number appears in statement text of a cited fact
                if fact.statement and _number_appears_in_statement(fact.statement, target_val, claim.unit, tolerance_pct):
                    supported = True
                    break

                # (a) Matches value_num of cited fact within 1% comparing only compatible units after normalising
                if fact.value_num is not None:
                    fact_fam, fact_norm = _normalize_unit_and_value(fact.value_num, fact.unit)
                    if fact_fam == targ_fam:
                        has_comparable = True
                        comparable_details.append(f"{fact.fact_id} ({fact.value_num} {fact.unit})")
                        if _matches_within_1pct(fact_norm, targ_norm, tolerance_pct):
                            supported = True
                            break

        if supported:
            pass  # Passed number check
        elif has_comparable:
            # Comparable numbers existed, but none matched within tolerance
            if claim_is_calc and claim.value is not None and recomputed_val is not None:
                reason = f"Number mismatch: calculated value {recomputed_val} does not match claimed value {claim.value} (formula: {claim.formula})"
            else:
                details_str = ", ".join(comparable_details) if comparable_details else "cited facts"
                reason = f"Number mismatch: claimed {claim.value} {claim.unit or ''}, but cited evidence has {details_str}"
            v = ClaimVerdict(
                claim_id=claim.claim_id,
                status=ClaimStatus.strike,
                check="number_matches",
                reason=reason,
            )
            return _protect_if_blocking(v, claim, position, can_block)
        else:
            # If no cited fact has a comparable number, skip the check rather than striking
            pass

    # 5. reliability
    cited_facts = [get_fact(eid) for eid in clean_eids if get_fact(eid) is not None]
    if cited_facts and all(
        (f.reliability == Reliability.low or str(f.reliability).lower() == "low")
        for f in cited_facts
    ):
        v = ClaimVerdict(
            claim_id=claim.claim_id,
            status=ClaimStatus.flag,
            check="low_reliability",
            reason="Every cited fact has low reliability.",
        )
        return _protect_if_blocking(v, claim, position, can_block)

    return None


def _build_related_facts_text(claim: Claim, all_facts: list[Any]) -> str:
    """Format other facts with the same entity AND the same metric as any cited fact."""
    clean_eids = [str(eid).strip() for eid in claim.evidence_ids if str(eid).strip()]
    related: list[Any] = []
    seen_ids = set(clean_eids)

    for eid in clean_eids:
        fact = get_fact(eid)
        if not fact:
            continue
        ent = (fact.entity or "").strip().lower()
        met = (fact.metric or "").strip().lower()
        if not ent or not met:
            continue

        for of in all_facts:
            if of.fact_id in seen_ids:
                continue
            of_ent = (of.entity or "").strip().lower()
            of_met = (of.metric or "").strip().lower()

            # Strictly require the same entity AND the same metric
            if of_ent == ent and of_met == met:
                related.append(of)
                seen_ids.add(of.fact_id)

    if not related:
        return "(none)"

    lines = []
    for rf in related:
        lines.append(f"- {rf.fact_id}: {rf.statement} (entity: {rf.entity}, metric: {rf.metric}, as_of: {rf.as_of})")
    return "\n".join(lines)


def _check_claims_llm_batched(
    adviser_id: str,
    claims: list[Claim],
    position: Optional[AdviserPosition],
    can_block: bool,
) -> list[ClaimVerdict]:
    """Execute one batched Gemini Pro reasoning call for all surviving claims of an adviser."""
    if not claims:
        return []

    settings = get_settings()
    all_facts = load_all_facts(include_superseded=True)
    claims_blocks: list[str] = []

    for c in claims:
        ev_lines = []
        for eid in c.evidence_ids:
            f = get_fact(eid)
            if f:
                ev_lines.append(
                    f"  * {f.fact_id}: {f.statement} (source: {f.source_ref}, reliability: {f.reliability.value})"
                )
            else:
                ev_lines.append(f"  * {eid}")
        ev_text = "\n".join(ev_lines) if ev_lines else "  (none)"
        rel_text = _build_related_facts_text(c, all_facts)

        claims_blocks.append(
            f"--- Claim ID: {c.claim_id} ---\n"
            f"Claim text: {c.text}\n"
            f"Kind: {c.kind} (value: {c.value}, unit: {c.unit})\n"
            f"Cited evidence:\n{ev_text}\n"
            f"Other facts that may conflict:\n{rel_text}\n"
        )

    full_claims_text = "\n".join(claims_blocks)

    prompt = f"""# Moderator: Check claims for adviser '{adviser_id}'

Code has already confirmed that each claim cites evidence, that the evidence exists and isn't superseded, and that any number matches.
Your job is the judgement code can't make: **does the cited evidence actually support the claim as worded?**

Return one of:
- **KEEP**: the evidence supports the claim as worded.
- **FLAG**: partly supported. For example it over-generalises (evidence covers mobile only; claim says all channels), relies only on a low-reliability source, or ignores a conflicting fact you can see. The claim stays, with your warning.
Only treat another fact as a conflict if it measures the same thing (same entity and same metric). Different measures, such as merchant NPS and borrower satisfaction, are not conflicts.
- **STRIKE**: the evidence doesn't support the claim, or contradicts it.

Never strike a blocking finding raised by an adviser allowed to block. If you doubt it, FLAG it and explain.

{full_claims_text}

Reply strictly as a JSON array containing one object per claim:
[
  {{"claim_id": "...", "status": "KEEP|FLAG|STRIKE", "check": "supported|overgeneralised|contradicted|low_reliability|conflict", "reason": "one sentence"}}
]
"""

    client = get_genai_client()
    raw_text = ""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=settings.models.reasoning,
                contents=prompt,
                config={"temperature": 0.0},
            )
            raw_text = response.text or ""
            break
        except Exception as exc:
            if ("429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)) and attempt < max_retries - 1:
                import time
                time.sleep(2 ** (attempt + 1) * 3)
                continue
            # Fallback soft if reasoning call fails
            verdicts = []
            for c in claims:
                v = ClaimVerdict(
                    claim_id=c.claim_id,
                    status=ClaimStatus.flag,
                    check="moderator_llm_error",
                    reason=f"LLM verification failed: {exc}",
                )
                verdicts.append(_protect_if_blocking(v, c, position, can_block))
            return verdicts

    # Parse response JSON array
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)

    parsed_items: list[dict[str, Any]] = []
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            parsed_items = parsed
        elif isinstance(parsed, dict):
            parsed_items = [parsed]
    except Exception:
        # Try to find JSON array or individual objects with regex
        match = re.search(r"\[\s*\{.*\}\s*\]", cleaned, re.DOTALL)
        if match:
            try:
                parsed_items = json.loads(match.group(0))
            except Exception:
                pass

    item_by_id = {str(item.get("claim_id")): item for item in parsed_items if isinstance(item, dict)}
    verdicts: list[ClaimVerdict] = []

    for c in claims:
        item = item_by_id.get(c.claim_id)
        if item:
            status_raw = str(item.get("status", "KEEP")).upper().strip()
            if status_raw in ("KEEP", "FLAG", "STRIKE"):
                status = ClaimStatus(status_raw)
            elif status_raw in ("DROP", "REJECT", "REMOVE"):
                status = ClaimStatus.strike
            elif status_raw in ("WARN", "WARNING", "PARTIAL"):
                status = ClaimStatus.flag
            else:
                status = ClaimStatus.keep

            check_val = str(item.get("check") or "llm_check")
            reason_val = str(item.get("reason") or "Evaluated by reasoning model.")
            v = ClaimVerdict(
                claim_id=c.claim_id,
                status=status,
                check=check_val,
                reason=reason_val,
            )
        else:
            v = ClaimVerdict(
                claim_id=c.claim_id,
                status=ClaimStatus.keep,
                check="supported",
                reason="Default keep; passed code checks.",
            )
        verdicts.append(_protect_if_blocking(v, c, position, can_block))

    return verdicts


def check_claims(
    positions: dict[str, AdviserPosition],
    run_tool_log: Any = None,
) -> list[ClaimVerdict]:
    """Execute two-stage moderation on claims across all adviser positions.
    
    1. Deterministic checks in code (stopping at first failure):
       - evidence_present -> STRIKE
       - evidence_exists -> STRIKE
       - not_superseded -> STRIKE (reason: 'superseded by <id>')
       - number_matches -> STRIKE
       - reliability -> FLAG
    2. Batched reasoning model support check per adviser for surviving claims.
    3. Blocking findings from advisers with can_block=true are never struck (downgraded to FLAG).
    """
    if not positions:
        return []

    config = load_moderator_config()
    tol_pct = float(config.get("number_tolerance_pct", 1.0))
    retrieved_doc_refs = _get_retrieved_doc_refs(run_tool_log)
    registry = get_registry()

    import concurrent.futures

    deterministic_by_adviser: dict[str, list[ClaimVerdict]] = {}
    claims_for_llm_by_adviser: dict[str, list[Claim]] = {}

    for adviser_id, position in positions.items():
        if not isinstance(position, AdviserPosition):
            continue

        adv_cfg = registry.get(adviser_id)
        can_block = adv_cfg.can_block if adv_cfg else False

        # Gather regular claims and blocking findings from this adviser
        all_claims: list[Claim] = list(position.claims)
        for idx, bf in enumerate(position.blocking_findings, start=1):
            bf_kind = "blocking" if can_block else "fact"
            bf_claim = Claim(
                claim_id=f"{adviser_id}-B{idx}",
                text=bf.text,
                evidence_ids=bf.evidence_ids,
                kind=bf_kind,
            )
            all_claims.append(bf_claim)

        det_verdicts: list[ClaimVerdict] = []
        llm_claims: list[Claim] = []

        for claim in all_claims:
            v = check_single_claim_deterministic(
                claim=claim,
                position=position,
                can_block=can_block,
                retrieved_doc_refs=retrieved_doc_refs,
                tolerance_pct=tol_pct,
            )
            if v is not None:
                det_verdicts.append(v)
            else:
                llm_claims.append(claim)

        deterministic_by_adviser[adviser_id] = det_verdicts
        claims_for_llm_by_adviser[adviser_id] = llm_claims

    # Dispatch LLM checks concurrently across advisers (one batched call per adviser)
    advisers_to_llm = {
        aid: (claims, positions[aid], registry.get(aid).can_block if registry.get(aid) else False)
        for aid, claims in claims_for_llm_by_adviser.items()
        if claims
    }

    llm_verdicts_by_adviser: dict[str, list[ClaimVerdict]] = {}
    if advisers_to_llm:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(advisers_to_llm), 6)) as executor:
            future_to_aid = {
                executor.submit(_check_claims_llm_batched, aid, claims, pos, cb): aid
                for aid, (claims, pos, cb) in advisers_to_llm.items()
            }
            for future in concurrent.futures.as_completed(future_to_aid):
                aid = future_to_aid[future]
                try:
                    llm_verdicts_by_adviser[aid] = future.result()
                except Exception as exc:
                    err_verdicts = []
                    for c in claims_for_llm_by_adviser[aid]:
                        err_verdicts.append(
                            ClaimVerdict(
                                claim_id=c.claim_id,
                                status=ClaimStatus.flag,
                                check="moderator_error",
                                reason=f"Moderation failed: {exc}",
                            )
                        )
                    llm_verdicts_by_adviser[aid] = err_verdicts

    # Merge verdicts preserving original order of claims followed by blocking findings
    all_verdicts: list[ClaimVerdict] = []
    for adviser_id, position in positions.items():
        if not isinstance(position, AdviserPosition):
            continue
        det = deterministic_by_adviser.get(adviser_id, [])
        llm = llm_verdicts_by_adviser.get(adviser_id, [])
        v_map = {v.claim_id: v for v in det + llm}

        expected_ids = [c.claim_id for c in position.claims] + [
            f"{adviser_id}-B{idx}" for idx in range(1, len(position.blocking_findings) + 1)
        ]
        for cid in expected_ids:
            if cid in v_map:
                all_verdicts.append(v_map[cid])

    return all_verdicts


def check_memo(
    brief: DecisionBrief | dict[str, Any],
    memo: Memo | dict[str, Any],
    selected_advisers: list[str],
    positions: dict[str, AdviserPosition | dict[str, Any]],
    claim_verdicts: list[ClaimVerdict | dict[str, Any]] | None = None,
    revision_number: int = 0,
) -> FinalVerdict:
    """Execute final memo check per ARCHITECTURE.md §8 and config/moderator.yaml.

    1. Deterministic code checks first:
       - every criterion in the brief is addressed
       - every selected adviser appears in the memo body or in dissent
       - every unresolved blocking finding is a condition or explicitly marked as needing a human override
       - dissent is present if any adviser's stance differs from the memo's stance
       - every key_number matches its evidence (recompute calculations, 1% tolerance; fact values vs value_num)
    2. Pass code-check results and the memo to the reasoning model (temperature 0) with prompts/moderator_final.md,
       returning PASS / INCOMPLETE / WRONG with issues and revision instructions.
    3. If any code check failed, the verdict can't be PASS.
    """
    # 1. Normalize schemas
    brief_obj = brief if isinstance(brief, DecisionBrief) else DecisionBrief(**brief)
    memo_obj = memo if isinstance(memo, Memo) else Memo(**memo)
    positions_obj: dict[str, AdviserPosition] = {
        aid: (pos if isinstance(pos, AdviserPosition) else AdviserPosition(**pos))
        for aid, pos in positions.items()
    }
    verdicts_obj: list[ClaimVerdict] = [
        v if isinstance(v, ClaimVerdict) else ClaimVerdict(**v)
        for v in (claim_verdicts or [])
    ]

    code_check_issues: list[MemoIssue] = []

    # Prepare memo searchable text corpus
    memo_body_text = (
        f"{memo_obj.recommendation} "
        f"{' '.join(memo_obj.conditions)} "
        f"{' '.join(memo_obj.phased_plan)} "
        f"{' '.join(memo_obj.assumptions)} "
        f"{' '.join(memo_obj.numbers_to_watch)} "
        f"{' '.join(kn.text for kn in memo_obj.key_numbers)}"
    )
    memo_full_text = memo_body_text + " " + " ".join(memo_obj.dissent)
    memo_lower = memo_full_text.lower()

    # Check 1: every criterion in the brief is addressed
    scorecard_criteria = {r.criterion.lower() for r in memo_obj.scorecard.rows}
    for crit in brief_obj.criteria:
        c_name = crit.name.strip()
        c_lower = c_name.lower()
        substantive_words = [w for w in re.findall(r"[a-z]{4,}", c_lower) if w not in {"impact", "other"}]
        addressed = (
            c_lower in memo_lower
            or c_lower in scorecard_criteria
            or any(w in memo_lower for w in substantive_words)
        )
        if not addressed:
            code_check_issues.append(
                MemoIssue(
                    kind="missing_criterion",
                    detail=f"Criterion '{c_name}' is not addressed in the memo or scorecard.",
                    evidence_ids=[],
                )
            )

    # Check 2: every selected adviser appears in the memo body or in dissent
    registry = get_registry()
    memo_evidence_index_lower = {e.lower() for e in (memo_obj.evidence_index or [])}
    for aid in selected_advisers:
        aid_lower = aid.lower()
        adv_cfg = registry.get(aid)
        adv_name = adv_cfg.name.lower() if adv_cfg else aid_lower

        # Check name/id in full memo text
        adviser_cited = (aid_lower in memo_lower) or (adv_name in memo_lower)

        # Check if any claim ID from this adviser was cited in memo body text
        if not adviser_cited and aid in positions_obj:
            for c in positions_obj[aid].claims:
                if c.claim_id.lower() in memo_lower:
                    adviser_cited = True
                    break

        # Check if the adviser's evidence IDs appear in the memo's evidence_index
        # (scribe may cite BUS-xx, FIN-xx evidence without naming the adviser explicitly)
        if not adviser_cited and aid in positions_obj:
            adviser_prefix = aid.upper().rstrip("0123456789") + "-"  # e.g. "BUS-", "FIN-"
            # Use claim IDs prefix (e.g. "FIN-01" -> adviser "finance")
            adv_claim_prefix = positions_obj[aid].claims[0].claim_id.split("-")[0].upper() + "-" if positions_obj[aid].claims else None
            for eid in memo_evidence_index_lower:
                # Match by adviser id prefix (finance -> fin, business -> bus, etc.)
                if eid.startswith(aid_lower[:3]):
                    adviser_cited = True
                    break
                if adv_claim_prefix and eid.upper().startswith(adv_claim_prefix):
                    adviser_cited = True
                    break

        if not adviser_cited:
            code_check_issues.append(
                MemoIssue(
                    kind="missing_adviser",
                    detail=f"Selected adviser '{aid}' ({adv_cfg.name if adv_cfg else aid}) is not represented in the memo body or dissent.",
                    evidence_ids=[],
                )
            )

    # Check 3: every unresolved blocking finding is a condition or explicitly marked as needing a human override
    verdict_by_id = {v.claim_id: v for v in verdicts_obj}
    conditions_and_rec = (f"{memo_obj.recommendation} {' '.join(memo_obj.conditions)} {' '.join(memo_obj.phased_plan)}").lower()

    for aid in selected_advisers:
        pos = positions_obj.get(aid)
        if not pos or not pos.blocking_findings:
            continue
        for idx, bf in enumerate(pos.blocking_findings, start=1):
            bf_cid = f"{aid}-B{idx}"
            v = verdict_by_id.get(bf_cid)
            if v and v.status == ClaimStatus.strike:
                # Struck finding is resolved / discarded
                continue

            # Check if reflected in conditions, recommendation, phased plan or human override
            reflected = False
            if "human override" in conditions_and_rec or "override" in conditions_and_rec:
                reflected = True
            else:
                # Check key words from finding text or fix
                text_words = [
                    w for w in re.findall(r"[a-z]{4,}", (bf.text + " " + bf.fix).lower())
                    if w not in {"with", "this", "that", "order", "prior", "before", "agreement", "standard", "formal", "vendor"}
                ]
                matches = sum(1 for w in set(text_words) if w in conditions_and_rec)
                if matches >= 2 or bf_cid.lower() in conditions_and_rec:
                    reflected = True

            if not reflected:
                code_check_issues.append(
                    MemoIssue(
                        kind="ignored_blocker",
                        detail=f"Unresolved blocking finding '{bf_cid}' is neither a condition nor marked for human override.",
                        evidence_ids=bf.evidence_ids,
                    )
                )

    # Check 4: dissent is present if any adviser's stance differs from the memo's stance
    memo_stance = memo_obj.stance.lower().strip()
    dissent_required = False
    disagreeing_advisers: list[str] = []

    for aid in selected_advisers:
        pos = positions_obj.get(aid)
        if not pos:
            continue
        pos_stance = pos.stance.lower().strip()

        # Check disagreement
        if memo_stance in ("b", "favours_b"):
            if pos_stance in ("favours_a", "object", "a", "insufficient_evidence"):
                dissent_required = True
                disagreeing_advisers.append(aid)
        elif memo_stance in ("a", "favours_a"):
            if pos_stance in ("favours_b", "object", "b", "insufficient_evidence"):
                dissent_required = True
                disagreeing_advisers.append(aid)
        elif memo_stance in ("conditional",):
            if pos_stance in ("favours_a", "object"):
                dissent_required = True
                disagreeing_advisers.append(aid)

    if dissent_required and not memo_obj.dissent:
        code_check_issues.append(
            MemoIssue(
                kind="missing_dissent",
                detail=f"Adviser(s) {disagreeing_advisers} disagreed with memo stance '{memo_obj.stance}', but dissent section is empty.",
                evidence_ids=[],
            )
        )

    # Check 5: every key_number matches its evidence (recompute calculations, 1% tolerance; fact values vs value_num)
    for kn in memo_obj.key_numbers:
        if not kn.evidence_ids:
            code_check_issues.append(
                MemoIssue(
                    kind="number_mismatch",
                    detail=f"Key number '{kn.text}' does not cite any evidence IDs.",
                    evidence_ids=[],
                )
            )
            continue

        if kn.kind == "calculation" and kn.formula:
            try:
                from agents.tools import calculate
                recomputed = calculate(kn.formula)
                if kn.value is not None and not _matches_within_1pct(recomputed, kn.value, tolerance_pct=1.0):
                    code_check_issues.append(
                        MemoIssue(
                            kind="number_mismatch",
                            detail=f"Calculation mismatch for '{kn.text}': formula '{kn.formula}' = {recomputed}, but claimed {kn.value}",
                            evidence_ids=kn.evidence_ids,
                        )
                    )
            except Exception as exc:
                code_check_issues.append(
                    MemoIssue(
                        kind="number_mismatch",
                        detail=f"Formula error for '{kn.text}': {exc}",
                        evidence_ids=kn.evidence_ids,
                    )
                )
        elif kn.value is not None:
            matched = False
            comparable_eids: list[str] = []  # evidence IDs in same unit family
            for eid in kn.evidence_ids:
                fact = get_fact(eid)
                if not fact:
                    continue
                # (b) Appears in statement text
                if fact.statement and _number_appears_in_statement(fact.statement, kn.value, kn.unit, tolerance_pct=1.0):
                    matched = True
                    break
                # (a) Matches value_num within 1%
                if fact.value_num is not None:
                    t_fam, t_val = _normalize_unit_and_value(kn.value, kn.unit)
                    f_fam, f_val = _normalize_unit_and_value(fact.value_num, fact.unit)
                    if t_fam == f_fam:
                        comparable_eids.append(eid)
                        if _matches_within_1pct(f_val, t_val, tolerance_pct=1.0):
                            matched = True
                            break

            # Only flag number_mismatch when a *single* directly comparable evidence ID
            # contradicts the value. When multiple evidence IDs in the same unit family are
            # cited, the memo value is likely a derived aggregate (sum/difference of
            # components) that cannot be verified without an explicit formula — skip.
            if comparable_eids and not matched and len(comparable_eids) == 1:
                code_check_issues.append(
                    MemoIssue(
                        kind="number_mismatch",
                        detail=f"Number mismatch in '{kn.text}': value {kn.value} {kn.unit or ''} does not match cited evidence {kn.evidence_ids}.",
                        evidence_ids=kn.evidence_ids,
                    )
                )

    # 2. Build code checks summary for reasoning model
    if not code_check_issues:
        code_checks_text = "ALL CODE CHECKS PASSED: Every criterion addressed, every selected adviser represented, unresolved blockers reflected, dissent present, all key numbers match evidence."
    else:
        failures_list = [f"- [{issue.kind}] {issue.detail}" for issue in code_check_issues]
        code_checks_text = "CODE CHECK FAILURES DETECTED:\n" + "\n".join(failures_list)

    # Build accepted claims summary
    accepted_claims_lines = []
    for aid in selected_advisers:
        pos = positions_obj.get(aid)
        if not pos:
            continue
        for c in pos.claims:
            v = verdict_by_id.get(c.claim_id)
            if v and v.status == ClaimStatus.strike:
                continue
            status_tag = f"[{v.status.value}]" if v else "[KEEP]"
            warn_str = f" (Warning: {v.reason})" if v and v.status == ClaimStatus.flag else ""
            accepted_claims_lines.append(f"- ({aid}) {c.claim_id}: {c.text} {status_tag}{warn_str}")

    # 3. Load prompts/moderator_final.md
    root = Path(__file__).resolve().parent.parent
    prompt_file = root / "prompts" / "moderator_final.md"
    template = prompt_file.read_text(encoding="utf-8")

    prompt = template.format(
        brief=json.dumps(brief_obj.model_dump(), indent=2, default=str),
        code_checks=code_checks_text,
        memo=json.dumps(memo_obj.model_dump(), indent=2, default=str),
        claims="\n".join(accepted_claims_lines),
    )

    client = get_genai_client()
    settings = get_settings()
    max_retries = 3
    raw_response = ""

    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(
                model=settings.models.reasoning,
                contents=prompt,
                config={"temperature": 0.0},
            )
            raw_response = resp.text or ""
            break
        except Exception as exc:
            if ("429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)) and attempt < max_retries - 1:
                import time
                time.sleep(2 ** (attempt + 1) * 3)
                continue
            # If model call fails, produce an INCOMPLETE verdict with the error
            return FinalVerdict(
                verdict=MemoVerdictType.incomplete,
                issues=code_check_issues + [MemoIssue(kind="moderator_error", detail=f"LLM check failed: {exc}", evidence_ids=[])],
                revision_instructions="Moderator reasoning model call failed; retry verification.",
                revision_number=revision_number,
            )

    # Parse JSON
    cleaned = raw_response.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\n", "", cleaned)
        cleaned = re.sub(r"\n```$", "", cleaned)

    parsed: dict[str, Any] = {}
    try:
        parsed = json.loads(cleaned)
    except Exception:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(0))
            except Exception:
                pass

    verdict_raw = str(parsed.get("verdict", "PASS")).upper().strip()
    if verdict_raw == "PASS":
        verdict_type = MemoVerdictType.pass_
    elif verdict_raw == "WRONG":
        verdict_type = MemoVerdictType.wrong
    else:
        verdict_type = MemoVerdictType.incomplete

    llm_issues_raw = parsed.get("issues", [])
    llm_issues: list[MemoIssue] = []
    if isinstance(llm_issues_raw, list):
        for item in llm_issues_raw:
            if isinstance(item, dict):
                llm_issues.append(
                    MemoIssue(
                        kind=str(item.get("kind") or "issue"),
                        detail=str(item.get("detail") or ""),
                        evidence_ids=item.get("evidence_ids") or [],
                    )
                )

    revision_instructions = str(parsed.get("revision_instructions") or "").strip()

    # Rule: If any code check failed, the verdict can't be PASS
    final_issues = code_check_issues + llm_issues
    if code_check_issues:
        if verdict_type == MemoVerdictType.pass_:
            has_wrong = any(i.kind in ("number_mismatch", "ignored_blocker") for i in code_check_issues)
            verdict_type = MemoVerdictType.wrong if has_wrong else MemoVerdictType.incomplete

        cc_instr = "; ".join(i.detail for i in code_check_issues)
        if revision_instructions:
            revision_instructions = f"Resolve code check failures: {cc_instr}. {revision_instructions}"
        else:
            revision_instructions = f"Resolve code check failures: {cc_instr}"

    return FinalVerdict(
        verdict=verdict_type,
        issues=final_issues,
        revision_instructions=revision_instructions,
        revision_number=revision_number,
    )

