# Tasks

One task, one commit. Message format: `<type>(<T-id>): <imperative, lowercase>`. A T-id is never
reused and two tasks are never grouped in one commit.

T-01 to T-06 are the core slice: a running graph with a real router and honest stubs. T-07 to
T-14 replace the stubs with retrieval, tools and packaging.

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

### T-03 — `feat(T-03): add api contracts and graph state`
`app/contracts.py` with the typed enums, `ChatRequest`, `ChatResponse`, `Source`, `Escalation`;
`app/graph/state.py` with `GraphState` including `hops`.
**Done when:** models validate the spec §3 payloads and no model-facing schema carries an
identity field.

### T-04 — `feat(T-04): add router node with structured output`
`app/prompts/router.md` (pt-BR, versioned) and the prompt loader; `app/graph/router.py` with
`RouterDecision` (`intent`, `target_agent`, `confidence`, `rationale`, `lang`, `query_pt`) and
the node. Provider failure returns `confidence = 0.0` rather than raising or branching.
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
Two read-only tools over a fake account repository, `user_id` from injected state.
**Done when:** a message naming another id does not change the query, and an unknown id is
answered rather than raised.

### T-11 — `feat(T-11): add the support agent and its escalation triggers`
Support agent replaces its stub; hand-back to the router while `hops < 2`.
**Done when:** individual-case questions escalate even when account data exists.

### T-12 — `feat(T-12): add the input guardrail and pii masking`
**Done when:** out-of-domain is answered without routing, PII is masked before logging, and an
identity-swap attempt is refused and logged.

### T-13 — `test(T-13): add the routing golden set and its runner`
`evals/routing.py` and the labelled set.
**Done when:** the runner reports per-intent accuracy and a confusion matrix, and never runs
inside `pytest`.

### T-14 — `build(T-14): package the service and document it`
Dockerfile, compose with the index volume, README.
**Done when:** `docker compose up` serves, a missing index fails fast with the command to build
it, and the README covers build, configuration, run, tests and architecture.
