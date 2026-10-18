# Colosseum

**Six AI department heads debate a business decision, check each other's facts, and hand a human a decision memo they can trust.**

Built for the Google AI competition, theme *Future of Work & Enterprise Productivity*. Runs entirely on Google Cloud and Gemini.

**Live demo:** https://colosseum-cutfjcshcq-el.a.run.app. Anyone can browse past decisions; starting a new run needs the access code shared with judges.

---

## The problem

Big decisions in a company (switch a vendor, launch a product, enter a market) need input from Finance, Legal, Security, Operations, Business and Customer teams. In practice that means weeks of meetings and email threads, each team arguing from its own spreadsheet, and a final memo nobody can trace back to evidence. Numbers get misquoted, security objections surface late, and the reasoning behind the decision is lost.

## What Colosseum does

You write a decision brief: the question, the options, the constraints and the criteria with weights. Colosseum then:

1. **Routes** the brief to the advisers who matter, using Gemini embeddings (not keywords).
2. **Debates.** Each adviser (a Gemini agent built with Google ADK) researches the company's own data — facts, BigQuery tables and PDFs — and takes a position with cited evidence.
3. **Checks claims.** A Moderator verifies every claim against the evidence the adviser actually retrieved in this run. A made-up number or a document the adviser never opened is flagged.
4. **Scores in code, not by the model.** The weighted scorecard is computed in Python from the advisers' scores, and InfoSec can **block** an option outright.
5. **Writes the memo.** A Scribe drafts the recommendation, conditions and phased plan.
6. **Does a final check.** The Moderator grades the memo PASS / INCOMPLETE / WRONG: every number must trace to a fact or a shown calculation. It allows up to 2 revisions and keeps the best draft.
7. **Leaves the decision to a human.** A person approves or overrides, with a reason, and that is recorded.

The demo company is **Legion Capital**, a fictional Indian NBFC. Its first decision (DEC-001): *should it move 40% of its payment volume (about ₹600 crore a month) from Arvanta Payments to Brevis Pay?* All companies, people and figures are fictional.

## The advisers

| Adviser | Owns | Special power |
|---|---|---|
| Finance | Cost, pricing, exit fees | — |
| Business | Growth, OKRs, board priorities | — |
| Customer | Complaints, settlements, churn | — |
| Legal | Contracts, regulation, liability | — |
| InfoSec | Data location, security reviews | **Can block an option** |
| Ops | Success rates, uptime, migration risk | — |

Supporting agents: a **Moderator** (claim checks and the final memo check) and a **Scribe** (memo writer).

## Architecture

```
Browser (web/)  ──►  Cloud Run: FastAPI (api/main.py)
                         │
                         ├─ Routing ............ gemini-embedding-001 → pick advisers
                         ├─ Advisers (×≤6) ..... Google ADK agents on Gemini 3.8 Flash
                         │     tools: get_facts · query_table · search_documents · calculate
                         │        ├─ facts ........ data/facts.csv + topic summaries
                         │        ├─ tables ....... BigQuery dataset `colosseum`
                         │        └─ documents .... Vertex AI Agent Search over PDFs in Cloud Storage
                         ├─ Moderator .......... Gemini 3.1 Pro + code checks (IDs, numbers, formulas)
                         ├─ Scorecard .......... pure Python (weights, blocks)
                         ├─ Scribe ............. Gemini 3.1 Pro
                         └─ Run ledger ......... Firestore (runs, routing logs, human decisions)
```

### Google stack

| Need | Google service |
|---|---|
| Reasoning models | Vertex AI Gemini 3.8 Flash (advisers), Gemini 3.1 Pro (Moderator, Scribe, final check) |
| Routing | gemini-embedding-001 |
| Agent framework | Google Agent Development Kit (ADK) |
| Company data | BigQuery |
| Document search | Vertex AI Agent Search (Discovery Engine) + Cloud Storage |
| Run history and audit trail | Firestore |
| Hosting | Cloud Run (asia-south1), built with Cloud Build, images in Artifact Registry |
| Secrets | Secret Manager |
| Access control | IAM (dedicated, least-privilege service account) |
| Development | Google Antigravity |

## What makes it trustworthy

- **Every claim cites evidence.** Fact IDs (e.g. `FIN-03`) or document pages (e.g. `Brevis_Draft_Agreement.pdf#p2`).
- **Evidence must be real and retrieved.** The Moderator rejects any ID that doesn't exist, and any document reference that the adviser's own search didn't return in this run.
- **Numbers are checked by code.** A key number in the memo must match a fact directly, or come with a formula that evaluates to it (within 0.5%). Units are normalised (₹, lakh, crore, per month or per year) before comparing.
- **The scorecard is arithmetic, not opinion.** Gemini proposes scores with evidence; Python does the weighting.
- **Security can veto.** A blocking InfoSec finding overrides a higher score.
- **A human has the last word.** Approve or override, with a recorded reason.

## Results

### Evaluation

