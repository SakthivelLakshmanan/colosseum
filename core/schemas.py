"""Colosseum data contracts.

Every module talks to every other module only through these models. Keep them stable:
new data sources, new advisers or a new UI should not need changes here.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Optional, Union

from pydantic import BaseModel, Field


# ---------- Knowledge layer ----------

class Reliability(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class Fact(BaseModel):
    """One atomic, citable fact. Planted facts, extracted document facts and live data all use this shape."""
    fact_id: str                      # e.g. "FIN-03". Stable; used as the evidence ID in claims.
    domain: str                       # primary adviser id, e.g. "finance"
    related_domains: list[str] = []   # other advisers who may care
    topic: str                        # short grouping key, e.g. "fees"
    statement: str                    # one plain-English sentence
    entity: str                       # who/what the fact is about
    metric: str                       # machine-friendly name, e.g. "blended_fee_pct"
    value_num: Optional[float] = None
    value_text: Optional[str] = None
    unit: str = ""
    as_of: date
    superseded_by: Optional[str] = None   # fact_id of the newer fact, if any
    source_type: str                      # "table" | "document" | "system" | "live"
    source_ref: str                       # table name, or "file.pdf §x"
    reliability: Reliability = Reliability.high


class DocumentHit(BaseModel):
    """A passage returned by Agent Search. Citable as evidence via doc_ref."""
    doc_ref: str                      # e.g. "Brevis_Draft_Agreement.pdf#p1"
    title: str
    snippet: str


class TopicSummary(BaseModel):
    topic: str
    summary: str
    fact_ids: list[str] = []
    summary_source: str = "llm"  # "llm" | "code"

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def __str__(self) -> str:
        return self.summary

    def __iter__(self):
        return iter((self.summary, self.summary_source))


class EvidencePack(BaseModel):
    """What the harness hands one adviser: the facts most relevant to its lens, already aggregated if large."""
    adviser_id: str
    facts: list[Fact]
    summaries: list[Union[TopicSummary, str, dict[str, Any]]] = []  # aggregator summaries; each must mention the fact_ids it covers
    total_matching_facts: int                                        # how many facts matched before trimming/aggregation
    truncated: bool = False


# ---------- Decision ----------

class Criterion(BaseModel):
    name: str
    weight: float                     # all weights sum to 1.0
    owners: list[str]                 # adviser ids that score this criterion
    good_looks_like: str


class DecisionBrief(BaseModel):
    decision_id: str
    question: str
    options: dict[str, str]           # {"A": "...", "B": "..."}
    constraints: list[str]
    criteria: list[Criterion]
    context: str = ""


# ---------- Harness ----------

class AdviserScore(BaseModel):
    adviser_id: str
    similarity: float                 # best cosine similarity across the adviser's capability statements
    best_matching_capability: str
    selected: bool
    reason: str                       # "above margin", "always-include rule: data", "below floor", ...


class RoutingDecision(BaseModel):
    decision_id: str
    routing_query: str                # the text the harness generated and embedded
    scores: list[AdviserScore]
    selected: list[str]
    created_at: datetime


# ---------- Advisers ----------

class Claim(BaseModel):
    claim_id: str                     # "<adviser_id>-<n>"
    text: str
    evidence_ids: list[str]           # fact_ids and/or doc_refs
    kind: str = "fact"                # "fact" | "calculation" | "judgement"
    formula: Optional[str] = None     # required when kind == "calculation"
    value: Optional[float] = None     # the number the claim asserts, if any
    unit: Optional[str] = None


class CriterionScore(BaseModel):
    criterion: str
    option: str                       # "A" | "B" | other option keys
    score: int = Field(ge=1, le=5)
    reason: str
    evidence_ids: list[str]


class BlockingFinding(BaseModel):
    text: str
    evidence_ids: list[str]
    fix: str                          # what would clear the block


class AdviserPosition(BaseModel):
    adviser_id: str
    stance: str                       # "favours_A" | "favours_B" | "conditional" | "insufficient_evidence"
    summary: str
    conditions: list[str] = []
    claims: list[Claim]
    criterion_scores: list[CriterionScore]
    blocking_findings: list[BlockingFinding] = []   # only advisers with can_block=True may populate
    handoffs: list[str] = []          # points for other advisers
    confidence: float = Field(ge=0, le=1)


# ---------- Moderator ----------

class ClaimStatus(str, Enum):
    keep = "KEEP"
    flag = "FLAG"       # stays, with a warning shown in the UI
    strike = "STRIKE"   # removed from the memo inputs


class ClaimVerdict(BaseModel):
    claim_id: str
    status: ClaimStatus
    reason: str
    check: str          # "evidence_missing" | "superseded" | "number_mismatch" | "unsupported" | "low_reliability" | "ok" ...


class MemoVerdictType(str, Enum):
    pass_ = "PASS"
    incomplete = "INCOMPLETE"
    wrong = "WRONG"


class MemoIssue(BaseModel):
    kind: str           # "missing_criterion" | "missing_dissent" | "ignored_blocker" | "number_mismatch" | "doesnt_follow" ...
    detail: str
    evidence_ids: list[str] = []


class FinalVerdict(BaseModel):
    verdict: MemoVerdictType
    issues: list[MemoIssue]
    revision_instructions: str = ""
    revision_number: int = 0


# ---------- Synthesis ----------

class ScorecardRow(BaseModel):
    criterion: str
    weight: float
    scores: dict[str, float]          # option -> average score from owner advisers
    evidence_ids: list[str]


class Scorecard(BaseModel):
    rows: list[ScorecardRow]
    weighted_totals: dict[str, float] # option -> weighted total (1-5)
    blocked_options: dict[str, list[str]]  # option -> blocking finding texts


class Memo(BaseModel):
    decision_id: str
    recommendation: str
    stance: str                       # "A" | "B" | "conditional"
    confidence: float
    conditions: list[str]
    phased_plan: list[str] = []
    scorecard: Scorecard
    key_numbers: list[Claim]          # every number in the memo, with evidence
    blocking_findings: list[BlockingFinding]
    dissent: list[str]                # "<adviser>: <view> (evidence ids)"
    assumptions: list[str]
    numbers_to_watch: list[str]
    evidence_index: list[str]         # every evidence id used


# ---------- Run record (Firestore) ----------

class HumanDecision(BaseModel):
    action: str                       # "approve" | "override"
    note: str = ""                    # required for override of a blocking finding
    decided_by: str = ""
    decided_at: datetime


class RunRecord(BaseModel):
    run_id: str
    brief: DecisionBrief
    routing: RoutingDecision
    evidence_packs: list[EvidencePack]
    positions: list[AdviserPosition]
    claim_verdicts: list[ClaimVerdict]
    memo_versions: list[Memo]
    final_verdicts: list[FinalVerdict]
    human_decision: Optional[HumanDecision] = None
    metrics: dict[str, float] = {}    # latency_s, facts_considered, tokens, etc.
