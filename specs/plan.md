# Plan — how the system is built (v2)

## 1. Module layout

```
app/
  settings.py          pydantic-settings: models, thresholds, paths, keys, operator token
  contracts.py         API and cross-node Pydantic models, typed enums
  main.py              FastAPI: POST /chat, GET /health, GET /metrics; lifespan wiring
  operator.py          GET /handoffs, POST /handoffs/{protocol}/resume, bearer check
  checkpoints.py       the conversation store: SQLite saver with a restricted deserializer
  handoffs.py          the handoff queue: one SQLite table, plain functions
  graph/
    state.py           GraphState — conversation fields and per-turn fields
    router.py          RouterDecision (with plan steps) + router node
    build.py           StateGraph assembly, conditional edges, checkpointer attached here
    memory.py          thread key, turn input, remember node, history window, summary fold
    compose.py         join point after the last step
    human_review.py    prepare_handoff and await_operator nodes
    numbers.py         hard-rule-4 guard: number extraction and normalization
    knowledge.py, support.py, guardrail.py, escalation.py
  rag/                 extraction, snapshot, chunking, index build, hybrid retriever
  tools/               account.py (two support tools), web.py (web search)
  providers/           llm.py (port + OpenAI), fake.py (CI backend), embeddings.py
  prompts/             versioned pt-BR markdown, loaded by id
  observability/       structlog setup, PII masking, metrics, tracing
data/corpus/documents.jsonl   committed extraction snapshot
evals/                 routing golden set + runner, LLM-as-judge runner, committed report
.github/workflows/ci.yml, Makefile, .env.example
tests/                 offline suite, mocked provider
```

Dependencies added in v2: `langgraph-checkpoint-sqlite` and `aiosqlite` (T-19; `aiosqlite` is
used directly by the store and later by the queue), `prometheus-client` and `langsmith` (T-26,
imported directly). Web search calls the
provider's REST API over `httpx`, which is already a dependency. No new framework.

## 2. Graph

```
START -> guardrail --blocked--> END                      (not remembered)
             |
             v
           router --confidence < threshold--> escalate
             |
             v  (one step at a time, in plan order)
     knowledge | support --refusal--> escalate
             |          --hand-back, hops < 2--> router
             |          --hand-back, budget spent--> escalate
             |          --more steps--> next specialist
             v
          compose -> remember -> END

escalate -> prepare_handoff -> await_operator (interrupt) -> remember -> END
```

`GraphState` has two kinds of field.

- **Conversation fields** persist in the thread: `messages` (LangChain messages with the
  `add_messages` reducer) and `summary`.
- **Per-turn fields** are reset by the turn input: `message`, `masked_message`, `user_id`,
  `trace_id`, `guardrail_outcome`, `route`, `hops`, `step_index`, `step_results`,
  `handoff_reason`, `refusal_reason`, `answer`, `sources`, `escalation`, `case`, `evidence`.

The reset matters. With a checkpointer, an invocation merges its input into the stored state, so
a field left out of the turn input would carry the previous turn's answer or hop count into this
one. `memory.turn_input()` is the single place that builds the input, and it sets every per-turn
field explicitly.

`step_results` needs no reducer: steps run sequentially, so a specialist returns the existing
list with its result appended, and the router resets it on every pass.

The confidence threshold lives in the conditional edge function, so routing policy is readable
in one place in `build.py` instead of hiding inside a node.

## 3. LLM provider

`app/providers/llm.py` defines the `LLMProvider` protocol with one method, `structured()`, that
takes a system prompt, a user message and a Pydantic schema, and returns an instance of it. The
OpenAI implementation wraps `ChatOpenAI.with_structured_output`. No other module imports the
vendor SDK.

`app/providers/fake.py` is the second implementation, selected by `LLM_BACKEND=fake` and used
only by the CI smoke test. It derives a valid instance of any requested schema deterministically
(declared defaults, then the first enum member, the upper bound of a constrained number, a fixed
marker string), so schemas added by later tasks need no change to it. The smoke test asserts the
response contract, never content.

Structured output is the only calling convention. Nothing in the system parses model prose into a
decision.

## 4. Memory

- **Checkpointer.** `AsyncSqliteSaver` opened in the API lifespan at `STATE_DIR/checkpoints.sqlite`
  and passed to `build_graph`, which passes it to `compile()`. Tests pass `InMemorySaver`, or a
  SQLite file under `tmp_path` for the restart case.
- **Restricted decoding.** LangGraph's checkpoint decoder imports any type a checkpoint names
  unless told otherwise. `app/checkpoints.py` gives the saver a serializer that allows only
  LangGraph's safe set plus the application types the state holds, so a tampered checkpoint is
  read back as plain data. A test keeps the allowlist equal to the types reachable from
  `GraphState`.
- **Thread key.** `memory.thread_id(user_id, conversation_id)` returns the SHA-256 hex digest of
  the pair encoded as a JSON array. The encoding keeps `("ab", "c")` and `("a", "bc")` from
  colliding; the hash keeps the raw identifier out of the checkpoint keys.
