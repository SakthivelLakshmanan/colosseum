# Colosseum — Build guide (one person, 2 hours a day)

Day 1 is done (rules, Google Cloud setup, model tests). This guide covers Days 2–11. Each day has a **milestone**, a **task breakdown** that adds up to 120 minutes, and the **Antigravity prompts** to paste. Always start an Antigravity session with: *"Read ARCHITECTURE.md and follow it."*

---

## Day 2 — Knowledge in the cloud · Milestone: **Data ready**

| # | Task | Min |
|---|---|---|
| 2.1 | Review the knowledge pack | 25 |
| 2.2 | Load data and documents into Google Cloud | 35 |
| 2.3 | Set up Agent Search over the documents | 40 |
| 2.4 | Create the project skeleton in Antigravity | 20 |

### 2.1 Review the knowledge pack (25 min)
- [ ] Open `Colosseum_Knowledge_Pack.xlsx` → **Start Here**.
- [ ] Skim **Planted Facts** adviser by adviser (71 facts; 10+ each). Check the numbers feel realistic.
- [ ] Read **Expected Outcome**. This is what a good run should recommend.
- [ ] If you want a number changed, note it and ask Claude to regenerate the pack. **Don't edit the CSVs by hand**; data, documents and facts must stay consistent.

### 2.2 Load data and documents (35 min)
- [ ] Open Cloud Shell. Click **⋮ → Upload** and upload `colosseum_starter_pack.zip`.
- [ ] Run (use your bucket name):
```
unzip -o colosseum_starter_pack.zip && cd colosseum_starter_pack
bash scripts/load_data.sh colosseum-docs-sl
```
- [ ] Check the output ends with: 6 domains / 125 facts, Arvanta 97.8 / Brevis 97.6, 30 merchants / 45.0 crore, and 9 PDFs listed.
- [ ] In the BigQuery console, open dataset `colosseum` → table `facts` → **Preview**. You should see rows like FIN-01.

### 2.3 Agent Search (40 min)
- [ ] In the console search bar, open **Agent Search** (it may still be labelled *AI Applications* or *Vertex AI Search*).
- [ ] **Create app** → **Custom search (general)**. Name: `colosseum-search`. Location: **global**. If asked about Enterprise features, turn them **on** (needed for snippets and answers).
- [ ] **Create data store** → **Cloud Storage** → `gs://YOUR_BUCKET/docs/*` → **Unstructured documents**. Name: `colosseum-docs`. If offered a document parser, pick **Layout parser**.
- [ ] Wait for indexing (10–30 minutes; do 2.4 meanwhile).
- [ ] In the app's **Preview**, ask these five questions. Each should return the right document:

| Question | Expected source and answer |
|---|---|
| What is Arvanta's early-exit fee and when does it apply? | Arvanta_MSA_Excerpt: ₹25 lakh if volume falls below 80% before 31 Mar 2028 |
| Which Brevis sub-processor is outside India? | Brevis_Security_Pack: Sentrix Risk Pte Ltd, Singapore |
| When does the board want cross-border acceptance live? | Board_Minutes_Sep2026: by 31 March 2027 |
| How long will migration to Brevis take? | Ops_Review_Q3_2026: 6 weeks internally; vendor claims under 4 |
| What is Brevis's dispute fee? | Brevis_Draft_Agreement: ₹250 per dispute |

- [ ] Copy the **app (engine) ID** and **data store ID** into the Setup tab and `config/settings.yaml`.

### 2.4 Project skeleton (20 min)
- [ ] On your laptop, unzip `colosseum_starter_pack.zip` and open the folder in Antigravity.
- [ ] Fill in `config/settings.yaml`: project ID, bucket, model IDs (from the Setup tab). Agent Search IDs once 2.3 is done.
- [ ] Paste into Antigravity:
> Read ARCHITECTURE.md. Create the folder layout from section 13 with empty Python modules (a docstring and TODOs only, no logic yet). Add requirements.txt (google-adk, google-cloud-bigquery, google-cloud-firestore, google-cloud-discoveryengine, google-genai, fastapi, uvicorn, pydantic, pyyaml, pytest) and a .gitignore. Keep core/schemas.py, config/ and prompts/ unchanged. Then create core/settings.py that loads config/settings.yaml into a typed object, and core/registry.py that loads config/advisers.yaml and the prompt files.
- [ ] Commit to a private GitHub repo.

**Done when:** BigQuery has 8 tables in `colosseum` and 3 in `colosseum_eval`; Agent Search answers the five questions; the skeleton is committed.

---

## Day 3 — Core foundations · Milestone: **Tools work**

| # | Task | Min |
|---|---|---|
| 3.1 | Settings, registry, ledger | 30 |
| 3.2 | Fact store, approved queries, document search | 40 |
| 3.3 | The four adviser tools | 30 |
| 3.4 | Unit tests and manual checks | 20 |

Prompts:
> 3.1 Implement core/ledger.py: save and update a RunRecord in Firestore collection `runs` (status field + progress list), and save RoutingDecision to `routing_logs`. Use settings for names.

