# Operations

## Configuration

Everything is read from the environment or `.env` (`app/settings.py`); `.env.example` documents
every setting. The ones that matter most:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | — | Chat model and embeddings. The API fails fast without it. |
| `TAVILY_API_KEY` | — | Web search for general questions. Without it they get a pt-BR "cannot look this up now". |
| `OPERATOR_TOKEN` | — | Bearer token for `/handoffs`. Unset, those endpoints answer 503. |
| `LLM_MODEL` / `LLM_REASONING_EFFORT` | `gpt-6-luna` / `low` | Chosen in [`evals/router_benchmark.md`](../evals/router_benchmark.md). `LLM_TEMPERATURE` is for non-reasoning models only (e.g. `gpt-4o-mini` with `0`). |
| `EMBEDDING_BACKEND` / `EMBEDDING_MODEL` | `openai` / `text-embedding-3-large` | Chosen in [`evals/retrieval_calibration.md`](../evals/retrieval_calibration.md). `local` needs no key (CI uses it). Changing it means rebuilding the index. |
| `RETRIEVAL_CONFIDENCE_THRESHOLD` | `0.52` | Below it, the knowledge agent abstains. Calibrated for the default embedder. |
| `ROUTER_CONFIDENCE_THRESHOLD` / `MAX_HOPS` | `0.6` / `2` | Below the first, a turn escalates before any specialist. The second bounds hand-backs. |
| `HISTORY_WINDOW` / `HISTORY_MAX_MESSAGES` | `6` / `12` | Recent messages kept verbatim; past the limit, older ones fold into a summary. |
| `HANDOFF_TTL_HOURS` | `24` | How long a case waits for an operator before the merchant's next message closes it. |
| `STORAGE` / `DATABASE_URL` | `sqlite` / — | Where checkpoints, cases and turn locks live: SQLite files on one node, or Postgres shared by replicas. |
| `NODE_TIMEOUT_S` / `NODE_ATTEMPTS` | `45` / `2` | LangGraph's cap on one attempt of a node, and attempts on a model failure or timeout before the turn escalates. |
| `LANGSMITH_TRACING` | `false` | Masked LangSmith tracing (with `LANGSMITH_API_KEY`). |
| `LLM_BACKEND` | `openai` | `fake` answers every call deterministically with no key: CI only. |

## Reproducible offline

`make verify` is the one command: `uv sync --frozen`, the only step that uses the network, then
`make verify-offline` (`scripts/verify.py`), which runs ruff, the format check, the suite and the
deterministic guardrail eval with every `*_API_KEY`, `*_TOKEN` and `*_SECRET` removed from their
environment, and writes [`reports/verify.md`](../reports/verify.md). The suite runs with sockets
blocked (`pytest-socket`, in `pyproject.toml`), and a blocked attempt fails the check even inside
a test that passed.

Nothing downloads at runtime. The audit, and what each path does now:

| Dependency | Before | Now |
| --- | --- | --- |
| tiktoken `cl100k_base` (chunking) | downloaded on first use | committed under `app/rag/assets/`, checked against tiktoken's SHA-256 |
| Chroma's local ONNX model (`EMBEDDING_BACKEND=local`) | downloaded on first use | fetched by the image build or `make models`; at runtime its absence stops with that fix |
| Chroma product telemetry | — | a no-op in chromadb 1.5 |
| LangSmith | — | off by default; the tracing tests record uploads in-process |
| OpenAI and Tavily | — | called only with a key; the suite scripts both |

## Docker

`docker compose up` from a fresh clone; nothing is built on the host. The image needs no API key
at build time. The first boot builds the knowledge index from the committed corpus snapshot into
the `index` volume, then starts the API; later boots skip the build. Conversations and open cases
live in a separate `state` volume, so a restart keeps them and rebuilding the index never deletes
one. Without `OPENAI_API_KEY` the first boot stops with a one-line fix and writes nothing.
`API_PORT` moves the host port. The base images are pinned by digest; the build fetches the local
embedding model, so a keyless image boots, indexes and answers with `--network none`.

`/health` says the process is up; `/ready` says this replica can take traffic, answering 503 with
what is missing until the store answers and the index is loaded. The image's healthcheck and
compose wait on `/ready`. On SIGTERM uvicorn stops accepting connections and gives in-flight turns
30 s (`--timeout-graceful-shutdown`; compose waits 40 s) before the lifespan closes the store.
LangGraph's RunControl drain is a feature of LangGraph Platform, not of the library this runs on.

## Scaling out

`make scale` runs the `scale` profile: Postgres, two replicas (`api-a`, `api-b`) and nginx round
robin on `SCALE_PORT` (8080).

