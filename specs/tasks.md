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
`scripts/ingest.py` (now `app/rag/ingest.py`): per-route extraction from `data/raw/` to
`kb.jsonl` (now the snapshot `data/corpus/documents.jsonl`), chunking, Chroma and BM25 artifacts;
`tests/fixtures/kb_sample.jsonl` derived from it.
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

## After the independent review

An independent review of v2 found the gaps below; T-42 to T-45 close them.

### T-42 — `feat(T-42): deliver operator replies, keep notes and expire cases`
The reply a case ends with is held in the queue and delivered by the merchant's next `/chat`, in
`ChatResponse.delivered`, before that message's answer. `GET /conversations/{id}` returns the
history through the same thread key as `/chat`. Messages sent while a case waits are appended to
it as masked notes. Cases carry `expires_at` (`HANDOFF_TTL_HOURS`) and an operator `cancel`
action; past the deadline, the merchant's next message closes the case as expired and runs. The
queue gains `outcome`, `notes`, `reply` and `delivered_at`, added in place to an older database.
**Done when:** an operator's reply reaches the merchant exactly once, with their next message;
the history is readable by its merchant only; notes reach the operator masked; a cancelled or
expired case closes with a notice and the conversation goes on; an older queue database opens.

### T-43 — `feat(T-43): finish the plan after the operator answers`
A step that needs a human sends the turn to compose at once, so it pauses before the plan's
remaining steps. On resume, `await_operator` writes the operator's answer into that step's result
(with an `operator` source) and the evidence, and `after_operator` routes back into the plan: the
next specialist, or compose. Compose joins only results not yet delivered, so a part sent at the
pause is not repeated. A replan within the hop budget keeps results an operator answered. A turn
that paused before any step ran, or a cancelled case, still ends with its reply directly.
**Done when:** a compound request whose first step needs a human pauses before the second, and
after the reply the second runs and compose joins both, citing the operator and the page; a
hand-back after the reply replans within the budget and keeps the operator's answer.

### T-44 — `fix(T-44): lock conversations, allow the caller's id, rename outcomes, guard statuses`
A per-conversation lock (`app/locks.py`), held by `/chat` from reading the thread to writing the
answer and by the operator's resume, so two requests on one conversation never run at once;
locks are dropped when idle. The guardrail compares an id in the message with the authenticated
`user_id`, so a merchant naming their own id is allowed. Prompt injection gets its own outcome,
`prompt_injection`, and refusal. A failed model call in the router, knowledge or support escalates
as `model_unavailable` (was `agent_not_implemented`, or low confidence), with a notice that says
something failed; stored cases with the old value still load. Hard rule 4 covers status words:
a merge, a draft or an operator rephrasing that states a status its source does not (for example
"aprovada" for "estornada") falls back as a new number does.
**Done when:** two concurrent messages on one conversation run one after the other and both stay
in the history, while two conversations run at once; "user_id: <own id>" passes the guardrail and
another id does not; injection reports `prompt_injection`; a router failure escalates as
`model_unavailable`; a flipped status in a merge or a rephrasing is rejected.

### T-45 — `docs(T-45): rerun the evals with the turn's day for the judge and cut the readme`
The router benchmark reruns on the current router prompt (v9). The README's follow-up claim is
corrected: v6 to v7 went from 28/40 to 40/40 on the 8 follow-up rows of the time, and today's 9
rows score 45/45 at low and at medium effort. The README becomes what a reviewer reads in five
minutes (under 350 lines): what it is, how to run it, the architecture, the flow, the human loop,
the demo table, the evals, the hard rules, what changed since v1, design decisions, limits and
process. The detail moves to `docs/`: `architecture.md` (workflow, layout, tools, memory, failure
handling, design questions), `rag.md`, `human-in-the-loop.md`, `demo.md` (the full output of
`scripts/demo.sh` from the image at T-44) and `operations.md` (configuration, Docker,
observability). The judge reruns three times and `evals/report.md` is refreshed. The rerun, made
after 21:00 in Brazil, showed the judge taking the UTC date for today and scoring correct "ontem"
answers 1, so the judge now receives the merchant's day each turn was answered on (`today`,
judge prompt v2).
**Done when:** `evals/router_benchmark.md` names router prompt v9; no README number disagrees with
the eval it cites; the README is under 350 lines and links every moved section; a judge run after
21:00 in Brazil scores day-relative answers against the merchant's day.

