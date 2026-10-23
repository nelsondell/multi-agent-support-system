# Verify report

`make verify` on 2026-10-23, on a copy of the tree with no git history: Darwin arm64, Python 3.12.13, no key in any check's environment.

| Check | Command | Result | Time |
| --- | --- | --- | --- |
| Sync | `uv sync --frozen` | done | 35 s |
| Lint | `ruff check .` | All checks passed! | 1.5 s |
| Format | `ruff format --check .` | 146 files already formatted | 0.0 s |
| Offline tests | `pytest -q` | 350 passed, 3 skipped in 13.84s | 16.2 s |
| Guardrail eval | `evals.run_routing_eval --guardrail-only` | overall: 100.0%  (28/28) | 0.9 s |
| Eval replay | `evals.run_evals` | pass-all traces 95/100, red_team 40/40 | 5.5 s |

all checks passed, in 24.0 s after the sync.
