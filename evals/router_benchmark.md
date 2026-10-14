# Router model benchmark

Every row of `evals/routing_golden_set.jsonl` (15 rows) routed 2 times per candidate through router prompt v5, 5 calls at a time. Written by `uv run python -m evals.run_model_benchmark` on 2026-10-14.

| Candidate | Accuracy | p50 latency | p90 latency | Tokens in / out per call | USD per 1k router calls |
| --- | --- | --- | --- | --- | --- |
| `gpt-4o-mini`, temperature 0.0 | 24/30 | 1.3 s | 1.7 s | 1937 / 74 | 0.34 |
| `gpt-6-luna`, none | 24/30 | 1.8 s | 2.2 s | 1935 / 80 | 0.23 |
| `gpt-6-luna`, low | 26/30 | 2.2 s | 3.2 s | 1935 / 131 | 0.26 |
| `gpt-6-luna`, medium | 26/30 | 1.9 s | 3.0 s | 1935 / 155 | 0.27 |
| `gpt-6-sol`, none | 25/30 | 1.9 s | 2.3 s | 1935 / 81 | 4.69 |

Misses, with how many of the runs missed:

- `gpt-4o-mini`, temperature 0.0: A01 (expected support, got knowledge) ×2, A02 (expected knowledge, got support) ×2, A03 (expected support, got support,support) ×2
- `gpt-6-luna`, none: A01 (expected support, got knowledge) ×2, A03 (expected support, got knowledge,support) ×2, S08 (expected support, got knowledge) ×2
- `gpt-6-luna`, low: A01 (expected support, got knowledge) ×1, A03 (expected support, got knowledge,support) ×2, S08 (expected support, got knowledge) ×1
- `gpt-6-luna`, medium: A01 (expected support, got knowledge) ×2, A03 (expected support, got knowledge,support) ×2
- `gpt-6-sol`, none: A01 (expected support, got knowledge) ×2, A03 (expected support, got knowledge,support) ×2, S08 (expected support, got knowledge) ×1

Prices are USD per million tokens, input / output, OpenAI list prices on 2026-10-14: `gpt-4o-mini` 0.15 / 0.60; `gpt-6-luna` 0.10 / 0.50; `gpt-6-sol` 2.00 / 10.00.