### T-46 — `fix(T-46): say the publication date could not be confirmed, not that the source has none`
A web answer whose sources have no structured publication date closed with "a fonte não informa a
data de publicação", which is false when the page text shows a date the provider did not
structure; the judge marked S02 and S07 down for it in every run. The notice is now "Não consegui
confirmar a data de publicação desta informação." (`web_undated` v3): it says what the system
could not do, never what the source lacks. The judge reruns three times, and `evals/report.md`,
the README numbers and the demo output are refreshed.
**Done when:** an answer without a structured date ends with the new notice and no wording that
the source lacks a date; the report and the README agree on the judge's numbers.

## After the V4 evaluation

The V4 evaluation scored v2 at 9.5. T-47 to T-55 close the gaps it named, one branch and one pull
request each.

### T-47 — `fix(T-47): reproducible offline, in one command`
The evaluator could not start the suite: `app/rag/chunk.py` imports tiktoken, which was only a
transitive dependency and downloads `cl100k_base` on first use. tiktoken is declared and the
encoding is committed under `app/rag/assets/`, loaded by pointing `TIKTOKEN_CACHE_DIR` at it for
that one load. The other runtime download, Chroma's local ONNX model, now comes only from the
image build or `make models`; without it the local embedder stops with that fix. The suite runs
with sockets blocked (`pytest-socket`); the LangSmith test client keeps run updates in-process. `make
verify` runs `uv sync --frozen`, then `scripts/verify.py`: ruff, the format check, the suite and
the guardrail eval (which now exits non-zero on a miss) with no key, written to
`reports/verify.md`. CI runs that offline phase on ubuntu inside `unshare --net` and on macOS,
scans the history with gitleaks, writes a syft SBOM of the image and fails on a critical Trivy
finding outside `.trivyignore`. Base images are pinned by digest, third-party actions by SHA, and
Dependabot covers uv, actions and Docker.
**Done when:** `make verify` passes from a clean clone with no key and no network after the sync,
in under 3 minutes; the verify job is green on ubuntu with the network cut and on macOS; the
secret, SBOM and vulnerability steps are green; the README quick start opens with `make verify`.

### T-48 — `feat(T-48): Postgres for replicas, native fault tolerance`
`STORAGE=sqlite|postgres`. With Postgres, LangGraph's `AsyncPostgresSaver` holds the checkpoints,
the handoff queue runs the same SQL through a Postgres adapter, and the per-conversation lock is a
`pg_advisory_xact_lock` on the thread, on a pool of its own. Replicas that boot together take
turns at creating the tables, with a polled session lock, because LangGraph's migrations build
indexes `CONCURRENTLY`. LangGraph moves to 1.2.16: every node runs under `TimeoutPolicy` and
`RetryPolicy` (`set_node_defaults`), and the router, knowledge and support nodes get an
`error_handler` that escalates a model failure as `model_unavailable`, through `collect` for a
plan step. Their try/except blocks are gone, and the OpenAI client no longer retries on its own.
Checkpoints are written synchronously after every step, and `/chat` finishes a turn left
unfinished by a dead replica before anything else, answering the merchant's retry with it.
`create_app()` builds the API, so a test can run two. `/ready` checks the store and the index. The
compose profile `scale` runs Postgres, two replicas and nginx round robin (`make scale`). Shutdown
goes through uvicorn's graceful timeout and the lifespan; RunControl drain is a LangGraph
Platform feature.
**Done when:** with Postgres, a case escalated on replica A is answered through B and delivered by
A, a replica killed mid-turn resumes from its checkpoint on the other, and concurrent turns on one
conversation serialize across replicas, in CI; the scale profile serves one conversation from both
replicas and survives one being killed; app code outside the storage adapters does not grow beyond
what the new probe and app factory need.