- **Remember.** The `remember` node appends the masked merchant turn and the reply to `messages`.
  It runs at the end of every answered or resumed turn, never after a guardrail block.
- **Window and summary.** The router renders `summary` plus the last `HISTORY_WINDOW` messages
  as context. When `messages` exceeds `HISTORY_MAX_MESSAGES`, `remember` folds everything older
  than the window into `summary` with one structured call (`ConversationSummary`) and removes
  those messages with `RemoveMessage`. On failure it removes nothing and logs the event.
- **Standalone query.** The router prompt receives the window and must return each step's
  `query_pt` as a self-contained Portuguese question. The original message and every `query_pt`
  are kept in state and logged masked, so a bad rewrite can be read in the logs.

## 5. Plans and compose

`RouterDecision` gains `steps: list[PlanStep]` with `min_length=1, max_length=2`; each `PlanStep`
is `{agent, query_pt}`. Decision-level `intent`, `confidence`, `rationale` and `lang` stay.

After each specialist, `after_step` decides: refusal escalates; a hand-back returns to the router
while the hop budget lasts; otherwise the next step runs, or `compose` when none is left.

`compose` is always the join point. With one step result it copies the answer and sources and
makes no model call, which is how single-intent turns keep v1's cost. With two it makes one
structured call (`ComposedAnswer`), takes the union of both steps' sources deterministically, and
applies the hard-rule-4 guard: if the composed text carries a number absent from the step
answers, it falls back to the step answers joined through a template.

The guard lives in `app/graph/numbers.py`, introduced by T-21 and reused by T-22. It extracts
numeric tokens from both texts and compares them as `Decimal` values, never as strings: currency
symbols, percent signs and spaces are dropped, and each token is read under the pt-BR convention
(`.` groups thousands, `,` marks decimals) and the en convention (the reverse). "R$ 1.234,56" and
"1234.56" therefore both yield 1234.56. A token with a single ambiguous separator ("1.234") yields
both readings, and a generated number passes when any of its readings matches any reading of a
source number. That leniency only accepts a value the source already contains under some
reading; it never lets an absent value through.

## 6. RAG pipeline

Extraction happens once and is committed; chunking, embedding and persistence happen at index
build; retrieval and generation happen at request time.

- **Extract.** Per-route parsers over `data/raw/` (FAQ accordion, Beaver Builder articles), as in
  v1. The output, a list of `Document`, is written to `data/corpus/documents.jsonl` in a stable
  order, which is the committed snapshot. `data/raw/` stays gitignored.
- **Source of documents.** Ingest re-extracts and refreshes the snapshot when `data/raw/` and the
  manifest are present; otherwise it reads the snapshot. Either way the rest of the pipeline is
  identical, and chunk ids are content hashes, so re-running upserts the same rows.
- **Chunk.** Section-aware, keeping FAQ pairs intact; each chunk keeps url and title.
- **Index.** Chroma persisted to disk plus `chunks.jsonl` for BM25. `--if-missing` skips the
  build when the collection already exists. Refreshing the test fixture is a host-side option,
  never part of the container build.
- **Retrieve.** Dense and lexical top-k fused with RRF, then the threshold check.
- **Generate.** The knowledge prompt receives only the surviving chunks and the hard rules.

## 7. Web search

`app/tools/web.py` exposes `search_web(query, settings, client) -> list[WebResult]`, where
`WebResult` is `{url, title, snippet}`. It masks the query with `mask_pii` before building the
request, posts to the provider's REST endpoint over `httpx` with a timeout, and returns an empty
list with a logged reason on a missing key, a timeout or a non-2xx response. Tests inject an
`httpx.MockTransport`, so no request leaves the suite.

The knowledge agent chooses its evidence by the decision's intent, not by fallback: `general_info`
goes to the web, everything else to the corpus. The web prompt (`knowledge_web.md`) receives the
snippets and today's date, must cite the urls it used, and is bound by hard rule 1. With no
results the agent answers with the `web_unavailable` template and no sources.

## 8. Human review

The fourth agent is two nodes because `interrupt()` re-executes its node from the start on
resume. Anything with a cost or a random value must happen in an earlier node, or it would run
twice.

- `escalate` (unchanged role, deterministic) mints the protocol and the typed reason. The protocol
  is minted before the pause, so it survives the re-execution.
- `prepare_handoff` builds the `HandoffCase`: masked summary and attempts from state, plus a
  proposed draft from one structured call (`HandoffDraft`). A failed call leaves the draft empty.
- `await_operator` calls `interrupt(case)` and nothing else before it. On resume it receives the
  operator's answer, makes one structured call (`OperatorReply`) to rephrase it, applies the
  hard-rule-4 guard, and returns the reply with one `operator` source.

The nodes never touch the queue. The API does:

- `/chat` invokes the graph. When the result carries `__interrupt__`, it records the case in the
  queue with its thread id and returns `pending_human`.
- Before invoking, `/chat` reads the thread state. A thread whose next node is `await_operator`
  has an open case, so it returns `pending_human` for that protocol without running the graph.
