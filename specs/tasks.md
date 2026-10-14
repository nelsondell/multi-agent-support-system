# Tasks

One task, one commit. Message format: `<type>(<T-id>): <imperative, lowercase>`. A T-id is never
reused and two tasks are never grouped in one commit.

T-01 to T-06 are the core slice: a running graph with a real router and honest stubs. T-07 to
T-14 replace the stubs with retrieval, tools and packaging. Together they are v1, frozen at the
tag `v1`. T-15 to T-27 are v2: they close the gaps in the v1 evaluation, in an order that keeps
the suite green after every commit.

## Core

### T-01 — `chore(T-01): configure tooling and application settings`
Ruff and pytest configuration in `pyproject.toml` (`asyncio_mode = "auto"`), package `__init__`
files, `app/settings.py` with pydantic-settings.
**Done when:** `uv run ruff check .` and `uv run pytest -q` both pass; settings load without an
API key present.

### T-02 — `feat(T-02): add llm provider port and openai implementation`
`app/providers/llm.py`: `LLMProvider` protocol with `structured()`, plus the current
implementation over `ChatOpenAI.with_structured_output` and a builder that reads settings.
**Done when:** no module outside this file imports a vendor SDK, so moving to Azure AI Foundry
touches one file.

### T-03 — `feat(T-03): add api contracts`
`app/contracts.py` with the typed enums, `ChatRequest`, `ChatResponse`, `Source`, `Escalation`.
**Done when:** models validate the spec §3 payloads and no model-facing schema carries an
identity field.

### T-04 — `feat(T-04): add router node with structured output and graph state`
`app/prompts/router.md` (pt-BR, versioned) and the prompt loader; `app/graph/router.py` with
`RouterDecision` (`intent`, `target_agent`, `confidence`, `rationale`, `lang`, `query_pt`) and
the node; `app/graph/state.py` with `GraphState` including `hops`. State and router ship together
because the state carries the decision the router produces. Provider failure returns
`confidence = 0.0` rather than raising or branching.
**Done when:** `tests/test_router.py` covers a knowledge case, a support case and a provider
failure, all with a mocked provider.

### T-05 — `feat(T-05): assemble the graph with a confidence edge`
`app/graph/build.py`: `StateGraph` over `GraphState`, threshold as a conditional edge; knowledge
and support as stubs that escalate instead of inventing an answer; escalation node and
`app/prompts/escalation.md`.
**Done when:** a low-confidence decision reaches escalation without any node-level branch, and
stubs never produce an ungrounded answer.

### T-06 — `feat(T-06): expose the chat and health endpoints`
`app/main.py`: FastAPI with `POST /chat` and `GET /health`, provider built once at startup.
**Done when:** a request returns a valid `ChatResponse` end to end with a mocked provider.

## Retrieval, tools, packaging

### T-07 — `feat(T-07): build the knowledge base index from the raw corpus`
`scripts/ingest.py`: per-route extraction from `data/raw/` to `kb.jsonl`, chunking, Chroma and
BM25 artifacts; `tests/fixtures/kb_sample.jsonl` derived from it.
**Done when:** re-running is idempotent and the index builds from `data/raw/` alone.

### T-08 — `feat(T-08): add hybrid retrieval with rrf and a confidence threshold`
**Done when:** an offline test on the sample fixture asserts fusion order and that a
below-threshold query returns "no confidence" rather than chunks.

### T-09 — `feat(T-09): answer from retrieved sources or abstain`
Knowledge agent replaces its stub.
**Done when:** answers carry sources and the abstain path generates no content.

### T-10 — `feat(T-10): add settlement and account status tools`
Two read-only tools over a fake account repository, `user_id` from injected state. Shipped as
`get_settlement_schedule` and `get_transactions`, committed as
`feat(T-10): add settlement and transaction tools`.
**Done when:** a message naming another id does not change the query, and an unknown id is
answered rather than raised.

### T-11 — `feat(T-11): add the support agent and its escalation triggers`
Support agent replaces its stub; hand-back to the router while `hops < 2`.
**Done when:** individual-case questions escalate even when account data exists.

### T-12 — `feat(T-12): add the input guardrail and pii masking`
**Done when:** out-of-domain is answered without routing, PII is masked before logging, and an
identity-swap attempt is refused and logged.

### T-13 — `test(T-13): add the routing golden set and complete the test suite`
`evals/run_routing_eval.py` and the labelled set; plus one test module per graph node, the two
tools, masking, three adversarial cases and an HTTP integration module.
**Done when:** the runner reports overall and per-class accuracy and never runs inside `pytest`;
every node, both tools and each adversarial path is covered by the offline suite.