### T-49 — `feat(T-49): verified identity, operator roles, audit and erasure`
Merchants send an RS256 JWT checked against the provider's JWKS (`AUTH_JWKS_URL`), issuer and
audience; the merchant is `sub`, the body's `user_id` is gone (a body naming one is a 422), and
InjectedState is unchanged. `make dev-keys` writes a local key pair (`.dev/`, ignored by git and
the image; compose mounts only the public half) and `scripts/dev_token.py` mints tokens
(both now `scripts/dev_auth.py`, T-56). Operators
carry their own tokens with a role, operator or supervisor; the shared `OPERATOR_TOKEN` is gone.
Every operator action and every write (expiry, erasure, purge) appends to an audit log whose table
refuses UPDATE and DELETE by trigger on SQLite and Postgres; supervisors read it at `GET /audit`.
A merchant is held to `RATE_LIMIT_PER_MINUTE` (429 with `Retry-After`). `DELETE
/me/conversations/{id}` erases checkpoints, cases, notes and replies; `make purge` erases
conversations idle past `RETENTION_DAYS`. Another merchant's conversation is a 404.
`docs/security.md` maps OWASP's Top 10 for Agentic Applications (ASI01–ASI10) to controls and test
files and applies the Rule of Two to each flow. Tests authenticate through one helper,
`tests/identity.py`.
**Done when:** forged, expired, wrong-audience, wrong-issuer and missing tokens are 401; another
merchant's conversation is 404; the role matrix holds; erasure leaves no row of the conversation
in SQLite or Postgres; the audit log cannot be updated or deleted.

