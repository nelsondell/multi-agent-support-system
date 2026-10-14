# Router model benchmark

Every row of `evals/routing_golden_set.jsonl` (15 rows) routed 2 times per candidate through router prompt v4, 5 calls at a time. Written by `uv run python -m evals.run_model_benchmark` on 2026-10-14.

| Candidate | Accuracy | p50 latency | p90 latency | Tokens in / out per call | USD per 1k router calls |
| --- | --- | --- | --- | --- | --- |
| `gpt-4o-mini`, temperature 0.0 | 24/30 | 1.4 s | 2.3 s | 1712 / 65 | 0.30 |
| `gpt-6-luna`, none | 24/30 | 1.8 s | 2.3 s | 1710 / 70 | 0.21 |
| `gpt-6-luna`, low | 26/30 | 2.0 s | 2.9 s | 1710 / 114 | 0.23 |
| `gpt-6-luna`, medium | 26/30 | 2.2 s | 3.1 s | 1710 / 131 | 0.24 |
| `gpt-6-sol`, none | 26/30 | 2.0 s | 2.1 s | 1710 / 71 | 4.14 |

Misses, with how many of the runs missed:

- `gpt-4o-mini`, temperature 0.0: A01 (expected support, got knowledge) ×2, A02 (expected knowledge, got support) ×2, A03 (expected support, got support,knowledge) ×2
- `gpt-6-luna`, none: A01 (expected support, got knowledge) ×2, A03 (expected support, got knowledge,support) ×2, S08 (expected support, got knowledge) ×2
- `gpt-6-luna`, low: A01 (expected support, got knowledge) ×1, A03 (expected support, got knowledge,support) ×2, S08 (expected support, got knowledge) ×1
- `gpt-6-luna`, medium: A01 (expected support, got knowledge) ×2, A03 (expected support, got knowledge,support) ×2
- `gpt-6-sol`, none: A03 (expected support, got knowledge,support) ×2, S08 (expected support, got knowledge) ×2

Prices are USD per million tokens, input / output, OpenAI list prices on 2026-10-14: `gpt-4o-mini` 0.15 / 0.60; `gpt-6-luna` 0.10 / 0.50; `gpt-6-sol` 2.00 / 10.00.
