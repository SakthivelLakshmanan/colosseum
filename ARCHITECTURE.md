# Colosseum — Architecture

Colosseum helps a company make big decisions. An **agentic harness** reads a decision, turns it into an embedding and routes it to the advisers who can contribute. Each adviser gets a bounded **evidence pack** from the company's fact store, argues its case with cited evidence, and a **moderator** strikes unsupported claims. Code computes a weighted **scorecard**, a **Scribe** writes the memo, and the moderator checks the final answer and sends it back if it's wrong or incomplete. A human approves or overrides.

The demo company is **Legion Capital** (fictional NBFC). The demo decision: move 40% of payment volume from Arvanta Payments to Brevis Pay.

This file is the build spec. Give it to Antigravity at the start of every build session.

---

## 1. Design principles

1. **Config over code.** Advisers, routing rules and moderator rules live in `config/*.yaml` and `prompts/*.md`. Adding an adviser = one YAML entry + one prompt file.
2. **One fact shape for everything.** Planted facts, facts extracted from documents and live data all become `Fact` rows (`core/schemas.py`). Nothing downstream cares where a fact came from.
3. **Evidence IDs everywhere.** Every claim cites `fact_id`s or `doc_ref`s. Every number in the memo traces back to evidence.
4. **Deterministic first, LLM second.** Arithmetic, number checks, superseded checks and the scorecard run in code. The LLM only does judgement.
5. **Bounded context.** Advisers never see "all the data". The harness builds an evidence pack per adviser, capped in size, with aggregation when there's too much.
6. **Latest wins.** A newer fact about the same entity and metric supersedes the older one. Superseded facts are excluded from evidence packs and can't support a claim.
7. **Answer keys stay hidden.** Test data lives in the `colosseum_eval` dataset. The app's service account can't read it.
8. **Fail soft.** One adviser timing out or returning bad JSON must not break the run; it is recorded and the run continues.

---

## 2. Layers and modules

| Layer | Module | Responsibility | v1 (this build) | Scale-up path |
|---|---|---|---|---|
| Ingestion | `connectors/` | Turn any source into `Fact` rows | CSV load via `scripts/load_data.sh` | Connector classes (BigQuery table, REST API, Pub/Sub) on a Cloud Scheduler → Cloud Run job |
| Knowledge | `knowledge/fact_store.py` | Read/write facts in BigQuery | SQL on `colosseum.facts` | Same table, partitioned by `as_of`, clustered by `domain` |
| Knowledge | `knowledge/queries.py` | Approved read-only queries on company tables | 8 named queries | Add named queries; never free-form SQL |
| Knowledge | `knowledge/documents.py` | Search company documents | Agent Search over `gs://BUCKET/docs/` | Same; add Document AI extraction to create facts from new docs |
| Harness | `harness/routing_query.py` | Brief → routing query (topics) | Gemini Flash + `prompts/harness_routing.md` | Same |
| Harness | `harness/embeddings.py` | Embeddings with caching | `gemini-embedding-001`, cached in Firestore | Same |
| Harness | `harness/router.py` | Score and select advisers | Cosine similarity in memory | Vector index if advisers grow to hundreds |
| Harness | `harness/context_builder.py` | Build an evidence pack per adviser | Filter by domain, keyword rank, cap at 25 | Rank with BigQuery `VECTOR_SEARCH` on fact embeddings |
| Harness | `knowledge/aggregator.py` | Compress large fact sets | Latest per (entity, metric), then one-line summaries per topic that keep fact_ids | Map-reduce summaries in parallel |
| Advisers | `agents/adviser_factory.py` | Build adviser agents from the registry | ADK `LlmAgent` per adviser | Same; any number of advisers |
| Advisers | `agents/tools.py` | `get_facts`, `query_table`, `search_documents`, `calculate` | Python functions as ADK tools | Same interfaces |
| Moderation | `agents/moderator.py` | Claim checks and final-answer check | Code checks + Gemini Pro | Same |
| Synthesis | `agents/scorecard.py` | Weighted scorecard | Pure Python | Same |
| Synthesis | `agents/scribe.py` | Write the memo | Gemini Pro + `prompts/scribe.md` | Same |
| Orchestration | `agents/pipeline.py` | Run the whole flow | ADK custom agent | Same |
| Records | `core/ledger.py` | Save runs, routing, decisions | Firestore | Same; export to BigQuery for analytics |
| Interface | `api/main.py`, `web/` | HTTP API and web page | FastAPI + static page on one Cloud Run service | Split API and UI if needed |
| Quality | `evals/run_evals.py` | Planted-fact, routing and moderator tests | Reads `colosseum_eval` | Run on every change |