> 3.2 Implement knowledge/fact_store.py (read facts from BigQuery `colosseum.facts` into Fact models; filter by domains incl. related_domains; exclude superseded by default), knowledge/queries.py (the 8 named queries in ARCHITECTURE.md §5 as parameterised SQL), and knowledge/documents.py (Agent Search query returning DocumentHit with doc_ref = file name + page).

> 3.3 Implement agents/tools.py: get_facts, query_table, search_documents, calculate (safe arithmetic parser, no eval). These are ADK function tools with clear docstrings.

> 3.4 Write pytest tests for calculate and for fact filtering (superseded FIN-12 must be excluded). Then a small script that prints: get_facts("fees", ["finance"]), query_table("success_rate_by_bank", {}), search_documents("Arvanta exit fee").

**Done when:** the script prints FIN facts (no FIN-12), success rates by bank, and the Arvanta MSA passage.

---

## Day 4 — Six advisers · Milestone: **Advisers work**

| # | Task | Min |
|---|---|---|
| 4.1 | Adviser factory + Finance | 40 |
| 4.2 | All six, run in parallel | 30 |
| 4.3 | Planted-fact eval | 30 |
| 4.4 | Tune prompts | 20 |

Prompts:
> 4.1 Implement agents/adviser_factory.py: build an ADK LlmAgent for any adviser in config/advisers.yaml. Instruction = prompts/advisers/_shared.md formatted with the adviser's name, lens, criteria and blocking rule, plus its own prompt file. Tools from agents/tools.py. The adviser receives the brief and its evidence pack as the user message and must return AdviserPosition JSON; validate with core/schemas.py and retry once on invalid JSON. For now build a simple evidence pack = get_facts for the adviser's domain. Make Finance runnable in `adk web`.

> 4.2 Add a function that runs a list of advisers concurrently with a 60-second timeout each; a failing adviser is recorded and skipped, not fatal.

> 4.3 Implement evals/run_evals.py `planted` mode: run all six advisers on evals/brief_DEC-001.json, then for each adviser compare the fact_ids cited in its claims with planted facts for its domain in colosseum_eval.planted_facts_answer_key. Print coverage per adviser and list missed facts. Also report any claim citing FIN-12 (should be zero).

- [ ] 4.4 Read the misses. Tighten the "What to look at" lists in the adviser prompt files (not the facts). Rerun.

**Done when:** each adviser cites at least 7 of its 10+ planted facts and none cites FIN-12.

---

## Day 5 — Agentic harness · Milestone: **Routing works**

| # | Task | Min |
|---|---|---|
| 5.1 | Routing query, embeddings, cache | 30 |
| 5.2 | Router selection and logging | 30 |
| 5.3 | Routing eval and tuning | 30 |
| 5.4 | Evidence packs with aggregation | 30 |

Prompts:
> 5.1 Implement harness/routing_query.py (Gemini Flash + prompts/harness_routing.md) and harness/embeddings.py (gemini-embedding-001, task types from config/routing.yaml, capability embeddings cached in Firestore keyed by a hash of the text).

> 5.2 Implement harness/router.py exactly as ARCHITECTURE.md §7: max cosine per adviser, margin/floor selection, min/max, always_include rules; return RoutingDecision with a reason per adviser; save it with the ledger.

> 5.3 Add `routing` mode to evals/run_evals.py: run the router on every brief in colosseum_eval.routing_tests; pass if all must_include are selected and no must_exclude is selected. Print a table with scores.

- [ ] Tune `relative_margin`, `absolute_floor` and capability statements until 8/8 pass.

> 5.4 Implement harness/context_builder.py and knowledge/aggregator.py exactly as ARCHITECTURE.md §7 (cap 25, latest per entity+metric, one-line topic summaries that list fact_ids). Replace the simple evidence pack from Day 4.

**Done when:** 8/8 routing tests pass and each selected adviser gets an EvidencePack.

---

## Day 6 — Moderator, scorecard, Scribe · Milestone: **Engine works**

| # | Task | Min |
|---|---|---|
| 6.1 | Claim checks (code + LLM) | 45 |
| 6.2 | Scorecard | 20 |
| 6.3 | Scribe | 25 |
| 6.4 | Final-answer check loop | 30 |

Prompts:
> 6.1 Implement the claim checks in agents/moderator.py per config/moderator.yaml and ARCHITECTURE.md §8: deterministic checks in code first, then a batched Gemini Pro support check with prompts/moderator_claims.md. Blocking findings from can_block advisers are never struck. Return ClaimVerdicts.

> 6.2 Implement agents/scorecard.py per §9 with pytest tests (weights sum to 1, blocked options flagged).

> 6.3 Implement agents/scribe.py with prompts/scribe.md returning a validated Memo; only KEEP/FLAG claims are passed in.

> 6.4 Implement the final-answer check per §8 (code checks + Gemini Pro with prompts/moderator_final.md) and the Scribe ⇄ check loop with max 2 revisions. Then agents/pipeline.py: the full flow from brief to RunRecord, saving progress to Firestore after each step.