### T-50 — `feat(T-50): one write action behind confirmation`
A support turn mixes untrusted input and private data, so a state change needs a human to approve
it (Meta's Agents Rule of Two). The support planner may propose `open_technical_ticket` for a
terminal still offline after the basic steps; code picks the merchant's own terminal and checks
it is offline, or the case goes to a person. The graph pauses in `confirm_action` with
`interrupt(response_schema=ActionDecision)`, and `/chat` answers `pending_confirmation` with
`pending_action`: an id and the operation rendered from the arguments. Only a `/chat` with that
`confirm_action_id` runs it; any other message cancels it and then runs as its own turn. The
ticket backend (`app/tools/tickets.py`) opens one ticket per proposal id, so a replay or a second
confirmation returns the same receipt, and another merchant's id is a 404; a failure mid-write
leaves no ticket and escalates as `tool_unavailable`. Proposals, tickets and cancellations are
audited. The first reply of a conversation says it is automated, and every handoff says the
operator already has the summary. `docs/tools.md` lists every tool with its schema, side effects,
idempotency and failure modes; the demo shows the proposal, the confirmation and the replay.
**Done when:** no confirmation, no write; a replayed or concurrent confirmation, one ticket;
another merchant's id, 404; a vendor failure mid-write, no ticket.

### T-54 — `feat(T-54): traces, cost per resolution, alerts and vendor drills`
OpenTelemetry spans in the GenAI conventions: a root span per request whose trace id is the logs'
`trace_id`, `invoke_agent` per node, `chat` per model call with provider, model and input, cached
and output tokens, and `execute_tool` per tool call; no content; OTLP when
`OTEL_EXPORTER_OTLP_ENDPOINT` is set; the conventions are noted as Development status. Metrics:
turns by intent and outcome, model tokens and spend (cost per resolution and cache hit rate),
vendor failures after retries, alongside escalations and node latency. Each model call sends the
static system prompt first, ending at an explicit cache breakpoint with a per-prompt cache key;
`evals/run_cache_benchmark.py` measures the effect (removed by T-56; its result is in
`evals/report.md`). A per-call model timeout
(`LLM_CALL_TIMEOUT_S`) was added after the timeout drill showed a hung vendor making the handoff
draft time out the whole run. The compose profile `observability` (`make observe`) runs
Prometheus with four alert rules, each with a runbook line and a `promtool` unit test, a
provisioned Grafana dashboard and Jaeger. `FAULT_INJECTION` (`model_error`, `model_timeout`,
`web_down`) drives `scripts/drills/run.sh`; `docs/drills.md` records each drill (T-56 turned
both into tests in `tests/test_resilience.py` and a table in `docs/operations.md`).
**Done when:** a turn is one trace named in its logs, with no content in any span; cost per
resolution, cache hit rate and p50/p95 per node are on the dashboard; each alert's unit test
fires; the model drills show the vendor alert firing and the recovery time.

### T-52 — `feat(T-52): replan on findings, within a budget`
The workflow stays where the steps are known; agency is added only where the next step depends
on a finding. A specialist's structured answer may carry a `Need` (knowledge needs the merchant's
terminal model; support found an offline terminal and the merchant asked what to do). Between
steps the supervisor (`app/graph/supervisor.py`) adds the step for it, drops an added step that
does not answer, and stops the turn on a budget: `MAX_HOPS`, `MAX_PLAN_CHANGES` (3),
`MAX_MODEL_CALLS` (12) and `MAX_COST_USD` (0.02), enforced in code. A stop logs the budget and
returns the answered part with a `budget_exhausted` escalation. Added steps only read, never
search the web, and only code marks them. Support gains `get_account_profile` (plan, terminal
model and status). `evals/planning_set.jsonl` holds 20 requests whose next step depends on a
finding; `evals/run_planning_eval.py` compares task completion, steps, model calls, p95 (and the
p95 of simple requests) and cost against `MAX_PLAN_CHANGES=0`. Independent steps stay sequential
until the eval shows `Send` would gain latency. `docs/adr/0001-bounded-replanning.md` records why
this is bounded replanning, not an open loop.
**Done when:** every supervisor rule has a test; the router's schema carries no `requested_by`;
a spent budget sends the answered part and escalates as `budget_exhausted` through `/chat`;
`evals/planning_report.md` compares replanning with the baseline, and the default follows it.

### T-56 — `refactor(T-56): trim what v3 added`
Only what T-47 to T-54 added shrinks; everything v2 had stays, and every behaviour the tasks asked
for stays. The fault-injection setting, its provider wrapper and the drill script go: each vendor
failure (model error, model hang, web search down) is a test that stubs it, and the drill results
become a table in `docs/operations.md`. The cache benchmark goes (two lines in `evals/report.md`
say it never ran), and so do the promtool tests and their CI job. CI becomes two jobs, test
(ubuntu and macOS, with Postgres and gitleaks on ubuntu) and container (build, SBOM, Trivy, smoke,
scale profile). `scripts/dev_auth.py` replaces the two dev identity scripts, and `scripts/verify.py`
keeps only what `make verify` needs. v3's tests keep one test per invariant, share an `api`
fixture, and gain two for code no test reached. Docstrings in v3 code are one line; the ADR is under
25 lines; operations, security and tools are tables. The tokenizer ships gzipped, and the Grafana
dashboard drops fields Grafana defaults.
**Done when:** `git diff origin/main` touches v2 code only where v3 changed it (and the two lines
in `evals/report.md`); app coverage is not lower; CI is under 130 lines; the PR reports v3's added
lines per area before and after, against the plan's target of about 3,000.

