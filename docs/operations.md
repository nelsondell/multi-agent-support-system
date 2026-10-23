# Operations

## Configuration

Everything is read from the environment or `.env` (`app/settings.py`); `.env.example` documents
every setting. The ones that matter most:

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | — | Chat model and embeddings. The API fails fast without it. |
| `TAVILY_API_KEY` | — | Web search for general questions. Without it they get a pt-BR "cannot look this up now". |
| `AUTH_JWKS_URL` | `.dev/jwks.json` | The identity provider's public keys: an https URL, or the file `make dev-keys` writes. The API refuses to start without them. |
| `RATE_LIMIT_PER_MINUTE` / `RETENTION_DAYS` | `30` / `90` | Merchant requests per 60 s per replica; days a conversation may sit idle before `make purge` erases it. |
| `LLM_MODEL` / `LLM_REASONING_EFFORT` | `gpt-6-luna` / `low` | Chosen in [`evals/router_benchmark.md`](../evals/router_benchmark.md). `LLM_TEMPERATURE` is for non-reasoning models only (e.g. `gpt-4o-mini` with `0`). |
| `EMBEDDING_BACKEND` / `EMBEDDING_MODEL` | `openai` / `text-embedding-3-large` | Chosen in [`evals/retrieval_calibration.md`](../evals/retrieval_calibration.md). `local` needs no key (CI uses it). Changing it means rebuilding the index. |
| `RETRIEVAL_CONFIDENCE_THRESHOLD` | `0.52` | Below it, the knowledge agent abstains. Calibrated for the default embedder. |
| `ROUTER_CONFIDENCE_THRESHOLD` / `MAX_HOPS` | `0.6` / `2` | Below the first, a turn escalates before any specialist. The second bounds hand-backs. |
| `MAX_PLAN_CHANGES` | `3` | Steps the supervisor may add to a turn's plan for what a step found. `0` runs the router's plan as it is. |
| `MAX_MODEL_CALLS` / `MAX_COST_USD` | `12` / `0.02` | A turn's model budget: past it, the answered part goes out and the rest goes to a person (`budget_exhausted`). |
| `HISTORY_WINDOW` / `HISTORY_MAX_MESSAGES` | `6` / `12` | Recent messages kept verbatim; past the limit, older ones fold into a summary. |
| `HANDOFF_TTL_HOURS` | `24` | How long a case waits for an operator before the merchant's next message closes it. |
| `STORAGE` / `DATABASE_URL` | `sqlite` / — | Where checkpoints, cases and turn locks live: SQLite files on one node, or Postgres shared by replicas. |
| `NODE_TIMEOUT_S` / `NODE_ATTEMPTS` | `45` / `2` | LangGraph's cap on one attempt of a node, and attempts on a model failure or timeout before the turn escalates. |
| `LLM_CALL_TIMEOUT_S` | `20` | One model call's cap: a hung vendor fails the call, so the node retries or degrades instead of timing out whole. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OTLP gRPC collector for the traces; `make observe` sets Jaeger. |
| `LLM_PRICE_INPUT` / `LLM_PRICE_CACHED_INPUT` / `LLM_PRICE_OUTPUT` | `0.10` / `0.01` / `0.50` | USD per million tokens, for the cost metrics. The cached-input price is an assumption until the price list states it. |
| `LANGSMITH_TRACING` | `false` | Masked LangSmith tracing (with `LANGSMITH_API_KEY`). |
| `LLM_BACKEND` | `openai` | `fake` answers every call deterministically with no key: CI only. |

## Reproducible offline

`make verify`: `uv sync --frozen`, the only step allowed the network, then ruff, the format check,
the suite with sockets blocked and the guardrail eval, with no key in their environment
([`reports/verify.md`](../reports/verify.md)). Nothing downloads at runtime:

| Dependency | Before | Now |
| --- | --- | --- |
| tiktoken `cl100k_base` (chunking) | downloaded on first use | committed gzipped under `app/rag/assets/`, checked against tiktoken's SHA-256 |
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
`API_PORT` moves the host port. Base images are pinned by digest, and a keyless image boots,
indexes and answers with `--network none`. The healthcheck waits on `/ready`, which answers 503
naming what is not ready; `/health` only says the process is up. On SIGTERM in-flight turns get
30 s (`--timeout-graceful-shutdown`); LangGraph's RunControl drain is a Platform feature, not in
the library.

## Scaling out

`make scale`: Postgres, two replicas (`api-a`, `api-b`) and nginx round robin on `SCALE_PORT`
(8080). Any replica serves any request; `tests/test_replicas.py` runs two in CI.

| What | One node (default) | Replicas (`STORAGE=postgres`) |
| --- | --- | --- |
| Checkpoints | SQLite file in the `state` volume | `AsyncPostgresSaver`, written after every step (`durability="sync"`) |
| Handoff queue, audit, tickets | SQLite file | the same tables and SQL in Postgres |
| One turn per conversation | an asyncio lock | `pg_advisory_xact_lock`, on a pool of its own |
| A replica dies mid-turn | — | its lock goes with its connection; the retry on the other replica resumes from the last step |
| Knowledge index | the `index` volume | one per replica, from the same snapshot: read-only, 148 chunks, and Chroma's SQLite is not meant to be shared |

