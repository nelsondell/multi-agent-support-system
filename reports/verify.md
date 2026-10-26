# Verify report

`make verify` on 2026-10-26, at `4f8fe15`: Darwin arm64, Python 3.12.13, no key in any check's environment.

| Check | Command | Result | Time |
| --- | --- | --- | --- |
| Sync | `uv sync --frozen` | done | 0 s |
| Lint | `ruff check .` | All checks passed! | 0.3 s |
| Format | `ruff format --check .` | 165 files already formatted | 0.0 s |
| Offline tests | `pytest -q` | 379 passed, 3 skipped in 14.84s | 17.4 s |
| Guardrail eval | `evals.run_routing_eval --guardrail-only` | overall: 100.0%  (28/28) | 1.1 s |
| Eval replay | `evals.run_evals` | pass-all traces 95/100, red_team 40/40 | 5.9 s |

all checks passed, in 24.7 s after the sync.