### T-51 — `test(T-51): error analysis and evals that gate PRs`
Error analysis first (Husain & Shankar, AI Evals FAQ, Sep 2026), in two sessions. Session 1:
`scripts/sample_traces.py` runs the 100 requests in `evals/trace_requests.jsonl` (the ten
scenarios, follow-ups, compound requests, account and product questions, troubleshooting, general
questions, edge cases and adversarial ones) through `/chat` on the real model, each as its
merchant, and exports masked traces to `evals/traces.csv`. A person annotates at least 30 with
pass or fail and a note, and the notes are clustered into `evals/failure_modes.md` with counts.
Session 2: the obvious failures are fixed directly; each remaining mode (3 to 5) gets one check, a
code assertion where possible, otherwise a binary judge from another model family
(`JUDGE_PROVIDER`, `JUDGE_MODEL`) validated with TPR and TNR on the person's labels
(train/dev/test); 40 red-team cases carry OWASP Agentic ids, 10 of them adaptive. Reports give the
pass-all rate and each check's rate, no weighted composite. Recorded responses let
`make eval-offline` replay everything in CI and fail a PR past `evals/thresholds.toml`;
`make eval` runs live. `evals/README.md` is one table: dataset, size, check, last result, command.
**Done when:** the labels' provenance is stated (a person's, or a model's and how many a person
ratified); each judge's TPR and TNR are reported on a held-out split, and the report calls a judge
not validated while that split holds fewer than 10 of its failures; a regression past a threshold
fails CI with no key.

### T-53 — `feat(T-53): fill the corpus gaps and test a reranker`
The error analysis found answers left partial for want of a page. The corpus gains the site's FAQ
pages on antecipação de vendas, Pix, crediário, Receba Já, operating the terminal and the Get
Smart, the Pix product page, two articles (Pix on the terminal, when to anticipate), and the user
manuals of the Get Smart, Get Clássica and Get Mini, whose error tables cover terminal
troubleshooting. The FAQ parser also reads the category layout (`a.c-more-answers__question`,
paired by the same `data-modal` id), a third route reads a PDF manual page by page, and the
snapshot is re-extracted. The retrieval set keeps its 37 questions and gains 60 from the error
analysis and the new pages. Every in-domain question carries the ids of the chunks that answer
it, drafted by a reasoning model from the corpus; every row reviewed by the owner, 0 edits, through
`evals/retrieval_review.csv`.
The abstention threshold is recalibrated on the larger set, by the same midpoint rule.
`evals/run_rerank_benchmark.py` compares recall@5, MRR and the latency each search adds, for
hybrid RRF alone and with a reranker over its 20 fused candidates: `BAAI/bge-reranker-v2-m3` and
`Qwen/Qwen3-Reranker-0.6B` (local, in an optional `rerank` dependency group, weights fetched by
the image build and never in tests) and Cohere `rerank-v4.0-fast` (API), behind
`RERANKER=none|bge|qwen|cohere`. The latency budget, set before measuring: at most 1 s added at
p95 per search, an eighth of the 8 s at which the node latency alert fires. A reranker is on by
default only if it raises recall@5 or MRR within that budget, and the table goes in `docs/rag.md`
either way. A reranker that fails degrades to the fused order. Cohere met that rule; the owner kept
RRF the default and Cohere opt-in (`docs/adr/0002-reranker-off-by-default.md`): a gain of 2 of 82
questions, a new vendor that needs compliance approval, and a trial key's rate limit.
The re-recorded traces moved failure mode 4 to other traces, so knowledge prompt v9 states a
fact whenever an excerpt has it and names every difference in a comparison. The floors in
`evals/thresholds.toml` are set from the accepted v9 recording.
**Done when:** the new routes have tests on excerpts of real pages; the owner reviewed the gold
ids; the table in `docs/rag.md` comes from a committed report; the default and why are in ADR
0002; the trace evals are re-recorded on the new corpus; the offline suite downloads nothing.