---

## 3. Request flow

```
POST /runs  {brief}
  1. Harness: routing query     brief ──Gemini Flash──▶ topic list
  2. Harness: route             embed topic list; score each adviser (max cosine over its capability statements);
                                select by margin/floor, min 2 / max 6; apply always_include rules; log to Firestore
  3. Harness: evidence packs    for each selected adviser: facts from its domains (+ related), not superseded,
                                ranked; if > 25, aggregate
  4. Advisers (parallel)        each returns AdviserPosition JSON (validated with core/schemas.py)
  5. Moderator: claims          code checks (evidence present/exists/not superseded/number match/reliability),
                                then Gemini Pro support check; blocking findings are protected
  6. Scorecard (code)           mean of owner advisers' scores per criterion × weights; options with an
                                unresolved blocking finding are marked "not eligible until fixed"
  7. Scribe ⇄ Final check       Scribe writes Memo → code checks + Gemini Pro verdict (PASS/INCOMPLETE/WRONG)
                                → if not PASS, back to Scribe with instructions (max 2 revisions)
  8. Save RunRecord             Firestore; status visible to the web page while running
POST /runs/{id}/decision {approve|override, note}
```

**ADK notes**
- Steps 1–8 run inside one custom ADK agent (`agents/pipeline.py`) so the selected advisers can be chosen at run time.
- Run the selected advisers concurrently (ADK `ParallelAgent`, or `asyncio.gather` inside the custom agent if building a `ParallelAgent` per request is awkward). Wrap each in a timeout; on failure, record it and continue.
- Advisers need tools **and** JSON output. If your ADK version doesn't allow `output_schema` together with tools, ask for JSON in the prompt (already done) and validate with Pydantic after the call; retry once on invalid JSON.
- Steps 7's loop can be an ADK `LoopAgent` (max 3 iterations) or a plain loop.

---

## 4. Data contracts

All in `core/schemas.py` (Pydantic v2): `Fact`, `DocumentHit`, `EvidencePack`, `DecisionBrief`, `Criterion`, `RoutingDecision`, `AdviserScore`, `AdviserPosition`, `Claim`, `CriterionScore`, `BlockingFinding`, `ClaimVerdict`, `Scorecard`, `Memo`, `FinalVerdict`, `HumanDecision`, `RunRecord`.

**Do not change these shapes without updating every module and the evals.**

---

## 5. Knowledge layer

### BigQuery dataset `colosseum` (agents may read)
| Table | What it holds |
|---|---|
| `facts` | The fact store: one row per fact (planted + background). Schema in `schemas/facts_schema.json`. |
| `transactions_monthly` | month, gateway, issuer_bank, txn_count, success_count, volume_inr_crore |
| `mandates_monthly` | month, gateway, mandate_type (UPI_AUTOPAY/ENACH), attempts, successes |
| `gateway_health_monthly` | month, gateway, uptime_pct, p95_latency_ms, source |
| `incidents` | incident_id, date, gateway, type, severity, duration_minutes, collections_delayed_inr_crore, description, certin_report_hours |
| `support_tickets_monthly` | month, category, gateway, tickets, breached_30d |
| `sales_pipeline` | merchant_id, segment, city, expected_monthly_volume_inr_crore, needs_international_cards, stage |
| `fees_quotes` | gateway, fee_item, value, unit, effective_from, source_doc |