| What | One node (default) | Replicas (`STORAGE=postgres`) |
| --- | --- | --- |
| Checkpoints | SQLite file in the `state` volume | Postgres, LangGraph's `AsyncPostgresSaver` |
| Handoff queue | SQLite file | the same table in Postgres; one SQL for both |
| One turn per conversation | an asyncio lock in the process | `pg_advisory_xact_lock` on the thread, held for the turn |
| Knowledge index | the `index` volume | one volume per replica, built from the same snapshot |

The index stays per node because it is read-only and small (148 chunks): every replica builds the
same one from the committed snapshot at first boot, and Chroma's on-disk SQLite is not meant to be
shared between processes. A corpus that outgrows that would move to a shared vector store.

Everything else a turn needs is in Postgres, so any replica can serve any request:

- A case escalated on one replica is listed and answered on another, and the reply is delivered
  by whichever replica gets the merchant's next message.
- Checkpoints are written after every step before the next one starts (`durability="sync"`). If a
  replica dies mid-turn, its lock goes with its connection, and the merchant's retry, sent to the
  other replica by the balancer, finishes the turn from the last step instead of starting over.
  nginx never re-sends a request that was already sent, so a turn cannot run twice.
- Replicas that boot together take turns at creating the tables. LangGraph's migrations build
  indexes `CONCURRENTLY`, so that lock is a polled session lock, not a transaction.

Each replica keeps two pools, one for data and one for turn locks: a turn holds a lock
connection for its whole length and must never wait for a data connection held by a turn that is
waiting for its lock. `tests/test_replicas.py` runs two replicas over one Postgres in CI.

## Fault tolerance in the graph

Every node gets LangGraph's own `TimeoutPolicy` (`NODE_TIMEOUT_S`) and `RetryPolicy`
(`NODE_ATTEMPTS`, on a model failure or a timeout), set once with `set_node_defaults`. The router,
knowledge and support nodes get an `error_handler` that turns a model failure, after its retries,
into a `model_unavailable` escalation. It escalates through `collect`, so an answered step still
goes out. Any other exception is a bug and fails the run. The OpenAI client itself does not retry,
so there is one layer of retries. These replace the try/except each of those nodes carried.

## CI and supply chain

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml), on every push and pull request:

- **Verify**, on ubuntu and macOS: `uv sync --frozen`, then `make verify-offline`. On ubuntu it
  runs inside a fresh network namespace (`unshare --net`), so any lookup or download fails the
  job; macOS has no namespaces, and its tests run with sockets blocked. Each uploads its
  `reports/verify.md`.
- **Secret scan**: gitleaks over the history. Reviewed false positives are listed by fingerprint
  in `.gitleaksignore`.
- **Replicas over Postgres**: `tests/test_replicas.py` against a Postgres service; a skipped test
  fails the job.
- **Image**: `docker compose build`, an SPDX SBOM by syft (uploaded as an artifact), a Trivy scan
  that fails on any critical finding not accepted in `.trivyignore` (each entry with its reason
  and an expiry), then the keyless container smoke tests of `/health`, `/metrics` and `/chat`.
- **Scale profile**: `docker compose --profile scale up`, `/ready` through the balancer, one
  conversation served by both replicas, then one replica killed while the balancer keeps
  answering.

Third-party actions are pinned by commit SHA. Dependabot proposes weekly updates for `uv.lock`,
the actions and the base-image digests (`.github/dependabot.yml`).

## Observability

- **Logs.** `structlog` JSON, masked by a processor on every event, so no node can log a CPF even by
  accident; ids the system issues (trace ids, protocols, transaction ids) are never masked away.
  Every node is wrapped by `instrument_node`, so timing and outcome fields are uniform. The merchant
  id is pseudonymised.
- **Metrics.** `GET /metrics`: `support_escalations_total{reason}` and
  `support_node_latency_seconds{node}`. Escalation reasons are a typed enum: `low_router_confidence`,
  `repeated_misroute`, `low_retrieval_confidence`, `insufficient_sources`, `individual_case`,
  `tool_unavailable`, `model_unavailable`. In the last timed real run, a turn took 4 to 6.5 s,
  and 9 s for a compound request.
- **Tracing.** Optional LangSmith tracing through one client that masks inputs and outputs before
  upload. Off by default, and off means off: it also overrides a `LANGSMITH_TRACING=true` LangChain
  would otherwise act on, unmasked. It costs nothing per request and can't fail one.
- **Guardrail outcomes** are logged as `guardrail.blocked`, with the outcome (`prompt_injection`
  or `identity_swap`) and the masked message.