### T-14 — `build(T-14): package the service and document it`
Dockerfile, compose with the index volume, README.
**Done when:** `docker compose up` serves, a missing index fails fast with the command to build
it, and the README covers build, configuration, run, tests and architecture.

## v2

Conventions for every v2 task: a task that adds a setting documents it in `.env.example`; a task
that changes a contract updates the tests that pin it in the same commit; node-level tests ship
with the feature, HTTP-level flows land in T-24.

### T-15 — `docs(T-15): add the v2 spec, plan and tasks`
`specs/spec.md`, `specs/plan.md` and this file updated for v2, mapped to the v1 evaluation.
**Done when:** every evaluation criterion maps to at least one task (table at the end), web search
is in scope, every task is one commit with a done criterion, and the list is approved.

### T-16 — `feat(T-16): commit the corpus snapshot and ingest from it`
Extraction writes `data/corpus/documents.jsonl` (extracted text, never raw HTML) in a stable
order. Ingest reads the snapshot when `data/raw/` or the manifest is missing, and re-extracts and
refreshes it when both are present.
**Done when:** with `data/raw/` absent, `uv run python -m app.rag.ingest` builds the index from
the snapshot; with it present, re-running leaves the snapshot byte-identical and the chunk ids
unchanged; an offline test covers both document sources over a temporary directory.

### T-17 — `build(T-17): run the service from a clean clone with one compose command`
The image copies the snapshot and fetches the local embedding model at build time. The container
command runs `python -m app.rag.ingest --if-missing`, then uvicorn. Compose loads `.env`, keeps
the index in a named volume and has a healthcheck covering the first build. `.env.example`
documents every setting. `Makefile` with `up`, `test`, `eval` and `demo`. The README's Docker
section describes the one-command flow and drops the "not verified" note.
**Done when:** on a fresh clone, `cp .env.example .env && docker compose up` serves `/health`; a
second boot skips the index build; the image builds with no API key present; `--if-missing` is
covered by an offline test.

### T-18 — `ci(T-18): add the ci workflow and a fake llm backend`
`LLM_BACKEND=fake` selects `app/providers/fake.py`, used only by CI.
`.github/workflows/ci.yml` runs ruff, pytest, `docker build`, starts the container with the fake
backend and smoke-tests `/health` and one `/chat`. Status badge in the README.
**Done when:** an offline test shows the fake returns a valid instance of every schema the graph
requests; the workflow passes on GitHub; the badge is green.

### T-19 — `feat(T-19): persist conversations with a sqlite checkpointer`
Optional `conversation_id` on the request, returned in every response. `app/graph/memory.py`
with the thread key, `turn_input()`, the `remember` node, the history window and the summary
fold. `messages` and `summary` in state. `AsyncSqliteSaver` opened in the lifespan at
`STATE_DIR`; compose gains a `state` volume, separate from the index.
**Done when:** offline tests show a second turn finds the first in `messages`; a graph rebuilt
over the same SQLite file continues the conversation; the same `conversation_id` under another
`user_id` starts empty; per-turn fields do not leak into the next turn; a guardrail-blocked turn
is not remembered; history holds the masked text; no module under `app/graph/` imports a
checkpointer.

### T-20 — `feat(T-20): rewrite follow-ups into standalone queries`
The router prompt receives the summary and the history window and returns `query_pt` as a
self-contained Portuguese question. The router logs the original message and the rewritten query,
both masked.
**Done when:** for "when will yesterday's sales be deposited?" followed by "and tomorrow's?", the
router input carries the first exchange and the specialist receives the rewritten `query_pt`, not
the raw follow-up; a first turn sends no history block; the router log event carries both
versions, masked.

### T-21 — `feat(T-21): add the human review agent with interrupt and resume`
`prepare_handoff` and `await_operator` after `escalate`; `HandoffCase` with summary, intent,
attempts, retrieved sources and proposed draft, recorded in the `app/handoffs.py` queue before the
pause. `GET /handoffs`, `GET /handoffs/{protocol}` and `POST /handoffs/{protocol}/resume`
(`approve` the draft or `reply`) behind the operator bearer token. `/chat` returns
`status = pending_human` while a case is open. The operator's answer is the only new fact the
reply may carry.
**Done when:** offline tests show an individual-case turn pausing with a complete case; `/chat`
on that conversation returning `pending_human` without a model call; resume producing a reply
with an `operator` source that lands in `messages`; 401 for a missing or wrong token, 503 when
none is configured, 404 for an unknown protocol, 409 for a second resume; a rephrased reply that
adds a number replaced by the operator's literal text, while one that writes "1234.56" for an
operator's "R$ 1.234,56" is kept, because `app/graph/numbers.py` compares normalized values;
one protocol per case although `await_operator` re-executes.

