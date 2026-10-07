# Colosseum starter pack

Colosseum is an AI decision room: an agentic harness routes a decision to the advisers who can contribute, each argues with cited evidence, a moderator checks every claim and the final memo, and a human decides. Built only on Google tools.

Demo company: **Legion Capital** (fictional NBFC). Demo decision: move 40% of payment volume from Arvanta Payments to Brevis Pay.

## What's in here
| Folder / file | What it is | Used on |
|---|---|---|
| `BUILD_GUIDE.md` | Day-by-day milestones, tasks and Antigravity prompts | Every day |
| `ARCHITECTURE.md` | The build spec. Give it to Antigravity every session | Every day |
| `data/` | 8 CSV tables for BigQuery, incl. the fact store (`facts.csv`: 71 planted + 54 background facts) | Day 2 |
| `docs/` | 9 company documents (PDF) for Agent Search | Day 2 |
| `scripts/load_data.sh` | Loads data to BigQuery and docs to Cloud Storage | Day 2 |
| `config/` | Settings, adviser registry, routing and moderator rules | Day 2 onwards |
| `prompts/` | Adviser, harness, moderator and Scribe prompts | Day 4 onwards |
| `core/schemas.py` | Data contracts every module uses | Day 3 onwards |
| `evals/` | Answer key, routing tests, moderator tests, sample brief, expected outcome | Days 4–7 |

Everything is fictional: Legion Capital, Arvanta Payments, Brevis Pay, Sentrix Risk, Vireo Finance, Coral Bank and Tamra Bank.

**Never give agents access to `evals/` or the `colosseum_eval` dataset.** That's the answer key.
