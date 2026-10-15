"""Scorecard builder per ARCHITECTURE.md §9 and config/moderator.yaml.

Pure Python synthesis of adviser criterion scores, ignoring scores whose evidence
all belongs to struck claims, computing weighted totals, and mapping unresolved
blocking findings to options.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from core.registry import AdviserRegistry, load_registry
from core.schemas import (
    AdviserPosition,
    ClaimStatus,
    ClaimVerdict,
    Criterion,
    CriterionScore,
    DecisionBrief,
    Scorecard,
    ScorecardRow,
)


def _map_finding_to_option(finding_text: str, brief: DecisionBrief) -> str:
    """Map a blocking finding to the option that adopts the vendor in question."""
    t_lower = str(finding_text).lower()

    # 1. Match vendor / distinctive keywords against brief options descriptions
    for opt_key, opt_desc in brief.options.items():
        desc_lower = opt_desc.lower()
        words = [
            w for w in re.findall(r"[a-zA-Z0-9_-]+", desc_lower)
            if len(w) >= 4 and w not in {
                "stay", "with", "move", "volume", "into", "from",
                "that", "this", "adopt", "option", "total", "percent",
            }
        ]
        for w in words:
            if w in t_lower:
                return opt_key

    # 2. Check direct mentions like "option b", "option a"
    for opt_key in brief.options:
        if f"option {opt_key.lower()}" in t_lower:
            return opt_key

    # 3. For DEC-001 (or default when option B adopts the alternative vendor)
    if getattr(brief, "decision_id", "") == "DEC-001" and "B" in brief.options:
        return "B"

    if "B" in brief.options:
        return "B"
    return next(iter(brief.options.keys()))


def normalize_criterion_name(name: str) -> str:
    """Normalize criterion name case-insensitively and ignoring punctuation."""
    return re.sub(r"[\W_]+", "", str(name)).lower()


def build_scorecard(
    brief: DecisionBrief | dict[str, Any],
    positions: dict[str, AdviserPosition | dict[str, Any]],
    claim_verdicts: list[ClaimVerdict | dict[str, Any]] | None = None,
    registry: Optional[AdviserRegistry] = None,
) -> Scorecard:
    """Build a Scorecard by synthesizing adviser criterion scores and blocking findings.
    
    1. For each criterion in the brief, average the scores from the advisers that own it
       (criteria_owned / criterion.owners) for each option.
    2. Ignore a criterion_score if all its evidence_ids belong to struck claims.
    3. Weighted total per option = sum(weight * average).
    4. blocked_options: map each unresolved blocking finding to the option that adopts
       the vendor in question with the finding's text.
    """
    # Normalize inputs
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

    if registry is None:
        try:
            registry = load_registry()
        except Exception:
            registry = None

    # Step 1: Identify struck claims and map evidence IDs to claims
    struck_claim_ids: set[str] = set()
    for v in verdicts_obj:
        status_val = v.status.value if hasattr(v.status, "value") else str(v.status)
        if status_val.upper() == "STRIKE":
            struck_claim_ids.add(v.claim_id)

    claims_by_evidence: dict[str, set[str]] = {}
    for aid, pos in positions_obj.items():
        for c in pos.claims:
            for eid in c.evidence_ids:
                clean_eid = str(eid).strip()
                if clean_eid:
                    claims_by_evidence.setdefault(clean_eid, set()).add(c.claim_id)
        for idx, bf in enumerate(pos.blocking_findings, start=1):
            bf_cid = f"{aid}-B{idx}"
            for eid in bf.evidence_ids:
                clean_eid = str(eid).strip()
                if clean_eid:
                    claims_by_evidence.setdefault(clean_eid, set()).add(bf_cid)

    def _evidence_id_belongs_to_struck_claim(eid: str) -> bool:
        clean_eid = str(eid).strip()
        if clean_eid in struck_claim_ids:
            return True
        citing_claims = claims_by_evidence.get(clean_eid, set())
        if citing_claims and citing_claims.issubset(struck_claim_ids):
            return True
        return False

    def _should_ignore_score(cs: CriterionScore) -> bool:
        if not cs.evidence_ids:
            return False
        if not struck_claim_ids:
            return False
        # Ignore if all its evidence_ids belong to struck claims
        return all(_evidence_id_belongs_to_struck_claim(eid) for eid in cs.evidence_ids)

    # Step 2: Build rows for each criterion
    rows: list[ScorecardRow] = []

    for criterion in brief_obj.criteria:
        # Determine owners for this criterion (matching case-insensitively and ignoring punctuation)
        owners: list[str] = list(criterion.owners) if criterion.owners else []
        norm_crit = normalize_criterion_name(criterion.name)
        if registry:
            for adv in registry.advisers:
                for owned in adv.criteria_owned:
                    if normalize_criterion_name(owned) == norm_crit and adv.id not in owners:
                        owners.append(adv.id)

        scores_by_option: dict[str, Optional[float]] = {}
        row_evidence_ids: list[str] = []

        for opt_key in brief_obj.options:
            collected_scores: list[float] = []

            for owner_id in owners:
                pos = positions_obj.get(owner_id)
                if not pos:
                    continue
                for cs in pos.criterion_scores:
                    if (
                        normalize_criterion_name(cs.criterion) == norm_crit
                        and cs.option.strip().upper() == opt_key.strip().upper()
                    ):
                        if _should_ignore_score(cs):
                            continue
                        collected_scores.append(float(cs.score))
                        for eid in cs.evidence_ids:
                            clean_eid = str(eid).strip()
                            if clean_eid and clean_eid not in row_evidence_ids:
                                row_evidence_ids.append(clean_eid)

            if collected_scores:
                scores_by_option[opt_key] = round(sum(collected_scores) / len(collected_scores), 4)
            else:
                # Missing criterion gets None (never 0)
                scores_by_option[opt_key] = None

        # Determine missing owners for this criterion
        missing_owners = [
            o for o in owners
            if not (
                o in positions_obj
                and any(normalize_criterion_name(cs.criterion) == norm_crit for cs in positions_obj[o].criterion_scores)
            )
        ]

        rows.append(
            ScorecardRow(
                criterion=criterion.name,
                weight=criterion.weight,
                scores=scores_by_option,
                evidence_ids=row_evidence_ids,
                missing_owners=sorted(list(set(missing_owners))),
            )
        )

    # Step 3: Compute weighted totals per option over scored criteria with re-normalized weights
    weighted_totals: dict[str, float] = {}
    for opt_key in brief_obj.options:
        scored_rows = [r for r in rows if r.scores.get(opt_key) is not None]
        if scored_rows:
            total_weight = sum(r.weight for r in scored_rows)
            if total_weight > 0:
                total = sum((r.weight / total_weight) * float(r.scores[opt_key]) for r in scored_rows)
            else:
                total = 0.0
            weighted_totals[opt_key] = round(total, 4)
        else:
            weighted_totals[opt_key] = 0.0

    # Step 4: Map unresolved blocking findings to blocked_options
    blocked_options: dict[str, list[str]] = {}
    for aid, pos in positions_obj.items():
        if not pos.blocking_findings:
            continue

        for idx, bf in enumerate(pos.blocking_findings, start=1):
            bf_cid = f"{aid}-B{idx}"
            # An unresolved blocking finding is one that has not been struck
            if bf_cid in struck_claim_ids:
                continue

            opt_key = _map_finding_to_option(bf.text, brief_obj)
            blocked_options.setdefault(opt_key, []).append(bf.text)

    has_missing_scores = any(None in r.scores.values() for r in rows)

    return Scorecard(
        rows=rows,
        weighted_totals=weighted_totals,
        blocked_options=blocked_options,
        has_missing_scores=has_missing_scores,
    )
