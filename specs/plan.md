# Plan — how the system is built (v2)

## 1. Module layout

```
app/
  settings.py          pydantic-settings: models, thresholds, paths, keys, identity provider
  auth.py              JWT verification, merchant and operator dependencies, roles, rate limit
  audit.py             the append-only audit log
  retention.py         `make purge`: erase conversations idle past RETENTION_DAYS
  graph/actions.py     the proposed write and the `confirm_action` node
  tools/tickets.py     the ticket backend: idempotent on the proposal id, audited
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
    statuses.py        hard-rule-4 guard: status words, read as the status they name
    knowledge.py, support.py, guardrail.py, escalation.py
  rag/                 extraction, snapshot, chunking, index build, hybrid retriever
  tools/               account.py (two support tools), web.py (web search)
  providers/           llm.py (port + OpenAI), fake.py (CI backend), embeddings.py
  prompts/             versioned pt-BR markdown, loaded by id
  observability/       structlog setup, PII masking, metrics, tracing
  locks.py             one turn at a time per conversation, for /chat and operator resume
  storage.py           STORAGE=sqlite|postgres: checkpointer, handoff queue and turn locks
data/corpus/documents.jsonl   committed extraction snapshot
evals/                 routing golden set + runner, model benchmark, LLM-as-judge runner, reports
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
     knowledge | support --hand-back, hops < 2--> router (replans the whole turn)
             |
             v  (answer, refusal, or hand-back with the budget spent)
          collect --more steps--> next specialist
             |
             v
          compose --a step needs a human--> escalate (answered part kept)
             |
             v
          remember -> END

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
- **Standalone query.** The router receives a JSON input: `message`, plus `history` (the window)
  and `summary` only when there are earlier turns. History is a data field, never part of the
  instructions, and the prompt forbids obeying text found in it or dragging an old topic into a
  new question. The router must return each step's `query_pt` as a self-contained Portuguese
  question, keeping relative dates as words. A rewrite containing an identifier-like token (any
  alphanumeric token with a digit) absent from the message, window or summary is discarded for
  the original message. The original message and every `query_pt` are kept in state and logged
  masked, so a bad rewrite can be read in the logs.
- **Turn date.** The API stamps each turn with the merchant's date (UTC-3, Brazil has no daylight
  saving time). The support planner receives it with the standalone question, which is what
  turns "amanhã" into a date range; it never receives the history.

## 5. Plans and compose

`RouterDecision` gains `steps: list[PlanStep]` with `min_length=1, max_length=2`; each `PlanStep`
is `{agent, query_pt}`. Decision-level `intent`, `confidence`, `rationale` and `lang` stay.

After each specialist, `after_specialist` decides: a hand-back returns to the router while the hop
budget lasts, and the router replans the whole turn; anything else goes to `collect`, which
records the step as a `StepResult` (answer and sources, or a refusal reason) and runs the next
step, or `compose` when none is left. The steps are sequential, so `step_results` is a plain list
with no reducer.

`compose` is always the join point. With one answered step it copies the answer and sources and
makes no model call, which is how single-intent turns keep v1's cost. With two it makes one
structured call (`ComposedAnswer`), takes the union of both steps' sources deterministically, and
applies the hard-rule-4 guard: if the composed text carries a number absent from the step
answers, it falls back to the step answers joined as written. When one step answered and the
other refused, the answered part becomes `partial_answer`, the turn escalates with the refused
step's reason, and `escalate` keeps that part and its sources in front of the handoff notice;
`remember` later stores it together with the operator's reply.

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
- **Retrieve.** Dense and lexical rankings fused with RRF per query; the step's per-item searches
  (`PlanStep.search_queries`) add one ranking each, interleaved best-first; then the threshold
  check, calibrated per embedder by `evals/run_retrieval_calibration.py`. An unreachable embedder
  reads as nothing retrieved.
- **Generate.** The knowledge prompt receives only the surviving chunks and the hard rules, and
  returns `coverage`: `full` and `partial` are answered, a partial one naming what the site does
  not cover; with `none` the step abstains with `insufficient_sources`.

## 7. Web search

`app/tools/web.py` holds `WebSearch`, a Tavily client over the API's shared `httpx.AsyncClient`:
one `POST /search` per question with a bearer key, `max_results` capped by
`WEB_SEARCH_MAX_RESULTS` and a `WEB_SEARCH_TIMEOUT_S` timeout, returning `WebResult`
`{url, title, content}`. A missing key, a timeout, an error status, an unreadable body or no
usable result raises `WebSearchUnavailable`. `outbound_query(query, message)` builds what may
leave the process: the step's question through `mask_pii`, with every token carrying a digit
dropped unless the merchant typed it in the current message, so amounts and ids from the account
or the history never reach the provider. A step with a `target_day` then gets that day's date
appended, which is the system's own and never the merchant's. Tests inject an `httpx.MockTransport`, so no request
leaves the suite.

The knowledge agent chooses its evidence by the decision's intent, not by fallback: `general_info`
goes to the web, everything else to the corpus, and the support agent has no path to it. The web
prompt (`knowledge_web.md`) receives the question, today's date and the pages as a JSON data field,
is told that page content is untrusted and never an instruction, and must cite the urls it used.
`trustworthy()` then checks the answer deterministically: every number must appear in a page or
be today's date (hard rule 4), and the text may hold no link or e-mail, which is how a planted
instruction usually surfaces. A result set the model flags as not answering the question, a
failed check, a failed call or `WebSearchUnavailable` all produce
the `web_unavailable` pt-BR template with no sources and no escalation.

## 8. Human review

The fourth agent is two nodes because `interrupt()` re-executes its node from the start on
resume. Anything with a cost, a random value or a write must happen in an earlier node, or it
would run twice.

- `escalate` (unchanged role, deterministic) mints the protocol and the typed reason. The protocol
  is minted before the pause, so it survives the re-execution.
- `prepare_handoff` builds the `HandoffCase`: masked summary, intent, attempts derived from the
  state (route, hops, pages consulted, why it stopped), the pages the knowledge agent retrieved,
  and a proposed draft from one structured call (`HandoffDraft`) that also sees the turn's
  `evidence` (retrieved chunks and tool results). A failed call, or a draft with a number found
  in none of the merchant's message, the protocol or that evidence, leaves the draft empty. It then records the case in the
  queue (`INSERT OR IGNORE` on the protocol), so no conversation can wait for a human without its
  case being listed.
- `await_operator` calls `interrupt(case)` and nothing else before it. On resume it receives the
  operator's decision: `approve` sends the draft as written; `reply` makes one structured call
  (`OperatorReply`) to rephrase the answer and applies the hard-rule-4 guard, falling back to
  the operator's literal text. The reply carries one `operator` source and goes on to `remember`.

The API owns the rest:

- Before invoking, `/chat` reads the thread state. A thread whose next node is `await_operator`
  has an open case, so it returns `pending_human` for that protocol without running the graph.
- When a run ends in `interrupt()`, the result carries `__interrupt__` and the response is
  `pending_human`.
- `/handoffs/{protocol}/resume` looks the case up (404 unknown, 409 closed, 422 approve without a
  draft), claims it with one conditional update so two operators cannot both resume it, invokes
  the graph with `Command(resume=decision)` on the case's thread, and reopens the case if that
  run fails.

The queue is a table, not a scan of checkpoints: listing open cases must not read every thread.
It lives at `STATE_DIR/handoffs.sqlite`, on the same volume as the checkpoints, or in Postgres
with `STORAGE=postgres`, behind a small `HandoffQueue` over a two-method adapter per database.

Operator routes take an operator JWT (`app/auth.py`): `OPERATOR_AUDIENCE` and a role. Each call
appends to the audit log, whose table refuses UPDATE and DELETE by trigger.

## 9. Prompts

Markdown files under `app/prompts/`, each with a `version` in front matter, loaded by id. No
prompt literal appears in Python. v2 bumps `router` and adds `summary`, `handoff_draft`,
`operator_reply`, `pending_human`, `compose`,
`knowledge_web`, `web_unavailable` and `judge`. `out_of_domain` is removed with the outcome that
used it.

## 10. Configuration and startup

`pydantic-settings` reads `.env`; `.env.example` documents every setting.

| Group | Settings |
| --- | --- |
| Model | `LLM_BACKEND` (`openai` or `fake`), `OPENAI_API_KEY`, `LLM_MODEL`, `LLM_REASONING_EFFORT`, `LLM_TEMPERATURE`, `JUDGE_MODEL` |
| Retrieval | `EMBEDDING_BACKEND`, `EMBEDDING_MODEL`, `INDEX_PATH`, `COLLECTION_NAME`, `RETRIEVAL_TOP_K`, `RETRIEVAL_CONFIDENCE_THRESHOLD` |
| Routing | `ROUTER_CONFIDENCE_THRESHOLD`, `MAX_HOPS` |
| Memory | `STATE_DIR`, `HISTORY_WINDOW`, `HISTORY_MAX_MESSAGES` |
| Web | `TAVILY_API_KEY`, `WEB_SEARCH_MAX_RESULTS`, `WEB_SEARCH_TIMEOUT_S` |
| Identity | `AUTH_JWKS_URL`, `AUTH_ISSUER`, `MERCHANT_AUDIENCE`, `OPERATOR_AUDIENCE`, `RATE_LIMIT_PER_MINUTE`, `RETENTION_DAYS` |
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
  with optional prior turns per row (`history`), words the rewrite must keep
  (`expected_mentions`), and expected plans.
- `uv run python -m evals.run_model_benchmark` — the golden set routed through each candidate chat
  model, with accuracy, latency, tokens and cost, written to `evals/router_benchmark.md`. The
  default `LLM_MODEL` and `LLM_REASONING_EFFORT` are chosen from it.
- `uv run python -m evals.run_judge` — the ten challenge scenarios, two follow-ups, a compound
  request and the human-review loop through the real graph, every answered turn scored by
  `JUDGE_MODEL` (a tier above the chat model) for groundedness against that turn's `evidence` and
  for relevance, written to `evals/report.md` with the retrieval calibration numbers. The judge
  is given the merchant's day the turn was answered on, so "ontem" means the same day to both.

No eval runs inside pytest: a statistical measure that dips one case in thirty is not a red
test suite.

### Every v2 invariant and the offline test that pins it

| Invariant (spec section) | Test |
| --- | --- |
| `conversation_id` minted when absent, echoed, validated (§3) | `test_api::test_a_conversation_id_is_minted_when_absent_and_echoed_when_given`, `test_a_malformed_conversation_id_is_rejected` |
| Response consistency: escalation iff escalated, operator source only when resolved, no agent on router escalation (§3) | `test_support::test_response_cannot_claim_an_answer_and_an_escalation`, `test_an_operator_source_needs_an_escalation_the_operator_answered`, `test_router_level_escalation_cannot_name_a_specialist` |
| kb and web sources carry a url, account and operator ones never (§3) | `test_support::test_pages_are_cited_with_a_url_and_nothing_else_is` |
| Operator endpoints: 401, 503, 404, 409, 422 (§3) | `test_human_review::test_operator_routes_reject_a_missing_or_wrong_token`, `test_operator_routes_are_disabled_without_a_configured_token`, `test_an_unknown_protocol_is_404`, `test_resuming_a_closed_case_is_409`, `test_approving_a_case_without_a_draft_is_422` |
| Open cases listed oldest first (§3) | `test_handoffs::test_open_cases_are_listed_oldest_first` |
| A plan has one or two steps; one step costs what v1 did (§5) | `test_plans::test_a_three_step_plan_is_rejected_by_the_schema`, `test_a_single_intent_request_makes_the_same_calls_as_before` |
| Compose keeps every source and adds no number (§5) | `test_plans::test_a_compound_request_runs_support_then_knowledge_and_keeps_both_sources`, `test_a_composed_answer_with_a_new_number_falls_back_to_the_step_answers`, `test_flows::test_a_compound_request_answers_both_parts_with_every_source` |
| Partial answer plus handoff under one protocol; hop budget spans the plan (§5) | `test_plans::test_one_step_answers_now_and_the_other_goes_to_a_human_under_one_protocol`, `test_the_hop_budget_holds_across_the_plan` |
| Router failure is zero confidence (§5) | `test_router::test_provider_failure_becomes_zero_confidence_not_an_exception` |
| History persists and survives a restart (§6) | `test_memory::test_a_second_turn_finds_the_first_in_the_history`, `test_a_graph_rebuilt_over_the_same_sqlite_file_continues`, `test_flows::test_a_second_turn_reads_the_first`, `test_a_restart_over_the_same_state_directory_continues_the_conversation` |
| Thread bound to user and conversation (§6) | `test_memory::test_the_same_conversation_id_under_another_user_starts_empty`, `test_flows::test_the_same_conversation_id_under_another_merchant_starts_empty` |
| Per-turn fields reset; blocked turns forgotten; history masked; summary fold (§6) | `test_memory::test_per_turn_fields_do_not_leak_into_the_next_turn`, `test_a_blocked_turn_is_not_remembered`, `test_the_history_holds_the_masked_text`, `test_older_exchanges_fold_into_the_summary`, `test_a_failed_fold_keeps_the_history_whole` |
| No node imports the checkpointer; restricted deserialization (§6) | `test_memory::test_no_graph_module_imports_a_checkpointer`, `test_checkpoints::*` |
| Follow-ups rewritten, history as data, no invented identifier (§6) | `test_followups::*`, `test_router::test_a_search_that_invents_an_identifier_is_dropped` |
| Comparison retrieves each item's page; abstention below threshold (§7) | `test_retrieve::test_a_comparison_retrieves_the_page_of_each_item`, `test_knowledge::test_below_threshold_refuses_without_calling_the_model` |
| Coverage: full and partial answered, none escalates (§7) | `test_knowledge::test_excerpts_that_answer_part_of_the_question_are_answered`, `test_excerpts_that_do_not_answer_abstain_instead_of_saying_so`, `test_human_review::test_a_product_question_the_excerpts_do_not_answer_goes_to_a_human` |
| Unreachable embedder abstains (§7, §14) | `test_knowledge::test_an_unreachable_embedder_reads_as_nothing_retrieved` |
| Web only for general questions; masked, dated, capped, degraded (§7) | `test_web::*`, `test_flows::test_a_general_question_is_answered_from_the_web_with_a_key`, `test_without_a_key_a_general_question_degrades_and_nothing_leaves`, `test_the_web_query_that_leaves_is_masked` |
| Guardrail: injection and identity refused as separate outcomes, the caller's own id allowed, PII masked, system ids untouched (§10) | `test_guardrail::*`, `test_adversarial::*`, `test_observability::*` |
| Replicas over Postgres: escalated on one, answered on the other; a dead replica's turn resumes; turns serialize across replicas (§13) | `test_replicas::*` (CI job `replicas`), the `scale` CI job |
| Node timeout and retry; a model that keeps failing escalates, a bug fails the run (§13) | `test_resilience::*` |
| One turn at a time per conversation; other conversations never wait (§3) | `test_locks::*`, `test_flows::test_two_messages_on_one_conversation_take_turns`, `test_different_conversations_do_not_wait_for_each_other` |
| A failed model call escalates as `model_unavailable`; older stored reasons still load (§11) | `test_router::test_provider_failure_becomes_zero_confidence_not_an_exception`, `test_knowledge::test_generation_failure_refuses_instead_of_raising`, `test_support::test_planning_failure_does_not_raise`, `test_escalation::test_a_model_failure_escalates_with_its_own_notice`, `test_a_reason_stored_before_the_rename_still_loads` |
| Escalation deterministic, typed reason, masked summary (§11) | `test_escalation::*` |
| Human review: case before pause, pending runs nothing, approve or reply, rephrase guarded, survives restart (§11) | `test_human_review::*` |
| Hard rule 1 and 4: no number or status its source lacks (§9) | `test_numbers::*`, `test_statuses::*`, `test_plans::test_a_composed_answer_with_a_new_number_falls_back_to_the_step_answers`, `test_a_composed_answer_that_flips_a_status_falls_back_to_the_step_answers`, `test_human_review::test_a_rephrasing_with_a_new_number_falls_back_to_the_operator_text`, `test_a_rephrasing_that_flips_a_status_falls_back_to_the_operator_text`, `test_web::test_an_answer_with_a_number_the_results_lack_degrades` |
| Hard rule 3: declines answered from the recorded reason, otherwise a person (§9) | `test_support::test_a_decline_is_explained_with_the_reason_the_issuer_recorded`, `test_a_decline_the_data_cannot_explain_goes_to_a_person` |
| Hard rule 5: a person is promised only with a protocol (§9) | `test_plans::test_an_answer_never_promises_a_person_when_no_protocol_opens`, `test_an_answer_that_only_promises_a_person_opens_the_protocol` |
| One write behind confirmation: none without it, one per proposal, 404 across merchants, nothing partial on a vendor failure; first reply discloses automation (§3) | `test_actions::*`, `test_replicas::test_a_ticket_proposed_on_one_replica_is_confirmed_once_on_the_other` |
| Merchant only from a verified token; forged, expired or misaddressed tokens 401; roles; rate limit; erasure leaves nothing; audit append-only; retention (§3) | `test_security::*`, `test_replicas::test_erasing_on_one_replica_leaves_nothing_in_postgres`, `test_the_audit_log_cannot_be_rewritten_in_postgres` |
| Identity injected, never in a tool schema (§8) | `test_tools::test_user_id_is_not_in_the_schema_the_model_sees`, `test_a_smuggled_user_id_cannot_reach_another_customer`, `test_adversarial::test_another_customers_data_is_unreachable_through_the_graph` |
| Settlements by sale or credit date, derived status, merchant's money notation (§8) | `test_tools::test_yesterdays_sales_are_found_by_sale_date_and_credit_today`, `test_records_are_read_as_of_the_turns_day`, `test_amounts_reach_the_model_in_the_merchants_notation`, `test_support::test_tomorrows_sales_are_read_as_the_money_crediting_tomorrow` |
| A tool error escalates, never a stack trace (§14) | `test_flows::test_an_unreadable_account_escalates_instead_of_failing_the_request` |
| First boot: no key, no half index (§13) | `test_ingest::test_a_first_boot_without_a_key_stops_with_the_fix_and_writes_nothing`, `test_a_build_that_fails_halfway_keeps_neither_half` |
| Nothing downloads at runtime; the suite opens no socket (§13) | `test_offline::*`, `pytest-socket` with `--disable-socket` in `pyproject.toml`, `scripts/verify.py` failing on a blocked attempt |
| The suite reads no developer configuration and no clock | `test_model_settings::test_the_suite_never_reads_the_developers_configuration`, the `pinned_today` fixture |

Hard rule 2 (a forecast is labelled as one) lives in the support prompt and is checked by the
judge, not offline: no scripted model can show that a real one labels it.

## 12. Docker and CI

Multi-stage build with uv, no API key at build time. The image includes the committed corpus
snapshot, and the local embedding model is fetched during the build (`python -m
app.providers.embeddings`) so the first boot needs no download besides the LLM calls themselves;
the chunker's tokenizer is committed under `app/rag/assets/`. Base images are pinned by digest. The container command runs
`python -m app.rag.ingest --if-missing`, then starts uvicorn. Ingestion checks the embedder before
reading anything and writes the vectors before the BM25 corpus, dropping the collection if the
vectors fail, so a keyless or rejected first boot stops with one line and leaves no half index. Compose declares two named volumes,
`index` and `state`, so the index can be dropped and rebuilt without touching a conversation; it
loads `.env` and has a healthcheck whose start period covers the first index build.

`.github/workflows/ci.yml`, on push and pull request: `make verify-offline` on ubuntu (inside
`unshare --net` after `uv sync --frozen`) and macOS; gitleaks over the history; `docker compose
build`, a syft SBOM and a Trivy scan failing on critical findings outside `.trivyignore`; then the
container with `LLM_BACKEND=fake`, `/health`, `/metrics` and one `/chat`. `make verify` is the same
offline phase after a frozen sync, from a clean clone. The README carries the workflow badge.
Two more jobs: `tests/test_replicas.py` against a Postgres service, and the `scale` profile
(Postgres, `api-a`, `api-b`, nginx) brought up keyless, checked through the balancer, then with one
replica killed. The image's healthcheck and compose wait on `/ready`; uvicorn runs with
`--timeout-graceful-shutdown 30` and compose's `stop_grace_period` is 40 s.

## 13. Observability

- **Logs.** structlog JSON with the masking processor, as in v1. The router logs the masked
  original message and each masked `query_pt`.
- **Metrics.** `app/observability/metrics.py` defines two families with `prometheus-client`:
  `support_escalations_total{reason}` and `support_node_latency_seconds{node}` (histogram). Both
  are recorded in `instrument_node`, which already wraps every node, so no agent can forget them.
- **Tracing.** `app/observability/tracing.py` sets LangSmith's global switch once in the
  lifespan. With `LANGSMITH_TRACING` on and a key, it points every trace at one client whose
  `hide_inputs` and `hide_outputs` apply `mask_pii`; otherwise it sets the switch off, which also
  overrides LangChain's own, unmasked, environment-driven tracer. Off by default, no per-request
  cost, and an upload error stays in LangSmith's background thread.
- **Evidence.** Each specialist records the text it answered from or looked at (chunks, tool
  result or snippets) in the per-turn `evidence` field, introduced with the handoff draft guard
  in T-21. It never reaches the response or the history; the judge grades against it.

## 14. Deliberately not abstracted

No repository interface over the account fixture or the handoff queue, no agent base class, no
message bus, no web-search port. The one port that exists, `LLMProvider`, has two implementations
for a reason: the vendor is expected to change, and CI needs a deterministic backend.

## 15. Delivery

v2 is built on a feature branch, one commit per task, and merges to `main` through a single pull
request at the end, after T-27. CI must be green on the pull request and again on `main` after
the merge; the README badge tracks `main`. v1 stays reachable at the tag `v1`.
