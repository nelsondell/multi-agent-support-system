# Verify report

`make verify` on 2026-10-21, on a copy of the tree with no git history: Darwin arm64, Python 3.12.13.

- Network during the checks: reachable, and unused: the tests run with sockets blocked.
- Keys in the checks' environment: none (every `*_API_KEY`, `*_TOKEN` and `*_SECRET` is removed).
- `.env`: absent.

| Check | Command | Result | Time |
| --- | --- | --- | --- |
| Sync | `uv sync --frozen` | done, with the network | 94 s |
| Lint | `ruff check .` | All checks passed! | 0.9 s |
| Format | `ruff format --check .` | 138 files already formatted | 0.2 s |
| Offline tests | `pytest -q` | 360 passed, 10 skipped in 30.91s | 37.9 s |
| Guardrail eval | `python -m evals.run_routing_eval --guardrail-only` | overall: 100.0%  (28/28) | 2.0 s |

all checks passed, in 40.9 s after the sync.