**Done when:** one run goes brief → routing → six advisers → claim checks → scorecard → memo → verdict, saved in Firestore.

---

## Day 7 — Evals and API · Milestone: **Evals pass**

| # | Task | Min |
|---|---|---|
| 7.1 | Moderator eval (14 cases) | 35 |
| 7.2 | Full flow ×3 against the expected outcome | 25 |
| 7.3 | Background runs with progress | 30 |
| 7.4 | API endpoints | 30 |

Prompts:
> 7.1 Add `moderator` mode to evals/run_evals.py using colosseum_eval.moderator_tests: claim cases go through the claim checks, memo cases through the final check (build a minimal Memo from the input description). Print expected vs actual.

- [ ] 7.2 Run the pipeline three times. Compare the memo with `evals/expected_outcome.md`: phased move, conditions, no exit fee in phase 1, dissent from Legal/InfoSec.

> 7.3/7.4 Implement api/main.py per ARCHITECTURE.md §11: POST /runs starts the pipeline in the background and returns run_id; GET /runs/{id} returns status and partial results from Firestore; POST /runs/{id}/decision; GET /advisers; GET /briefs/sample. Serve web/ as static files.

**Done when:** at least 12/14 moderator tests match, and the memo matches the expected outcome in 2 of 3 runs.

---

## Day 8 — Web page · Milestone: **Usable on your laptop**

| # | Task | Min |
|---|---|---|
| 8.1 | Brief and "Why these advisers" panel | 40 |
| 8.2 | Debate grid | 40 |
| 8.3 | Memo, verdict badge, approve/override | 40 |

Prompt:
> Build web/index.html, app.js, styles.css (no framework). Screen 1: pre-filled brief from GET /briefs/sample and a Run button. Screen 2: routing panel (score bar per adviser, selected/not, reason) and a 3×2 adviser grid that fills in as GET /runs/{id} progresses; claims show evidence chips (hover = fact statement), struck claims crossed out with the reason, FLAG claims with a warning, blocking findings with a red badge. Screen 3: memo with scorecard table, conditions, phased plan, dissent, key numbers with evidence, the moderator's verdict badge and revision count; Approve / Override buttons (override of a blocker requires a typed note). Plain, readable design.

**Done when:** the whole flow works in your browser on localhost.

---

## Day 9 — Go live · Milestone: **Public URL · no new features after today**

| # | Task | Min |
|---|---|---|
| 9.1 | Deploy to Cloud Run | 60 |
| 9.2 | Test the live URL and fix | 60 |

- [ ] Create a service account `colosseum-app` with: Vertex AI User, Discovery Engine User, BigQuery Job User, BigQuery Data Viewer **on dataset `colosseum` only**, Cloud Datastore User. No access to `colosseum_eval`.
- [ ] Deploy from source to Cloud Run in `asia-south1`, using that service account. (Same approach as your earlier Antigravity deployment.)
- [ ] Run all three test briefs on the public URL from your phone and another browser. Paste any error into Antigravity.

---

## Day 10 — Scale test and record · Milestone: **Video recorded**

| # | Task | Min |
|---|---|---|
| 10.1 | Scale test with 1,000+ facts | 30 |
| 10.2 | Fix bugs and tune | 40 |
| 10.3 | Record the demo video | 50 |

> 10.1 Write scripts/scale_test.py: copy colosseum.facts to colosseum.facts_scale and add 1,000 synthetic facts (random domains, plausible but irrelevant statements, some newer versions of existing metrics that supersede older ones). Point settings at facts_scale, run DEC-001, and report: evidence pack sizes, whether aggregation triggered, run time, and whether the recommendation still matches the expected outcome. Then point settings back.

- [ ] Keep the scale-test numbers for the deck ("same answer with 1,000+ facts, packs capped at 25").
- [ ] Record the video on the live URL following the demo script; record a backup take.

---

## Day 11 — Package and submit · Milestone: **Submitted**

| # | Task | Min |
|---|---|---|
| 11.1 | Edit the video | 25 |
| 11.2 | README (ask Antigravity to draft from ARCHITECTURE.md; add your words) | 25 |
| 11.3 | Deck in Google Slides | 35 |
| 11.4 | Submission text and rubric check | 20 |
| 11.5 | Min instances 1, no keys in repo, budget alert; submit | 15 |

---

## If you fall behind
- **End of Day 3:** skip `search_documents` for now; advisers use facts and tables only (the facts already carry document references).
- **End of Day 5:** keep the routing panel but run all six advisers; tune routing later.
- **End of Day 6:** allow one Scribe revision instead of two.
- **End of Day 7:** build only screens 2 and 3 on Day 8; hard-code the sample brief.
- **End of Day 9, not live:** record on localhost while you keep fixing the deployment.
- **Never cut:** evidence IDs, the moderator's claim checks, the InfoSec block, the final-answer check, the scorecard.