The answer key lives in a separate BigQuery dataset (`colosseum_eval`). The app's service account has **no access** to it, and no eval content is ever placed in a prompt.

| Eval | Result |
|---|---|
| Outcome checklist on DEC-001 (8 expected elements of a good memo) | **8/8 (100%)**, on 2 independent fresh runs, both final verdict **PASS** |
| Routing (right advisers chosen) | **8/8** |
| Live run on Cloud Run (DEC-001) | Final verdict **PASS** |
| Unit and integration tests | 112 passing |

### Sample outcome (DEC-001)

- **Recommendation:** approve a *phased* move to Brevis Pay. Start at about 15% once conditions are met, then scale to 40% after a 60-day review, before 31 March 2027.
- **Scorecard:** Option B 3.57 vs Option A 2.70. **However, B is blocked by InfoSec** until Brevis's India-only processing add-on is live (card data currently processed in Singapore, `SEC-01`).
- **Conditions:** India-only processing, closing the open pen-test finding, a current SOC 2 Type II report, five contract fixes, and keeping weaker banks on Arvanta so the success rate doesn't fall.

### Scale test (honest numbers)

We grew the knowledge base 10× by adding 1,200 realistic decoy facts (125 → 1,325) and re-ran retrieval:

- Each adviser's evidence pack stays **capped at 25 facts** (150 in total); overflow is summarised by topic (35 → 162 summaries).
- **71% of the evidence chosen at normal size is still chosen** at 10×, and **43 of the 150 pack slots went to decoys**.
- Pack-building time rose to minutes at 10× (p50 about 2–3 min, p95 up to about 26 min), mostly first-time summary generation that is cached afterwards.

**Next improvement:** adviser-specific vector search plus re-ranking, so ranking quality holds at large scale. Full results are in `docs/scale_test.md`.

## Security design

- **Access code.** Starting a run or recording a human decision needs an access code (`X-Access-Code` header). The code lives only in Secret Manager and is mounted into Cloud Run at runtime. It is never in code, config or the browser bundle.
- **Least-privilege service account** `colosseum-app`:
  - `roles/aiplatform.user` — call Gemini
  - `roles/datastore.user` — Firestore run ledger
  - `roles/bigquery.jobUser` + **BigQuery Data Viewer on the `colosseum` dataset only** — no access to `colosseum_eval`
  - `roles/discoveryengine.viewer` — read-only document search
- **Cost and abuse guard.** One active run at a time (`MAX_ACTIVE_RUNS=1`, returns HTTP 429 otherwise), max 1 instance, scale to zero when idle, and a budget alert.
- **Nothing sensitive in the image.** `.gcloudignore` excludes `.env*`, key files, `evals/` and tests.
- **All data is fictional.** No real customers, companies or personal data.

## Run it locally

Prerequisites: Python 3.11+, the Google Cloud SDK, and a Google Cloud project with Vertex AI, BigQuery, Firestore, Cloud Storage and Agent Search enabled.

```bash
git clone <this repo> && cd colosseum
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
gcloud auth application-default login

# Set project_id, dataset, bucket and Agent Search engine in:
#   config/settings.yaml

uvicorn api.main:app --port 8080
# open http://localhost:8080
```

Run the tests with `pytest`.

## Deploy to Cloud Run

```bash
gcloud run deploy colosseum \
  --source . \
  --region asia-south1 \
  --service-account colosseum-app@<PROJECT_ID>.iam.gserviceaccount.com \
  --set-secrets ACCESS_CODE=colosseum-access-code:latest \
  --set-env-vars MAX_ACTIVE_RUNS=1 \
  --no-cpu-throttling --min-instances 0 --max-instances 1 \
  --cpu 1 --memory 2Gi --concurrency 40 --timeout 300 \
  --allow-unauthenticated
```

`--allow-unauthenticated` lets anyone *view* the UI and past runs; starting a run still requires the access code.

## Repository layout

```
api/          FastAPI app (endpoints, access-code guard, run limiter)
agents/       adviser factory, tools, moderator, pipeline
harness/      routing (embeddings, router) and evidence-pack builder
knowledge/    fact store, document search (Agent Search)
connectors/   data loaders (CSV today; the plug-in point for live enterprise systems)
core/         schemas, settings, Gemini client, Firestore ledger
schemas/      JSON schema for the facts file
prompts/      adviser, moderator, scribe prompts
config/       settings.yaml
data/         facts.csv (fictional Legion Capital facts)
adk_apps/     ADK app for trying a single adviser on its own
web/          single-page UI
scripts/      scale test and utilities
tests/        pytest suite
```

More detail: [ARCHITECTURE.md](ARCHITECTURE.md).

## What's next

- Adviser-specific vector search and re-ranking for large knowledge bases.
- More decision templates (the catalog already lists 23 decisions Legion Capital could debate).
- Connectors to live enterprise systems (ERP, ticketing, contract repositories) in place of the demo data.
- Per-team sign-off on the human decision step.
