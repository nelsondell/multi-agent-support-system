# Verify report

`make verify` on 2026-10-25, at `49f04ff` with uncommitted changes: Darwin arm64, Python 3.12.13, no key in any check's environment.

| Check | Command | Result | Time |
| --- | --- | --- | --- |
| Sync | `uv sync --frozen` | done | 0 s |
| Lint | `ruff check .` | All checks passed! | 0.1 s |
| Format | `ruff format --check .` | 163 files already formatted | 0.0 s |
| Offline tests | `pytest -q` | 363 passed, 3 skipped in 9.53s | 10.9 s |
| Guardrail eval | `evals.run_routing_eval --guardrail-only` | overall: 100.0%  (28/28) | 0.9 s |
| Eval replay | `evals.run_evals` | pass-all traces 95/100, red_team 40/40 | 5.5 s |

all checks passed, in 17.3 s after the sync.