## Fault tolerance in the graph

| Failure | What happens |
| --- | --- |
| A model call fails or times out (`LLM_CALL_TIMEOUT_S`) | LangGraph retries the node (`NODE_ATTEMPTS`, `NODE_TIMEOUT_S`); the OpenAI client does not retry |
| It keeps failing in the router, knowledge or support | the `error_handler` escalates as `model_unavailable`, through `collect`, so answered steps still go out |
| It keeps failing in compose, a draft, a rephrasing or a summary | that node degrades in place |
| Any other exception | a bug: the run fails |

## CI and supply chain

| Job | What it runs |
| --- | --- |
| Test (ubuntu, macOS) | `uv sync --frozen`, then `make verify-offline`, under `unshare --net` on ubuntu and with sockets blocked on both; on ubuntu also the replica tests against a Postgres service (a skip fails) and gitleaks (reviewed false positives in `.gitleaksignore`) |
| Container | build, SPDX SBOM (syft), Trivy failing on any critical finding not in `.trivyignore` (each with a reason and an expiry), keyless smoke tests of `/health`, `/metrics` and `/chat`, then the `scale` profile: one conversation across both replicas, and one replica killed |

Third-party actions are pinned by commit SHA; Dependabot proposes weekly updates for `uv.lock`, the
actions and the base-image digests.

## Observability

`make observe` adds Prometheus (`:9090`, with the alert rules), Grafana (`:3000`, the provisioned
dashboard) and Jaeger (`:16686`).

- **Logs.** `structlog` JSON, masked by a processor on every event, so no node can log a CPF even by
  accident; ids the system issues (trace ids, protocols, transaction ids) are never masked away.
  Every node is wrapped by `instrument_node`, so timing and outcome fields are uniform. The merchant
  id is pseudonymised. A request's `trace_id` is the trace id of its OpenTelemetry trace.
- **Traces** in the OTel GenAI conventions, still in Development status: per request,
  `invoke_agent <node>`, `chat <model>` (provider, model, input, cached and output tokens) and
  `execute_tool <tool>` spans, with no content, sent to `OTEL_EXPORTER_OTLP_ENDPOINT` when set.
  Optional LangSmith tracing (masked, off by default) is separate.
- **Metrics.** `GET /metrics`, all counters and histograms in Prometheus format:

  | Metric | What it answers |
  | --- | --- |
  | `support_turns_total{intent,outcome}` | Resolution without a human, by intent: `resolved`, `escalated`, `awaiting_confirmation`, `resolved_by_human` |
  | `support_model_cost_usd_total` | Model spend, from each call's tokens and `LLM_PRICE_*` |
  | cost per resolution | spend over `resolved` turns, so escalated turns' spend is carried by the resolved ones (a dashboard panel and an alert) |
  | `support_model_tokens_total{kind}` | input, cached and output tokens; cached over input is the prompt cache hit rate |
  | `support_escalations_total{reason}` | why turns leave the automated path |
  | `support_node_latency_seconds{node}` | p50 and p95 per node |
  | `support_vendor_failures_total{vendor,kind}` | model or web vendor calls that failed after their retries, by error or timeout |

- **Prompt caching.** Static prompts end at an explicit cache breakpoint with a per-prompt key;
  the effect is not measured yet ([evals/report.md](../evals/report.md)).
- **Alerts** (`deploy/prometheus/alerts.yml`):

  | Alert | Fires when | Runbook, first line |
  | --- | --- | --- |
  | `NodeLatencyP95High` | a node's p95 is above 8 s for 5 min | open its slowest traces: a slow `chat` span is the vendor, a slow `execute_tool` the tool's backend |
  | `EscalationSpike` | over 40% of turns go to a person for 2 min | split escalations by reason: a vendor, the account store, or questions the corpus lacks |
  | `VendorFailures` | over 3 vendor failures in 5 min | check the vendor's status and the failing spans; it clears when the vendor answers |
  | `CostPerResolutionHigh` | over USD 0.01 per resolved turn for 15 min | compare cached with input tokens and the escalation rate |

- **Vendor failures**, each stubbed in `tests/test_resilience.py`; the model rows were also drilled
  against the running stack, which is how a hung draft call was found taking the whole handoff
  down (now cut by `LLM_CALL_TIMEOUT_S`).

  | Failure | The merchant gets | Counted as | Drill, 2026-10-21 |
  | --- | --- | --- | --- |
  | model answers 500 | `model_unavailable` escalation after one retry, never a 5xx | `vendor="model", kind="error"` | 6/6 escalated in 0.6–1.4 s; `VendorFailures` fired at 76 s; answering 7 s after recovery |
  | model hangs | each call cut at `LLM_CALL_TIMEOUT_S`, then the same | `vendor="model", kind="timeout"` | 6/6 escalated in 6.6–7.3 s; alert at 55 s; answering 7 s after recovery |
  | web search down | the pt-BR "cannot look this up now", no escalation | `vendor="web"` | not drilled |
- **Guardrail outcomes** are logged as `guardrail.blocked`, with the outcome (`prompt_injection`
  or `identity_swap`) and the masked message.
