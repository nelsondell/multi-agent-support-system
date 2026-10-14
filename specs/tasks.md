# Tasks

One task, one commit. Message format: `<type>(<T-id>): <imperative, lowercase>`. A T-id is never
reused and two tasks are never grouped in one commit.

T-01 to T-06 are the core slice: a running graph with a real router and honest stubs. T-07 to
T-14 replace the stubs with retrieval, tools and packaging. Together they are v1, frozen at the
tag `v1`. T-15 to T-41 are v2, an iteration on the v1 evaluation outside the 60-minute timebox:
they close its gaps in an order that keeps the suite green after every commit. T-28 to T-36 are
fix passes from real runs and land before T-24; T-37 to T-41 follow the judge's first runs.

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
`RouterDecision.steps` (one or two `PlanStep`), `collect` after each step, and `compose` as the
join point. The response's `target_agent` becomes `agents`; support answers cite the account. The
hop budget is unchanged and spans the plan.
**Done when:** a compound message runs support then knowledge and the response lists both agents
and every source of both; a single-step turn makes the same model calls as before; when one step
refuses, the other step's answer goes out now and the rest goes to `human_review` under one
protocol; the hop budget spans the plan; the schema rejects a third step; a composed answer that
adds a number falls back to the joined step answers, while one that only respells a step's value
("R$ 396,06" as "396.06") is kept.

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
key, and the masked web query. The golden set gains multi-turn, human-review and compound rows,
and A03 is relabelled as the compound request it is; the runner accepts prior turns and expected
plans. `specs/plan.md` §11 maps every v2 invariant to the test that pins it; a failing account
tool, the one invariant found unpinned and unmet, escalates with `tool_unavailable`.
**Done when:** those flows pass offline and the suite still runs in seconds; the runner reports
accuracy for the new groups, and the extended set has run against the real router; assertions
check invariants, never exact model text.