### T-55 — `docs(T-55): evaluator guide, ADRs and pilot plan`
`REVIEW.md` at the root gives each V4 criterion one row: the evidence (file, test, number), the
offline command that reproduces it, and its known limit. `docs/adr/` records the decisions that
shape the system, one short ADR each: LangGraph, hybrid retrieval with RRF (the reranker is ADR
0002), bounded replanning (ADR 0001), write actions behind confirmation, Postgres for replicas and
JWT identity, plus one ADR for what was considered and not done, each with its reason: an open
agent loop, an MCP server, Kubernetes, a model cascade and a semantic cache. `docs/pilot.md` rolls
the assistant out by intent from shadow to assisted to autonomous, with numeric entry and exit
gates taken from the evals and metrics, a rollback trigger, and one cost/benefit table where every
input is sourced or marked as an assumption. The README opens with "Since V4": each gap the
evaluator named, the change and its evidence. A clean copy runs `make verify`, `docker compose up`,
`scripts/demo.sh` and the `scale` and `observability` profiles, with the outputs in
`docs/demo.md`; Linux runs `make verify` in CI. Dead code goes, and every README number matches a
committed report. A body `user_id` is accepted again, only when it is the token's `sub` (403
otherwise), so the challenge's `{"message", "user_id"}` payload works with a token; `make verify`
leaves the tree clean and writes `reports/verify.md` only when asked.
**Done when:** `REVIEW.md` covers the 11 criteria; every number in it and in the README comes from
a committed report; each ADR is under 25 lines; the pilot's gates are numbers the repo measures;
`docs/demo.md` shows runs of this commit.

### T-57 — `test(T-57): a held-out set no prompt was tuned on`
The T-51 traces stopped being a test once prompts were fixed against them.
`evals/heldout_requests.jsonl` holds 80 new requests that no prompt was tuned on. 55 are stratified
like `trace_requests.jsonl`: 8 account, 8 product, 8 adversarial, 8 edge, 7 follow-up, 6
challenge-like scenarios, 4 compound, 3 troubleshooting and 3 general. The other 25 ask for a fact
the site's evidence states (failure mode 4 bait), flagged only in the jsonl. 74 are in Brazilian
Portuguese as a merchant writes it (informal, short, typos left in) and 6 in English, the mix of the
tuning set; a `lang` field says which. Ids are neutral and the order is shuffled, so the export does
not show which is which. `scripts/sample_traces.py` runs them through `/chat` on the current code
and exports them masked to `evals/heldout_traces.csv`, with the columns of `traces.csv`, empty
`pass_fail` and `note`, and the whole evidence, so a labeller can check a fact. No label is drafted:
the owner labels them blind, into `evals/heldout_labels.csv`. `evals/run_heldout.py` reports the
pass rate by those labels (overall, bait and the rest) and the `missing_info` judge's TPR and TNR
against them, validated only with at least 10 labelled failures of its mode and both rates at 0.8;
its answers are recorded, so the report replays with no key. **Done when:** the 80 requests share no
message with `trace_requests.jsonl`; the export has 80 rows with empty labels; no prompt changes in
this task.

### T-59 — `docs(T-59): a shorter readme, merged ADRs and a one-page review`
Docs only. The README keeps its header, Since V4, the quick start with the curl, the architecture,
the evaluation summary and the known limitations, at most 250 lines. Everything else moves behind
links:
- the message flow and the design decisions to `docs/architecture.md` and the ADRs;
- the human-review loop to `docs/human-in-the-loop.md`;
- the demo table to `docs/demo.md`;
- the guardrail and the financial hard rules to `docs/security.md`;
- what changed since v1 and the process to `docs/history.md`.

The hybrid-retrieval ADR (0004) and the reranker ADR (0002) become one, under 0002's path, which
the code cites. `REVIEW.md` fits one page. The quick start names its prerequisites (Docker, make
and uv, which installs Python 3.12 itself), and `make up` prints how to install uv when it is
missing. Code, tests and v2 docstrings are unchanged.
**Done when:** the README is at most 250 lines; every relative link and anchor resolves; no ADR
repeats another; the diff touches only Markdown and the Makefile's uv check.