### BigQuery dataset `colosseum_eval` (never visible to agents)
`planted_facts_answer_key`, `routing_tests`, `moderator_tests`. Give the app's service account access to `colosseum` only.

### Approved queries (`knowledge/queries.py`)
Agents call `query_table(name, params)`; they never write SQL.

| Name | Returns |
|---|---|
| `success_rate_by_bank` | success % by gateway and issuer bank over a date range |
| `success_rate_overall` | success % by gateway over a date range |
| `autopay_success` | mandate success % by gateway and mandate type |
| `uptime_latency` | average uptime and p95 latency by gateway |
| `incidents_12m` | incidents by gateway, type and severity in the last 12 months |
| `ticket_trend` | tickets by month for a category and gateway |
| `pipeline_summary` | merchant count and total expected volume |
| `fee_quotes` | all fee items for a gateway |

### Documents
Nine PDFs in `gs://BUCKET/docs/`, indexed by an Agent Search data store. `search_documents` returns `DocumentHit`s; `doc_ref` = file name + page.

---

## 6. Tools (adviser-facing)

```python
get_facts(keywords: str, domains: list[str] | None = None, top_k: int = 10) -> list[Fact]
query_table(name: str, params: dict) -> list[dict]
search_documents(question: str, top_k: int = 5) -> list[DocumentHit]
calculate(expression: str) -> float   # safe arithmetic only (+ - * / parentheses, numbers); no eval of arbitrary code
```

---

## 7. Harness

### Routing (`config/routing.yaml`)
1. Gemini Flash turns the brief into a **routing query**: 5–12 topic phrases (`prompts/harness_routing.md`).
2. Embed the routing query (`RETRIEVAL_QUERY`). Each adviser's capability statements (`config/advisers.yaml`) are embedded once (`RETRIEVAL_DOCUMENT`) and cached.
3. Adviser score = **max** cosine similarity across its capability statements.
4. Select advisers with score ≥ (top score − `relative_margin`) or ≥ `absolute_floor`; enforce `min_advisers` / `max_advisers`.
5. Apply `always_include` keyword rules (for example data or vendor → InfoSec).
6. Save `RoutingDecision` (scores, reasons, selected) to Firestore and show it in the "Why these advisers" panel.

Tune `relative_margin` and `absolute_floor` with `evals/routing_tests.csv` until all 8 briefs pass.

### Evidence packs (`harness/context_builder.py`)
For each selected adviser:
1. Candidates: facts whose `domain` is the adviser's, or whose `related_domains` include it; drop superseded facts.
2. Rank (v1): keyword overlap with routing query + brief across `statement`, `topic`, `metric`; tie-break by reliability, then newest `as_of`.
3. If ≤ `max_facts_per_adviser` (25): use them all.
4. Else aggregate (`knowledge/aggregator.py`): keep only the latest fact per (entity, metric); keep the top 25 as full facts; summarise the rest per topic in one line each, listing the fact_ids covered.
5. Return `EvidencePack` with `total_matching_facts` and `truncated`.

---

## 8. Moderator

### Claim checks (step 5)
Run in code first, in order (`config/moderator.yaml`):
`evidence_present` → `evidence_exists` → `not_superseded` → `number_matches` (recompute `formula` with `calculate`; 1% tolerance) → `reliability` (FLAG if only low-reliability evidence).
Claims that pass go to Gemini Pro (`prompts/moderator_claims.md`) for a support check: KEEP / FLAG / STRIKE. Batch claims per adviser to save calls. Blocking findings from advisers with `can_block: true` are never struck.

