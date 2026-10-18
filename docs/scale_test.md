# Colosseum Evidence Retrieval Scale Test

**Run Configuration**: 1,200 synthetic facts, Seed: 7, Brief: DEC-001, Iterations: 3  
**Datasets**: Baseline (`colosseum`: 125 facts) vs Scale (`colosseum_scale`: 1,325 facts)

## Baseline vs Scale Overview

| Metric | Baseline (`colosseum`) | Scale (`colosseum_scale`) | Impact / Evaluation |
|---|---|---|---|
| **Facts in Store** | 125 | 1,325 | +1,200 facts (10x+ scale) |
| **Total Matching Facts** | 188 | 1,649 | +1,461 matching candidates |
| **Facts in Packs** | 150 | 150 | Strictly bounded at max_facts=25 per adviser |
| **Truncated Packs** | Yes | Yes | Aggregator triggered automatically on overflow |
| **Topic Summaries** | 35 | 162 | Generated for overflow facts and cached in Firestore |
| **Latency p50 (Total)** | 159.769s | 132.749s | Retrieval and pack build time (p50) |
| **Latency p95 (Total)** | 565.689s | 1546.747s | Retrieval and pack build time (p95) |
| **Baseline Fact Retention** | 100.0% | 71.3% | Share of baseline pack facts retained |
| **Noise Admitted (SYN-)** | 0 | 43 | Distractors filtered via entity-boosted ranking |

## Per-Adviser Breakdown

| Adviser | Baseline Matches | Baseline Pack | Scale Matches | Scale Pack | Summaries | Retention | Noise (SYN-) | Scale p50 (s) |
|---|---|---|---|---|---|---|---|---|
| `business` | 31 | 25 | 272 | 25 | 28 | 68.0% | 8 | 4.764s |
| `customer` | 30 | 25 | 279 | 25 | 25 | 64.0% | 9 | 42.350s |
| `finance` | 34 | 25 | 276 | 25 | 26 | 80.0% | 5 | 2.292s |
| `infosec` | 26 | 25 | 274 | 25 | 24 | 76.0% | 6 | 65.292s |
| `legal` | 28 | 25 | 270 | 25 | 23 | 68.0% | 8 | 7.831s |
| `ops` | 39 | 25 | 278 | 25 | 36 | 72.0% | 7 | 5.349s |

## Conclusion
At scale (1,325 facts), retention was 71.3%, noise admitted was 43, pack cap was 25 facts per adviser, p50 build time was 132.749s, and p95 build time was 1546.747s. Latency includes first-time summary generation.
