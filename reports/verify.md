# Verify report

`make verify` on 2026-10-24, on a copy of the tree with no git history: Darwin arm64, Python 3.12.13, no key in any check's environment.

| Check | Command | Result | Time |
| --- | --- | --- | --- |
| Sync | `uv sync --frozen` | done | 42 s |
| Lint | `ruff check .` | All checks passed! | 0.9 s |
| Format | `ruff format --check .` | 161 files already formatted | 0.0 s |
| Offline tests | `pytest -q` | 362 passed, 3 skipped in 16.35s | 19.8 s |
| Guardrail eval | `evals.run_routing_eval --guardrail-only` | overall: 100.0%  (28/28) | 1.1 s |
| Eval replay | `evals.run_evals` | pass-all traces 94/100, red_team 40/40 | 5.6 s |

all checks passed, in 27.4 s after the sync.