### Final-answer check (step 7)
Code checks: every criterion addressed; every selected adviser represented; unresolved blockers reflected; dissent present if any adviser disagreed; memo numbers match evidence. Then Gemini Pro (`prompts/moderator_final.md`) gives PASS / INCOMPLETE / WRONG with revision instructions. Max 2 revisions; the final verdict and revision count are shown on the memo.

---

## 9. Scorecard and Scribe
- `agents/scorecard.py`: for each criterion, average the scores from its owner advisers for each option; weighted total per option; list options blocked by unresolved findings. Pure Python, unit-tested.
- `agents/scribe.py`: Gemini Pro with `prompts/scribe.md`; returns `Memo`. Uses only KEEP/FLAG claims.

---

## 10. Records (Firestore)
| Collection | Document |
|---|---|
| `runs/{run_id}` | `RunRecord` + `status` (routing, advisers, moderating, writing, checking, done, failed) + progress for the UI |
| `routing_logs/{run_id}` | `RoutingDecision` |
| `human_decisions/{run_id}` | `HumanDecision` |
| `cache_embeddings/{hash}` | cached capability embeddings |

---

## 11. API (FastAPI, same Cloud Run service as the web page)
| Method | Path | Does |
|---|---|---|
| POST | `/runs` | Start a run from a `DecisionBrief`; returns `run_id` immediately; work continues in the background |
| GET | `/runs/{run_id}` | Current status and everything produced so far (the page polls this every 2 s) |
| POST | `/runs/{run_id}/decision` | Approve or override; override of a blocking finding requires a note |
| GET | `/advisers` | The registry (for the page) |
| GET | `/briefs/sample` | `evals/brief_DEC-001.json` |

---

## 12. Built to scale: 100+ facts, live data, more advisers

| Growth | What happens | What to change |
|---|---|---|
| 100–1,000 facts | Evidence packs stay capped at 25 per adviser; the aggregator summarises the rest with fact_ids kept | Nothing (v1 handles it). Day 10 scale test proves it with 1,000 synthetic facts |
| 1,000+ facts | Keyword ranking gets weak | Set `retrieval.ranking: vector`: store fact embeddings in BigQuery (`ML.GENERATE_EMBEDDING`) and rank with `VECTOR_SEARCH`; same `EvidencePack` output |
| Live data | New values arrive daily | Add a connector that emits `Fact`s; upsert by (entity, metric, as_of); when a newer fact arrives for the same (entity, metric), set `superseded_by` on the old one; schedule with Cloud Scheduler → Cloud Run job |
| New documents | New contracts, reports | Agent Search indexes them automatically; optional Document AI extraction creates facts |
| More advisers | e.g. HR, Risk, Treasury | Add to `config/advisers.yaml` + a prompt file; routing picks them up; scorecard uses `criteria_owned` |
| More decisions | Any decision type | New brief JSON; routing decides who joins |

**Rule:** anything that grows (facts, tables, documents, advisers) is reached only through the retriever, named queries, Agent Search or the registry. No module reads "everything".

---

## 13. Folder layout
```
colosseum/
  ARCHITECTURE.md  BUILD_GUIDE.md  README.md
  config/        settings.yaml advisers.yaml routing.yaml moderator.yaml
  prompts/       harness_routing.md moderator_claims.md moderator_final.md scribe.md advisers/*.md
  core/          schemas.py settings.py registry.py ledger.py
  knowledge/     fact_store.py queries.py documents.py aggregator.py
  connectors/    base.py csv_loader.py
  harness/       routing_query.py embeddings.py router.py context_builder.py
  agents/        tools.py adviser_factory.py moderator.py scorecard.py scribe.py pipeline.py
  api/           main.py
  web/           index.html app.js styles.css
  evals/         run_evals.py + test data
  data/ docs/ schemas/ scripts/
  tests/         unit tests for scorecard, moderator code checks, router selection, aggregator
```

## 14. Not in v1
Second debate round, red team, live connectors (interface only), voice, Document AI extraction, Agent Runtime hosting, Looker dashboards.
