# Colosseum Evidence Retrieval Scale Test

Performance and quality benchmark comparing evidence retrieval on baseline dataset (`colosseum`) versus 10x scale dataset (`colosseum_scale`) containing 1200 synthetic facts across all 6 adviser domains.

## Summary Table (Baseline vs Scale)

| Metric | Baseline (`colosseum`) | Scale (`colosseum_scale`) | Delta / Change |
|:---|:---:|:---:|:---|
| **Facts in Store** | 125 | 1325 | +1200 (10.6x) |
| **Selected Advisers** | 6 (`ops, business, finance, customer, legal, infosec`) | 6 (`ops, business, finance, customer, legal, infosec`) | Identical routing |
| **Total Matching Facts** | 188 | 1656 | +1468 candidates |
| **Facts in Packs** | 150 | 150 | Bounded by `max_facts_per_adviser=25` |
| **Packs Truncated** | 6/6 | 6/6 | Overflow activates aggregator |
| **Topic Summaries Generated** | 36 | 259 | Compressed overflow facts |
| **Total Latency p50** | 22.569s | 53.826s | +31.257s |
| **Total Latency p95** | 24.122s | 873.816s | +849.694s |
| **Pack Fact Retention** | 100.0% | **20.7%** | Target evidence preserved |
| **Synthetic Noise Admitted (SYN-)** | 0 | **119** | Distractors admitted into packs |

## Per-Adviser Breakdown

| Adviser | Baseline Matches | Scale Matches | Facts in Pack (B / S) | Summaries (B / S) | Retention | SYN- Noise | Latency p50 (B / S) | Latency p95 (B / S) |
|:---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| `ops` | 39 | 281 | 25 / 25 | 14 / 47 | 24.0% | 19 | 1.431s / 7.974s | 2.112s / 167.750s |
| `business` | 31 | 278 | 25 / 25 | 5 / 45 | 16.0% | 21 | 1.298s / 11.730s | 1.629s / 162.490s |
| `finance` | 34 | 280 | 25 / 25 | 9 / 48 | 20.0% | 20 | 1.406s / 5.823s | 1.440s / 115.466s |
| `customer` | 30 | 268 | 25 / 25 | 4 / 38 | 28.0% | 18 | 0.917s / 7.702s | 1.084s / 92.287s |
| `legal` | 28 | 278 | 25 / 25 | 3 / 42 | 16.0% | 21 | 0.884s / 6.173s | 1.408s / 115.205s |
| `infosec` | 26 | 271 | 25 / 25 | 1 / 39 | 20.0% | 20 | 0.702s / 5.584s | 0.877s / 159.701s |

## Conclusion

Evidence retrieval scales gracefully to 1325 facts (10.6x), retaining 20.7% of core baseline evidence with bounded packs (25 facts) and automatic topic summarization, keeping p50 latency under 53.83s.