- `/handoffs/{protocol}/resume` looks the protocol up in the queue (404 unknown, 409 resolved),
  invokes the graph with `Command(resume=answer)` on that thread, marks the case resolved, and
  returns the `ChatResponse`.

The queue is a table, not a scan of checkpoints: listing open cases must not read every thread.
It lives at `STATE_DIR/handoffs.sqlite`, on the same volume as the checkpoints, and is accessed
through plain functions over `aiosqlite`.

The bearer check compares with `secrets.compare_digest`. An unset `OPERATOR_TOKEN` disables the
operator routes with a 503 rather than leaving them open.

## 9. Prompts

Markdown files under `app/prompts/`, each with a `version` in front matter, loaded by id. No
prompt literal appears in Python. v2 bumps `router` and adds `summary`, `handoff_draft`,
`operator_reply`, `operator_verbatim`, `pending_human`, `compose`, `compose_fallback`,
`knowledge_web`, `web_unavailable` and `judge`. `out_of_domain` is removed with the outcome that
used it.

## 10. Configuration and startup

`pydantic-settings` reads `.env`; `.env.example` documents every setting.

| Group | Settings |
| --- | --- |
| Model | `LLM_BACKEND` (`openai` or `fake`), `OPENAI_API_KEY`, `LLM_MODEL`, `LLM_TEMPERATURE`, `JUDGE_MODEL` |
| Retrieval | `EMBEDDING_BACKEND`, `EMBEDDING_MODEL`, `INDEX_PATH`, `COLLECTION_NAME`, `RETRIEVAL_TOP_K`, `RETRIEVAL_CONFIDENCE_THRESHOLD` |
| Routing | `ROUTER_CONFIDENCE_THRESHOLD`, `MAX_HOPS` |
| Memory | `STATE_DIR`, `HISTORY_WINDOW`, `HISTORY_MAX_MESSAGES` |
| Web | `TAVILY_API_KEY`, `WEB_SEARCH_MAX_RESULTS`, `WEB_SEARCH_TIMEOUT_S` |
| Operator | `OPERATOR_TOKEN` |
| Tracing | `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` |

The API builds the provider, the retriever, the checkpointer and the queue once in the lifespan
and fails fast with an explicit message when a required setting is missing. The API never builds
the index; the container command does, before starting the API.

## 11. Testing

Three levels, deliberately separate.

- `uv run pytest -q` — offline, provider scripted, web transport mocked, no cost, always runs.
  Each feature task ships the node-level tests that prove its done criterion; T-24 adds the
  HTTP-level flows through the FastAPI app (multi-turn, restart, isolation, human review,
  compound, web). Knowledge tests use `tests/fixtures/kb_sample.jsonl`, never the full corpus.
  Tests assert invariants — route, plan, sources, status, masking, isolation — never exact model
  text.
- `uv run python -m evals.run_routing_eval` — the routing golden set against a real model, now
  with optional prior turns per row and expected plans.
- `uv run python -m evals.run_judge` — the ten challenge scenarios through the real graph, scored
  by a judge model for groundedness and answer relevance, written to `evals/report.md`.

Neither eval runs inside pytest: a statistical measure that dips one case in thirty is not a red
test suite.

## 12. Docker and CI

Multi-stage build with uv, no API key at build time. The image includes the committed corpus
snapshot, and the local embedding model is fetched during the build so the first boot needs no
download besides the LLM calls themselves. The container command runs
`python -m app.rag.ingest --if-missing`, then starts uvicorn. Compose declares two named volumes,
`index` and `state`, so the index can be dropped and rebuilt without touching a conversation; it
loads `.env` and has a healthcheck whose start period covers the first index build.

`.github/workflows/ci.yml`, on push and pull request: `uv sync`, ruff, pytest, `docker build`,
`docker run` with `LLM_BACKEND=fake`, wait for `/health`, post one `/chat` and validate the
response shape. The README carries the workflow badge.

## 13. Observability

- **Logs.** structlog JSON with the masking processor, as in v1. The router logs the masked
  original message and each masked `query_pt`.
- **Metrics.** `app/observability/metrics.py` defines two families with `prometheus-client`:
  `support_escalations_total{reason}` and `support_node_latency_seconds{node}` (histogram). Both
  are recorded in `instrument_node`, which already wraps every node, so no agent can forget them.
- **Tracing.** When `LANGSMITH_TRACING` is on, the graph runs with a LangChain tracer whose
  LangSmith client applies `mask_pii` to inputs and outputs before upload. Off by default.
- **Evidence.** Each step records the text it answered from (chunks, tool result or snippets) in
  the per-turn `evidence` field. It never reaches the response or the history; the judge grades
  against it.

## 14. Deliberately not abstracted

No repository interface over the account fixture or the handoff queue, no agent base class, no
message bus, no web-search port. The one port that exists, `LLMProvider`, has two implementations
for a reason: the vendor is expected to change, and CI needs a deterministic backend.

## 15. Delivery

v2 is built on a feature branch, one commit per task, and merges to `main` through a single pull
request at the end, after T-27. CI must be green on the pull request and again on `main` after
the merge; the README badge tracks `main`. v1 stays reachable at the tag `v1`.