### T-58 — `docs(T-58): record two replanning prompt rules, tried and reverted`
The two main causes of replanning misses (`evals/planning_report.md`):
- knowledge answering from the site without the account fact that decides;
- escalations one account lookup would avoid.

They get prompt or router rules only, no new node. A rule stays only if planning completion rises
in all three runs and no gate drops. The planning eval runs three times before and after, and the
held-out requests' code checks run live before and after. Completion rose in one run of three, so
the rules were reverted and the measurement recorded.
**Done when:** the before and after runs, the gates and the held-out checks are in
`evals/planning_report.md`, and no prompt differs from `main`.

### T-60 — `fix(T-60): speak as the acquirer and fail answers that credit the site`
The owner's held-out labels found 11 jargon failures the check passed: answers that credit the
site ("o site informa", "o site não informa") instead of stating the fact. The knowledge and web
prompts now speak as the acquirer ("A Get Smart custa...") and never credit the site, a page or
the sources; a missing fact is said plainly. The jargon check also fails an answer that credits
the site, a page or the sources, in the phrasings the tuning traces show, and no fixed system text
(the source-date notices) trips it. The held-out requests run live before and after the prompt
change, counting the answers that credit the site; the evals are re-recorded.
The floors in `evals/thresholds.toml` move to the accepted recording's rates, as their rule says.
**Done when:** the count before and after is in `evals/failure_modes.md`; the floors are the v11
recording's rates (pass-all 0.96, jargon 1.0, missing info 0.95), and its replay meets them.

### T-62 — `fix(T-62): keep each compared item's pages and quote exchange rates in reais`
Two challenge scenarios answered wrong in the live demo on #12's dependencies. S01 left out the
Get Smart price: the merge of per-query rankings gave the last item's search one slot, so the Get
Smart page, its second hit, was cut. The merge now runs in rounds, every query adding its best hit
not yet taken, with the same number of rounds for every query, so a comparison can return a few
more than top-k hits. S07 quoted the euro as 1,1203 with no currency: the web prompt now quotes a
rate as "1 EUR = R$ X", copied from a result in reais, or says none was found, and a code check
fails an exchange-rate answer with no "R$" that is not a not-found. A forecast gives the maximum
and minimum copied from an hourly table, and the jargon check also fails "os resultados/os dados
não informam". Tuned on the tuning traces only; the held-out requests run once at the end.
**Done when:** S01 and S07 run live three times each, with the Get Smart price and a rate in reais
or a not-found; recall@5 on the 82 in-domain questions does not drop; the floors are the accepted
re-recording's rates and its replay meets them.

### Traceability

| Criterion (v1 score) | Tasks |
| --- | --- |
| Memory / multi-turn (1.5) | T-19, T-20, T-24, T-34 |
| Human-in-the-loop (5.8) | T-21, T-24, T-42, T-44, T-50 |
| Infra / Docker (6.8) | T-16, T-17, T-18, T-33, T-47, T-48 |
| Product / completeness (7.5) | T-23, T-27, T-30, T-32, T-46, T-53 |
| Multi-agent / orchestration (8.8) | T-22, T-43, T-52 |
| Observability / evals (9.1) | T-25, T-26, T-31, T-51, T-54 |
| Tests / quality (9.2) | T-24, T-31, plus the tests inside every task |
| Architecture / code (9.5) | every task: no new framework, no function over 40 lines; T-56 |
| Security / isolation (9.5) | T-19, T-21, T-23, T-44, T-47, T-49 |
| RAG / knowledge (9.8) | T-16, T-23, T-29, T-33, T-53 |
| Trade-offs / engineering (10.0) | T-27, T-28, T-45, T-55 |

Outside the task list: the pull request that merges v2 into `main` after T-27 (plan §15), and,
by hand, pushing the `v1` tag, the walkthrough video (at most 15 minutes) and the submission
message.
