# Verify report

`make verify` on 2026-10-23, on a copy of the tree with no git history: Darwin arm64, Python 3.12.13, no key in any check's environment.

| Check | Command | Result | Time |
| --- | --- | --- | --- |
| Sync | `uv sync --frozen` | done | 87 s |
| Lint | `ruff check .` | All checks passed! | 0.6 s |
| Format | `ruff format --check .` | 152 files already formatted | 0.0 s |
| Offline tests | `pytest -q` | 361 passed, 3 skipped in 12.73s | 15.4 s |
| Guardrail eval | `evals.run_routing_eval --guardrail-only` | overall: 100.0%  (28/28) | 1.0 s |
| Eval replay | `evals.run_evals` | pass-all traces 94/100, red_team 40/40 | 5.6 s |

all checks passed, in 22.6 s after the sync.
