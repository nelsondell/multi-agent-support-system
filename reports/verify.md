# Verify report

`make verify` on 2026-10-22, on a copy of the tree with no git history: Darwin arm64, Python 3.12.13, no key in any check's environment.

| Check | Command | Result | Time |
| --- | --- | --- | --- |
| Sync | `uv sync --frozen` | done | 9 s |
| Lint | `ruff check .` | All checks passed! | 0.6 s |
| Format | `ruff format --check .` | 135 files already formatted | 0.0 s |
| Offline tests | `pytest -q` | 345 passed, 3 skipped in 17.59s | 21.7 s |
| Guardrail eval | `evals.run_routing_eval --guardrail-only` | overall: 100.0%  (28/28) | 1.3 s |

all checks passed, in 23.6 s after the sync.
