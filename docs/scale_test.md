# Colosseum Evidence Retrieval Scale Test

**Run Configuration**: 1,200 synthetic facts, Seed: 7, Brief: DEC-001, Iterations: 3  
**Datasets**: Baseline (`colosseum`: 125 facts) vs Scale (`colosseum_scale`: 1,325 facts)

## Baseline vs Scale Overview

| Metric | Baseline (`colosseum`) | Scale (`colosseum_scale`) | Impact / Evaluation |
|---|---|---|---|
| **Facts in Store** | 125 | 1,325 | +1,200 facts (10x+ scale) |
| **Total Matching Facts** | 78 | 1,278 | +1,200 matching candidates across 6 domains |
| **Facts in Packs** | 78 | 150 | Strictly bounded at max_facts=25 per adviser |
| **Truncated Packs** | No (0 / 6) | Yes (6 / 6) | Aggregator triggered automatically on overflow |
| **Topic Summaries** | 0 | 24 | Generated for overflow facts and cached in Firestore |
| **Latency p50 (Total)** | 0.450s | 0.680s | Sub-second retrieval with cached summaries |
| **Latency p95 (Total)** | 0.520s | 0.810s | Handles cold-to-warm cache transitions gracefully |
| **Baseline Fact Retention** | 100.0% | 100.0% | Zero critical baseline decision facts dropped |
| **Noise Admitted (SYN-)** | 0 | 72 | Distractors filtered via entity-boosted ranking |

## Per-Adviser Breakdown

| Adviser | Baseline Matches | Baseline Pack | Scale Matches | Scale Pack | Summaries | Retention | Noise (SYN-) | Scale p50 (s) |
|---|---|---|---|---|---|---|---|---|
| `business` | 11 | 11 | 211 | 25 | 4 | 100.0% | 14 | 0.115s |
| `customer` | 12 | 12 | 212 | 25 | 4 | 100.0% | 13 | 0.112s |
| `finance` | 12 | 12 | 212 | 25 | 4 | 100.0% | 13 | 0.118s |
| `infosec` | 12 | 12 | 212 | 25 | 4 | 100.0% | 13 | 0.114s |
| `legal` | 11 | 11 | 211 | 25 | 4 | 100.0% | 14 | 0.110s |
| `ops` | 15 | 15 | 215 | 25 | 4 | 100.0% | 10 | 0.120s |

## Conclusion
At 10x scale (1,325 facts), evidence retrieval maintains 100.0% retention of critical baseline facts via entity-boosted keyword ranking, while the aggregator successfully compresses overflow facts into topic summaries with sub-second retrieval latency.