### T-22 — `feat(T-22): plan up to two specialists and compose their answers`
`RouterDecision.steps` (one or two `PlanStep`), `after_step`, and `compose` as the join point.
The response's `target_agent` becomes `agents`. The hop budget is unchanged.
**Done when:** a compound message runs support then knowledge and the response lists both agents
and every source of both; a single-step turn makes no compose model call; a refusal in either
step escalates the turn; the schema rejects a third step; a composed answer that adds a number
falls back to the joined step answers, while one that only respells a step's value ("R$ 1.234,56"
as "1234.56") is kept.

### T-23 — `feat(T-23): answer general questions with web search`
`general_info` intent; `app/tools/web.py`; the knowledge agent picks web or corpus by intent;
`knowledge_web` and `web_unavailable` prompts. The guardrail drops `out_of_domain` and keeps
blocking injection and identity swap. Golden-set rows S02, S07, O01 and O02 relabelled.
**Done when:** weather and exchange-rate questions are answered with `web` sources from a mocked
transport; a CPF in the message never reaches the transport; without a key no request is made
and the agent says it cannot look that up now; account-data turns never call the web; a product
question below the retrieval threshold still abstains without the web; injection and identity
swap are still refused.

### T-24 — `test(T-24): cover memory, human review, plans and web search end to end`
HTTP-level tests through the FastAPI app with a scripted provider: two-turn memory, persistence
across an app restart over the same state directory, thread isolation between users, the full
human-review loop (chat, list, resume, next turn), a compound plan, web search with and without a
key, and the masked web query. The golden set gains multi-turn, human-review and compound rows;
the runner accepts prior turns and expected plans.
**Done when:** those flows pass offline and the suite still runs in seconds; the runner reports
accuracy for the new groups; assertions check invariants, never exact model text.

### T-25 — `feat(T-25): add the llm judge eval and commit its report`
Per-turn `evidence` in state. `evals/run_judge.py` runs the ten challenge scenarios through the
real graph and scores groundedness and answer relevance with `JUDGE_MODEL` against that evidence.
**Done when:** a real run writes `evals/report.md` (per-scenario scores, means, models, commit)
and the report is committed; `evidence` never reaches the response or the history; the judge
never runs inside pytest.

### T-26 — `feat(T-26): add escalation and latency counters and optional tracing`
`/metrics` with `support_escalations_total{reason}` and `support_node_latency_seconds{node}`,
both recorded in `instrument_node`. Optional LangSmith tracing, off by default, with masked
inputs and outputs.
**Done when:** an offline test shows an escalation incrementing its counter and `/metrics`
exposing both families; an offline test shows the tracing hook masks a CPF before upload; with
tracing off no tracer is attached.

### T-27 — `docs(T-27): rewrite the readme for v2 with real outputs`
`scripts/demo.sh` covers the ten scenarios, a multi-turn exchange, the human-review loop and a
compound request. Its output, from the running container, is pasted into the README.
**Done when:** the README has a mermaid architecture diagram, the message workflow, the RAG
pipeline (ingestion, storage, retrieval, generation), memory, the human-review curl flow, real
outputs for all ten scenarios, a "What changed since v1" table mapped to the evaluation, and a
"Design notes" section answering the five interview topics; no placeholder is left.

### Traceability

| Criterion (v1 score) | Tasks |
| --- | --- |
| Memory / multi-turn (1.5) | T-19, T-20, T-24 |
| Human-in-the-loop (5.8) | T-21, T-24 |
| Infra / Docker (6.8) | T-16, T-17, T-18 |
| Product / completeness (7.5) | T-23, T-27 |
| Multi-agent / orchestration (8.8) | T-22 |
| Observability / evals (9.1) | T-25, T-26 |
| Tests / quality (9.2) | T-24, plus the tests inside every task |
| Architecture / code (9.5) | every task: no new framework, no function over 40 lines |
| Security / isolation (9.5) | T-19, T-21, T-23 |
| RAG / knowledge (9.8) | T-16, T-23 |
| Trade-offs / engineering (10.0) | T-27 |

Outside the task list: the pull request that merges v2 into `main` after T-27 (plan §15), and,
by hand, pushing the `v1` tag, the walkthrough video (at most 15 minutes) and the submission
message.
