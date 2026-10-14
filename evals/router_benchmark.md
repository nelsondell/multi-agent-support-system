# Router model benchmark

Every row of `evals/routing_golden_set.jsonl` (23 rows) routed 2 times per candidate through router prompt v7, 5 calls at a time. A follow-up row counts as a hit only when the rewrite also keeps its referent. Written by `uv run python -m evals.run_model_benchmark` on 2026-10-14.

| Candidate | Accuracy | p50 latency | p90 latency | Tokens in / out per call | USD per 1k router calls |
| --- | --- | --- | --- | --- | --- |
| `gpt-4o-mini`, temperature 0.0 | 38/46 | 1.3 s | 1.7 s | 2168 / 76 | 0.37 |
| `gpt-6-luna`, none | 40/46 | 1.7 s | 1.9 s | 2166 / 81 | 0.26 |
| `gpt-6-luna`, low | 41/46 | 2.1 s | 2.9 s | 2166 / 133 | 0.28 |
| `gpt-6-luna`, medium | 42/46 | 2.4 s | 3.1 s | 2166 / 152 | 0.29 |
| `gpt-6-sol`, none | 42/46 | 1.9 s | 2.3 s | 2166 / 82 | 5.16 |

Misses, with how many of the runs missed:

- `gpt-4o-mini`, temperature 0.0: A01 (expected support, got knowledge) ×2, A02 (expected knowledge, got support) ×2, A03 (expected support, got support,support) ×2, S08 (expected support, got knowledge) ×2
- `gpt-6-luna`, none: A01 (expected support, got knowledge) ×2, A03 (expected support, got knowledge,support) ×2, S08 (expected support, got knowledge) ×2
- `gpt-6-luna`, low: A01 (expected support, got knowledge) ×2, A03 (expected support, got knowledge,support) ×2, S08 (expected support, got knowledge) ×1
- `gpt-6-luna`, medium: A01 (expected support, got knowledge) ×2, A03 (expected support, got knowledge,support) ×2
- `gpt-6-sol`, none: A01 (expected support, got knowledge) ×1, A03 (expected support, got knowledge,support) ×2, S08 (expected support, got knowledge) ×1

## Follow-up resolution

The 8 `multi_turn` rows, 5 runs each, on the two efforts the router could use.

| Candidate | Accuracy | p50 latency | p90 latency | Tokens in / out per call | USD per 1k router calls |
| --- | --- | --- | --- | --- | --- |
| `gpt-6-luna`, low | 40/40 | 2.2 s | 2.8 s | 2213 / 146 | 0.29 |
| `gpt-6-luna`, medium | 40/40 | 2.6 s | 3.7 s | 2213 / 178 | 0.31 |

Misses, with how many of the runs missed:

- `gpt-6-luna`, low: none
- `gpt-6-luna`, medium: none

Prices are USD per million tokens, input / output, OpenAI list prices on 2026-10-14: `gpt-4o-mini` 0.15 / 0.60; `gpt-6-luna` 0.10 / 0.50; `gpt-6-sol` 2.00 / 10.00.
