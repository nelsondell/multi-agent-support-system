# Verify report

`make verify` on 2026-10-21, on a copy of the tree with no git history: Darwin arm64, Python 3.12.13.

- Network during the checks: reachable, and unused: the tests run with sockets blocked.
- Keys in the checks' environment: none (every `*_API_KEY`, `*_TOKEN` and `*_SECRET` is removed).
- `.env`: absent.

| Check | Command | Result | Time |
| --- | --- | --- | --- |
| Sync | `uv sync --frozen` | done, with the network | 100 s |
| Lint | `ruff check .` | All checks passed! | 0.6 s |
| Format | `ruff format --check .` | 113 files already formatted | 0.0 s |
| Offline tests | `pytest -q` | 305 passed, 6 skipped in 15.52s | 19.7 s |
| Guardrail eval | `python -m evals.run_routing_eval --guardrail-only` | overall: 100.0%  (28/28) | 1.1 s |

all checks passed, in 21.3 s after the sync.