### T-25 — `feat(T-25): add the llm judge eval and commit its report`
Per-turn `evidence` in state. `evals/run_judge.py` runs the ten challenge scenarios through the
real graph and scores groundedness and answer relevance with `JUDGE_MODEL` against that evidence.
The run also covers two follow-ups, a compound request and the human-review loop (pause, the
operator's reply, the next turn), each answered turn judged against its own evidence, and the
report carries the retrieval calibration numbers. The mock records gain the refunded duplicate of
a sale, so that line's refund has data behind it.
**Done when:** a real run writes `evals/report.md` (per-scenario scores, means, models, commit)
and the report is committed; `evidence` never reaches the response or the history; the judge
never runs inside pytest. S09 (crediário) answers with sources using the real router; if retrieval confidence is still below the threshold, the threshold is re-calibrated from measured in-domain and out-of-domain scores and the numbers go in the README.

### T-26 — `feat(T-26): add escalation and latency counters and optional tracing`
`/metrics` with `support_escalations_total{reason}` and `support_node_latency_seconds{node}`,
both recorded in `instrument_node`. Optional LangSmith tracing, off by default, with masked
inputs and outputs: LangSmith's global switch is set once at start-up, on through a client that
masks, or off, which also overrides a `LANGSMITH_TRACING=true` LangChain would otherwise act on,
unmasked. CI smoke-tests `/metrics`.
**Done when:** an offline test shows an escalation incrementing its counter and `/metrics`
exposing both families; an offline test shows the tracing hook masks a CPF before upload; with
tracing off no tracer is attached, even when the environment asks for one; a tracer that fails
never fails a turn.

### T-27 — `docs(T-27): rewrite the readme for v2 with real outputs`
`scripts/demo.sh` covers the ten scenarios, a multi-turn exchange, the human-review loop and a
compound request. Its output, from the running container, is pasted into the README.
**Done when:** the README has a mermaid architecture diagram, the message workflow, the RAG
pipeline (ingestion, storage, retrieval, generation), memory, the human-review curl flow, real
outputs for all ten scenarios, a "What changed since v1" table mapped to the evaluation, and a
"Design notes" section answering the five interview topics; no placeholder is left.

## Fix pass from the first real run

The first run against the real model and web search, in Docker (the ten scenarios, a follow-up, a
compound request and the human-review loop), found the gaps below. T-28 to T-31 close them before
T-24, so the end-to-end tests, the judge and the README describe the fixed behaviour.

### T-28 — `feat(T-28): add a reasoning effort setting and default to gpt-6-luna`
`LLM_TEMPERATURE` becomes optional and `LLM_REASONING_EFFORT` is added; the provider sends each
only when set, because reasoning models reject a temperature other than their default and models
without reasoning reject an effort. The effort defaults to `low` unless a temperature is set.
`evals/run_model_benchmark.py` routes the golden set through each candidate model and writes
`evals/router_benchmark.md`, which the README cites.
**Done when:** the default is `gpt-6-luna` at low effort with no temperature sent; `gpt-4o-mini`
with `LLM_TEMPERATURE=0` sends a temperature and no effort; setting both fails at start-up; an
empty value in `.env` counts as unset; the benchmark table is committed.

### T-29 — `fix(T-29): gate knowledge answers on sufficiency and calibrate the threshold`
`PlanStep.search_queries` carries one search per compared item; each query's lists are fused with
RRF and the per-query rankings interleaved. `KnowledgeAnswer.sufficient` makes "the excerpts do
not answer" a refusal (`insufficient_sources`), so it reaches a human with a protocol instead of
the merchant as an answer; web results flagged the same way degrade. An unreachable embedder
abstains. `evals/run_retrieval_calibration.py` measures in-domain against out-of-domain confidence
for the local model, `text-embedding-3-small` and `text-embedding-3-large`, and writes
`evals/retrieval_calibration.md`; the embedder and threshold defaults follow from it. The knowledge
prompt drops the "Fonte:" lines. `PlanStep.time_sensitive` web queries carry today's date.
**Done when:** a comparison retrieves each item's page where the question alone does not; an
insufficient answer escalates with a protocol and its text never reaches the merchant; an
unreachable embedder abstains instead of raising; the calibration report is committed and the
README states whether the chosen embedder separates the groups; a time-sensitive web query ends
with today's date; CI still runs keyless on the local embedder.

### T-30 — `fix(T-30): date account records to the turn, format amounts and drop unkept promises`
The support tools take the turn's `today` through `InjectedState` and read the mock records
shifted from the day they describe (`RECORDED_ON`) to it, ids included; every amount leaves the
tool as `brl()` writes it. `/chat` reads the date through `merchant_today()`, which the suite pins.
`compose` drops any answered sentence that mentions an agent, a team or a human; an answer made
only of that escalates with `insufficient_sources`. The prompts stop offering a human.
**Done when:** with today 2026-10-14, yesterday, today and tomorrow each return a settlement
entry; the model receives "R$ 1.240,00", never `1240.0`; an answered turn never mentions a person
and a turn that only promised one opens a protocol; the suite never reads the wall clock.

### T-31 — `fix(T-31): keep system ids out of masking and isolate the suite from its environment`
The phone pattern stops matching digits glued to letters, `mask_pii` skips protocols and
transaction ids, and the log processor leaves `trace_id` and `protocol` alone. The flaky-test hunt
runs the suite 30 times in order and 10 times shuffled; an autouse fixture keeps it off the
developer's `.env` and exported settings, which made 49 tests fail under non-default values.
**Done when:** no generated trace id or protocol is altered by masking, while CPF, CNPJ, card and
phone still are; 30 ordered and 10 shuffled runs pass; the suite passes with hostile values for
every setting exported in the shell.

## Second fix pass

The second real run found four more gaps, closed by T-32 to T-34, still before T-24.

### T-32 — `fix(T-32): filter settlements by sale or credit date and derive their status`
`get_settlement_schedule` takes `date_field` (`sale_date` or `credit_date`); the mock records
carry both dates and no stored status, which the tool derives from the credit date against today.
The planner filters "the money from yesterday's sales" by sale date and "what lands tomorrow" by
credit date; a sale date after today is read as a credit date. The answer step receives today's
date.
**Done when:** with today 2026-10-14, yesterday's sales return R$ 231,26 crediting 2026-10-14 with
status `previsto`; money credited before today is `liquidado` and nothing else; tomorrow's
"sales" return what credits tomorrow; the answer prompt never calls a settled entry a forecast.

### T-33 — `fix(T-33): answer partial coverage, date web queries by target day and keep boots whole`
`KnowledgeAnswer.coverage` (`full`, `partial`, `none`) replaces the yes/no flag: a partial answer
gives the supported part and says plainly what the site does not cover; `none` abstains and
escalates, and degrades on the web. `PlanStep.target_day` replaces `time_sensitive`, and the web
query carries that day's date. Pages that share a `<title>` take their `<h1>`, and the snapshot,
the fixture and the index are rebuilt. Ingestion checks the embedder before reading anything and
keeps both halves of the index or neither.
**Done when:** S04 is answered in part again; a "tomorrow" forecast query carries tomorrow's date;
the two product pages cite with distinct titles; a keyless first boot stops with a one-line fix
and writes nothing, and a rejected key leaves no collection and no BM25 corpus.

### T-34 — `fix(T-34): resolve follow-up references to the last reply and measure router effort`
Router prompt v7 resolves demonstratives ("dessa venda", "esse valor") to the entity of the last
assistant or operator message, with an example. The golden set gains eight `multi_turn` rows,
the post-operator case among them, with `history` and `expected_mentions`; both runners score a
follow-up as a hit only when the rewrite keeps its referent. The benchmark compares router effort
low and medium on those rows.
**Done when:** the follow-up rows are committed and the benchmark report shows low against medium
on them; a per-node effort exists only if medium wins by at least 10 points; the README states
the result and the prompt-only comparison.

### T-36 — `chore(T-36): ignore every env file but the example`
`.gitignore` ignores `.env.*` and re-includes `.env.example`, so a backup such as `.env.bak` with
real keys can never be staged.
**Done when:** `git status` does not list `.env.bak`, and `.env.example` is still tracked.

### T-35 — `fix(T-35): answer declines from the issuer's recorded reason and route them to support`
The router prompt sends a decline, even one phrased as a card-machine error, to `support` as an
individual case, with S08 as its example. The planner reads declined sales (`get_transactions`,
`status = "negada"`), whose records carry the issuer's `decline_reason`; the answer relays it and
nothing more. A decline the data cannot explain, with no declined sale or no recorded reason,
escalates. The answer step sees the query as it was run and answers only what was asked. Hard
rule 3 changes accordingly.
**Done when:** S08, run 10 times against the real router, goes to support at least 9 times and is
answered with the recorded reason; a merchant with no declined sale, or one without a reason,
gets a protocol; "E o de amanhã?" is answered without commenting on what was not asked.

## Third fix pass

The judge's first runs found three weak spots, closed by T-37 and T-38; T-39 replaces the single
judged run with three.

### T-37 — `fix(T-37): keep S01 above the threshold and state conditions in knowledge answers`
Retrieval confidence is the best dense similarity any query reached, the whole question being
always one of them. The router writes each per-item search with the product type ("maquininha
Get Clássica"), whose absence dropped S01 below the threshold. The knowledge prompt states the
condition behind a conditional price, and allows "the site doesn't say X" only when no excerpt
treats X.
**Done when:** a kept hit weaker than another query's best match does not lower the confidence;
the question is always searched; S01, run 5 times for real, answers every time, with both
product pages and the tiered prices stated with their conditions.

### T-38 — `fix(T-38): date time-sensitive web answers and say when the data is old`
A step with a `target_day` searches with Tavily's `time_range = day` first and without it when
that finds nothing. Results carry the provider's publication date. The web prompt cites the date
the data is from and reports it in `KnowledgeAnswer.data_date`; when it is not the day asked
about, or the pages do not show it, a fixed pt-BR notice is appended, printing a date only if the
pages show it.
**Done when:** a question about a day asks for recent pages first and falls back; data from
another day ends with the notice naming both days; a date the pages do not show is never
printed; a question that does not depend on the day gets neither the filter nor the notice; the
weather and euro questions, asked for real, answer with the date of their data.

### T-39 — `docs(T-39): report the judge over three runs with means, minimums and spread`
`evals/run_judge.py` runs every line three times (`--runs`) and `evals/report.md` reports, per line
and turn, how often it was answered or escalated and the mean and minimum of each score, the means
of each run with their standard deviation, S09's retrieval confidence in every run, and every score
below 4 with the answer the judge read. No run is chosen.
**Done when:** the report aggregates three real runs and states the run-to-run spread.

### T-40 — `fix(T-40): stamp web answers with the source's structured publication date only`
The model no longer reports the date of its data (`data_date` is gone) and is told not to write
publication dates or times. A time-sensitive web answer closes with a stamp built by code from
the cited results' structured `published_date`, as a day in the merchant's calendar: the date when
it is recent enough, a notice when it is older than the day in question, another when no result
has one. The judge runs three more times and `evals/report.md` is refreshed in the same commit.
**Done when:** the stamp comes from the structured date only, converted to Brazil's calendar, with
no time of day; the model never sees that date; an undated source says so; the report reflects
three runs on this code.

### T-41 — `docs(T-41): state that v2 iterates on the evaluator's feedback outside the timebox`
Next to the 60-minute line, the README says plainly that v2 is an iteration on the evaluator's
feedback on v1, done at the evaluator's invitation and outside the timebox, and links the "What
changed since v1" table. The demo section is refreshed from the image built after T-40.
**Done when:** a reader cannot take v2 for part of the timed exercise; the demo output matches the
current image.

### Traceability

| Criterion (v1 score) | Tasks |
| --- | --- |
| Memory / multi-turn (1.5) | T-19, T-20, T-24, T-34 |
| Human-in-the-loop (5.8) | T-21, T-24 |
| Infra / Docker (6.8) | T-16, T-17, T-18, T-33 |
| Product / completeness (7.5) | T-23, T-27, T-30, T-32 |
| Multi-agent / orchestration (8.8) | T-22 |
| Observability / evals (9.1) | T-25, T-26, T-31 |
| Tests / quality (9.2) | T-24, T-31, plus the tests inside every task |
| Architecture / code (9.5) | every task: no new framework, no function over 40 lines |
| Security / isolation (9.5) | T-19, T-21, T-23 |
| RAG / knowledge (9.8) | T-16, T-23, T-29, T-33 |
| Trade-offs / engineering (10.0) | T-27, T-28 |

Outside the task list: the pull request that merges v2 into `main` after T-27 (plan §15), and,
by hand, pushing the `v1` tag, the walkthrough video (at most 15 minutes) and the submission
message.
