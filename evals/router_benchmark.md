# Router model benchmark

Every row of `evals/routing_golden_set.jsonl` (28 rows) routed 2 times per candidate through router prompt v9, 5 calls at a time. A follow-up row counts as a hit only when the rewrite also keeps its referent. Written by `uv run python -m evals.run_model_benchmark` on 2026-10-14.

| Candidate | Accuracy | p50 latency | p90 latency | Tokens in / out per call | USD per 1k router calls |
| --- | --- | --- | --- | --- | --- |
| `gpt-4o-mini`, temperature 0.0 | 46/56 | 1.3 s | 1.9 s | 2321 / 80 | 0.40 |
| `gpt-6-luna`, none | 54/56 | 2.3 s | 2.9 s | 2319 / 86 | 0.28 |
| `gpt-6-luna`, low | 54/56 | 2.4 s | 3.7 s | 2319 / 139 | 0.30 |
| `gpt-6-luna`, medium | 54/56 | 2.5 s | 3.8 s | 2319 / 161 | 0.31 |
| `gpt-6-sol`, none | 55/56 | 2.0 s | 2.3 s | 2319 / 85 | 5.49 |

Misses, with how many of the runs missed:

- `gpt-4o-mini`, temperature 0.0: A01 (expected support, got knowledge) ×2, A02 (expected knowledge, got support) ×2, A03 (expected knowledge,support, got support,knowledge) ×1, A03 (expected knowledge,support, got support,support) ×1, C03 (expected knowledge,support, got support,knowledge) ×2, H03 (expected support, got support (lost 7788|89,50)) ×2
- `gpt-6-luna`, none: A01 (expected support, got knowledge) ×2
- `gpt-6-luna`, low: A01 (expected support, got knowledge) ×2
- `gpt-6-luna`, medium: A01 (expected support, got knowledge) ×2
- `gpt-6-sol`, none: A01 (expected support, got knowledge) ×1

## Follow-up resolution

The 9 follow-up rows (`multi_turn` and `human_review`), 5 runs each, on the two efforts the router could use.

| Candidate | Accuracy | p50 latency | p90 latency | Tokens in / out per call | USD per 1k router calls |
| --- | --- | --- | --- | --- | --- |
| `gpt-6-luna`, low | 45/45 | 2.5 s | 3.0 s | 2371 / 152 | 0.31 |
| `gpt-6-luna`, medium | 45/45 | 2.8 s | 4.0 s | 2371 / 186 | 0.33 |

Misses, with how many of the runs missed:

- `gpt-6-luna`, low: none
- `gpt-6-luna`, medium: none

Prices are USD per million tokens, input / output, OpenAI list prices on 2026-10-14: `gpt-4o-mini` 0.15 / 0.60; `gpt-6-luna` 0.10 / 0.50; `gpt-6-sol` 2.00 / 10.00.
