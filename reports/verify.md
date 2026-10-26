# Verify report

`make verify` on 2026-10-26, at `e7d1a62` with uncommitted changes: Darwin arm64, Python 3.12.13, no key in any check's environment.

| Check | Command | Result | Time |
| --- | --- | --- | --- |
| Sync | `uv sync --frozen` | done | 0 s |
| Lint | `ruff check .` | All checks passed! | 0.0 s |
| Format | `ruff format --check .` | 165 files already formatted | 0.0 s |
| Offline tests | `pytest -q` | 379 passed, 3 skipped in 9.72s | 11.2 s |
| Guardrail eval | `evals.run_routing_eval --guardrail-only` | overall: 100.0%  (28/28) | 1.0 s |
| Eval replay | `evals.run_evals` | pass-all traces 95/100, red_team 40/40 | 6.0 s |

all checks passed, in 18.3 s after the sync.
