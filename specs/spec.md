# Spec — multi-agent support system (v2)

## 0. What changed since v1

v1 was evaluated at 8.7/10. v2 closes every gap in that feedback and keeps what scored high:
hybrid RAG with abstention, the `user_id` security boundary, a small graph, an offline test
suite and written trade-offs.

| Criterion (v1 score) | v2 change | Section | Tasks |
| --- | --- | --- | --- |
| Memory / multi-turn (1.5) | Persistent conversations keyed by user and conversation; follow-ups rewritten into standalone queries | §6 | T-19, T-20 |
| Human-in-the-loop (5.8) | A fourth agent pauses the graph for an operator and continues from the operator's answer | §11 | T-21 |
| Infra / Docker (6.8) | A fresh clone runs with one command; CI builds the image and smoke-tests the container | §13 | T-16, T-17, T-18 |
| Product / completeness (7.5) | All ten challenge scenarios answered, including weather and exchange rate via web search | §7 | T-23, T-27 |
| Multi-agent / orchestration (8.8) | The router becomes a supervisor that plans up to two specialists and composes their results | §5 | T-22 |
| Observability / evals (9.1) | LLM-as-judge report, metrics, optional masked tracing | §13 | T-25, T-26 |
| Tests / quality (9.2) | Multi-turn, human review, isolation, restart and compound cases; CI green | §13 | T-24 |
| Security / isolation (9.5) | Thread bound to `user_id`, masked web query, masked history, operator token | §6, §7, §11 | T-19, T-21, T-23 |
| RAG / knowledge (9.8) | Hybrid retrieval and abstention unchanged; web only for general questions, always cited | §7 | T-23 |
| Architecture / code (9.5) | No new framework, no function over 40 lines | — | all |
| Trade-offs (10.0) | README design notes on the five interview topics | — | T-27 |

v1 is frozen at the git tag `v1` for comparison.

## 1. Purpose

A support system for a payments acquirer. One HTTP entry point receives a merchant message inside
a conversation and returns a grounded answer in Brazilian Portuguese, or hands the case to a human
operator and continues from the operator's answer. Four agent roles cooperate:

- **router** (supervisor): decides which specialists handle the turn, in what order, and rewrites
  the turn into a standalone query;
- **knowledge**: answers from the indexed website corpus, or from cited web results for
  general-purpose questions;
- **support**: reads the caller's own account data through two tools;
- **human_review**: pauses the graph, hands an operator a structured case, and turns the
  operator's answer into the reply.

## 2. Scope

In scope: the four agents, persistent multi-turn memory, supervisor plans of up to two
specialists, RAG plus web search, support tools, escalation and human review, input guardrail,
PII masking, offline tests, routing golden set, LLM-as-judge evals, metrics, Docker, CI.

Out of scope, and not to be added: OpenTelemetry, MCP, pushing operator replies to a messaging
channel, merchant authentication (the gateway's job), several API replicas over one SQLite file,
parallel plan steps, plans longer than two steps.

## 3. API contract

### `POST /chat`

`Authorization: Bearer <merchant JWT>`

```json
{ "message": "string", "conversation_id": "string (optional)", "confirm_action_id": "string (optional)" }
```

```json
{
  "conversation_id": "string",
  "status": "answered | pending_human | pending_confirmation",
  "answer": "string (pt-BR)",
  "intent": "product_info | troubleshooting | account_data | individual_case | general_info | other",
  "agents": ["support", "knowledge"],
  "confidence": 0.0,
  "sources": [{ "source_type": "kb | web | account | operator", "url": "string | null", "title": "string" }],
  "escalated": false,
  "escalation": { "protocol": "ESC-...", "reason": "string", "summary": "string" },
  "pending_action": { "id": "string", "operation": "string" }
}
```

- `conversation_id` is optional on input (1–64 characters of `[A-Za-z0-9_-]`). When absent the
  server mints one. Every response returns it.
- `agents` lists the specialists that ran, in order; it is empty when routing never reached one.
- `escalation` is present exactly when `escalated` is true.
- `status = pending_human` means a human has the case: the answer is a handoff notice with the
  protocol, preceded by any part of the request already answered, whose sources it keeps.
- `status = answered` with `escalated = true` is a reply resolved by an operator; it cites one
  `operator` source whose `url` is null and whose `title` is the protocol.
- `status = pending_confirmation` means the agent proposed a write, today only a technical ticket
  for the merchant's own offline terminal. `pending_action` carries its id and the operation as
  code renders it from the arguments. Only a `/chat` with that `confirm_action_id` runs it; any
  other message cancels it. A replayed confirmation returns the same receipt, and an id that is
  not this merchant's is a 404 (`docs/tools.md`).
- The first reply of a conversation says the assistant is automated, and every handoff notice
  says the operator already has the conversation's summary.
- `kb` and `web` sources always carry a `url`. An `account` source has no url; its `title` names
  the tool that read the merchant's account.

The merchant is the `sub` of an RS256 token whose signature, issuer, audience and expiry are
checked against the identity provider's keys (`AUTH_JWKS_URL`). A body `user_id`, accepted for the
challenge's payload, must be that same merchant or the request is a 403. That id becomes `user_id` in graph state: never a field the model can read or write,
and never in a tool schema. A merchant is held to `RATE_LIMIT_PER_MINUTE` requests (429 with
`Retry-After`). Identity, roles, audit, erasure and the OWASP agentic map are in
`docs/security.md`.

Messages on one conversation are answered one at a time. A second `/chat` on the same
conversation, or an operator's answer to its case, waits until the turn in progress has finished
and then runs on its result, so no turn is lost to another written over it. Other conversations
never wait. On one node the lock lives in the process; replicas over Postgres share it as an
advisory lock on the thread.

### Operator endpoints

All require an operator token: `OPERATOR_AUDIENCE` and a `role`, `operator` or `supervisor`.
Every call is appended to the audit log under the operator's id, and `GET /audit` lets a
supervisor read it. A case is
`{ "protocol", "reason", "intent", "summary", "attempts": ["string"], "sources": [Source], "proposed_draft": "string | null", "user_id", "conversation_id", "opened_at", "expires_at" }`,
shown as `{ "status": "open | closed", "outcome": "answered | cancelled | expired | null", "case": Case, "closed_at", "notes": [{ "at", "message" }] }`.
`notes` are the merchant's messages sent while the case waited, masked.

- `GET /handoffs` lists open cases, oldest first.
- `GET /handoffs/{protocol}` shows one case, open or closed.
- `POST /handoffs/{protocol}/resume` takes `{ "action": "approve" }`, which sends the proposed
  draft, `{ "action": "reply", "answer": "string" }`, or `{ "action": "cancel" }`, which closes
  the case without an answer. It resumes the paused conversation and returns the resulting
  `ChatResponse`, which is also held for the merchant: their next `/chat` delivers it, in
  `delivered`, before that message's own answer.

### Conversation history

`GET /conversations/{conversation_id}`, with the merchant's token, returns `{ "conversation_id",
"status": "active | pending_human", "protocol", "summary", "messages": [{ "role": "merchant |
assistant", "content" }] }`, read through the same thread key as `/chat`; another merchant's
conversation is a 404. `DELETE /me/conversations/{conversation_id}` erases it (LGPD): its
checkpoints and every case it opened, then 204; `make purge` erases conversations idle for longer
than `RETENTION_DAYS`.

| Condition | Status |
| --- | --- |
| Missing, forged, expired or misaddressed token | 401 |
| Token without the needed role | 403 |
| Unknown protocol | 404 |
| Case already closed | 409 |
| Approving a case that has no draft | 422 |

### Probes

`GET /health` returns `{"status": "ok"}`. `GET /metrics` returns Prometheus text (§13).

## 4. Agent boundary

The dividing question is unchanged: **does answering require reading this `user_id`'s account
data?** A second question now precedes it: **is this about the acquirer at all?**

| Message | Plan | Why |
| --- | --- | --- |
| "What's the difference between Get Clássica and Get Smart?" | knowledge | product facts, corpus |
| "My card machine won't connect" | knowledge | generic procedure from the corpus |
| "How does antecipação work?" | knowledge | product facts, corpus |
| "When will yesterday's sales be deposited?" | support | reads the account |
| "What's the weather in Porto Alegre tomorrow?" | knowledge | general question, web |
| "What's the euro exchange rate today?" | knowledge | general question, web |
| "My machine won't connect and yesterday's money didn't land" | knowledge, support | two intents, in the order asked |

Possessive phrasing does not decide anything. Only the data dependency does.

## 5. Routing, plans and control flow

1. The guardrail inspects the message before routing.
2. The router emits a validated `RouterDecision`; it never returns free text. The decision carries
   a plan of one or two steps, each naming a specialist and that step's standalone Portuguese
   query. The two-step cap lives in the schema, so the model cannot return a third.
3. Below the confidence threshold the request goes straight to escalation. The threshold is a
   conditional edge of the graph, not a branch inside a node.
4. Steps run sequentially in plan order. A single-step plan keeps v1's path and cost: no extra
   model call. A two-step plan ends in a compose step that merges both answers into one reply,
   keeps every source from both steps and adds no fact of its own.
5. A step that needs a human pauses the turn right there, under one protocol, with that step's
   reason; whatever earlier steps answered goes out now. When the operator answers, their text
   becomes that step's result and evidence, the plan's remaining steps run, and compose joins
   the operator's answer with them, without repeating the part already sent. A replan within
   the hop budget keeps the operator's answer.
6. A specialist that concludes a step is not its own may hand back to the router **once**, with a
   typed reason; the router then replans the whole turn. `hops` counts router passes across the
   whole plan. A second miss is recorded as that step's refusal, `repeated_misroute`.
7. LLM failure in the router produces a `RouterDecision` with `confidence = 0.0`, which the
   threshold edge already sends to escalation.
8. **Bounded replanning (T-52).** A specialist that answered may report a `Need`, a question
   only the other specialist can answer. Between steps the supervisor, which is code, revises the
   plan:
   - **add:** the need's step goes right after the current one, at most `MAX_PLAN_CHANGES` per
     turn, never repeating a planned step and never for a step that did not answer;
   - **drop:** an added step that does not answer from a read is dropped;
   - **stop:** before each step after the first, `MAX_MODEL_CALLS` and `MAX_COST_USD` are checked,
     and a spent turn sends its answered part and hands the router's remaining steps to a person
     as `budget_exhausted`.

   An added step only reads: no write reaches the merchant from it, a knowledge step added this
   way never searches the web, and a web answer never adds a step. Only code marks a step as
   added. `MAX_PLAN_CHANGES=0` runs the router's plan as it is.

## 6. Conversation memory

- A conversation is a LangGraph thread persisted by a checkpointer (SQLite by default, path from
  settings) on a volume, so conversations survive an API or container restart.
- The thread key is derived from the authenticated `user_id` **and** the `conversation_id`. The
  same `conversation_id` sent with another `user_id` is a different, empty thread. No endpoint
  loads a conversation by `conversation_id` alone.
- Across turns only two things persist: `messages`, the completed exchanges (merchant turn and
  reply), and `summary`, a rolling summary of exchanges that left the window. Every other state
  field belongs to one turn and is reset when the next one starts.
- Only turns that passed the guardrail enter the history, so a refused injection never becomes
  context for a later turn.
- History stores the masked merchant text. The checkpoint is a persistent store and gets the same
  treatment as a log.
- The router reads the summary plus a bounded window of recent messages, and resolves references
  ("and tomorrow's?") into a standalone query. Specialists never read history: they receive the
  standalone query, so they stay stateless.
- When the history outgrows its limit, the oldest exchanges are folded into the summary by one
  model call. If that call fails nothing is removed; the window still bounds what the router
  reads, and the fold is retried on the next turn.
- No node imports the checkpointer. It is attached when the graph is compiled.

## 7. Retrieval, web search and abstention

The corpus is 26 pt-BR pages plus 2 English pages from the acquirer's website and the PDF user
manuals of three terminals, extracted to a committed snapshot so the index builds without the raw
files. Retrieval runs on the step's Portuguese query.

Retrieval is hybrid: dense (Chroma) crosses an English question into the Portuguese corpus, BM25
pins exact product names such as "Get Smart" versus "Get Mini". Results are fused with
Reciprocal Rank Fusion. A comparison also runs one short search per item, so each product's own
page is retrieved. A reranker may then reorder each query's fused candidates (T-53). It is off by
default (ADR 0002); `RERANKER=cohere` turns on Cohere's, which gets the masked query and public
excerpts only. Without its key, or when it fails, the fused order stands; it never decides
abstention.

If the best dense similarity is below the retrieval threshold, calibrated per embedder, the
knowledge agent **generates nothing**. It states in pt-BR that the answer is not in its base and
the case goes to a human. The same happens when the model reports, in a structured field, that the
retrieved text does not answer the question: "not found" is never returned as an answer. When it
answers only part of the question, that part is answered and the rest is named as not covered.

Web search answers **general-purpose questions only**: those the router labels `general_info`,
such as weather and exchange rates. It is never a fallback for a product question the corpus
cannot answer, because a third-party page about acquirer rates is exactly the unsourced number
hard rule 1 forbids, and it is never used for account data.

- The query is PII-masked before it leaves the process, and carries no number or id the merchant
  did not type in the current message, so account data never reaches a web query.
- Page content is untrusted data: an instruction inside it is never followed. Every number in the
  answer must appear in the pages (hard rule 1 and 4), and the answer carries no link or e-mail of
  its own.
- Web answers cite the pages they used, with `source_type = web`, url and title, and receive
  today's date so "today" and "tomorrow" mean something. Results are capped and the call has a
  timeout.
- An answer that depends on the day prefers pages from the last day and closes with the source's
  publication date from the provider's structured field, without a time of day. A source older
  than the day in question gets a notice with its date. Without a structured date, a day the cited
  page names, on the day asked or between today and it, is shown as "Data informada pela própria
  fonte"; with none, the answer gets "Não consegui confirmar a data de publicação desta
  informação.", never a claim that the source has no date (T-51).
- Without an API key, on timeout, on a provider error, or when the answer fails those checks, the
  agent says in pt-BR that it cannot look that up right now. This is a degraded answer, not an
  escalation.

## 8. Support tools and authorization

Exactly two, both read-only:

- `get_settlement_schedule` — settlement entries for the caller between two dates, filtered by
  the sale date ("the money from yesterday's sales") or by the credit date ("what lands
  tomorrow"). Each entry carries both dates, and its status is derived from the credit date
  against today — `liquidado` before today, `previsto` from today on — so it can never contradict
  the date. A sale date after today is read as a credit date: no sale has happened yet.
- `get_transactions` — the caller's most recent transactions, optionally filtered by status.
- `open_technical_ticket` — the one write: proposed by the agent, run only on the merchant's
  confirmation, idempotent on the proposal id, for the caller's own terminal when the account
  shows it offline.

Both take `user_id`, the verified token's `sub`, from injected graph state. There is no identity parameter in either schema,
so a message naming another merchant's id cannot change what is queried. An unknown id yields an
empty result, which is answered as "não localizei seu cadastro" — not as an error.

## 9. Financial hard rules

These are testable statements, not prompt suggestions.

1. **No number without a literal source.** A rate, term, limit or amount may appear only if it is
   present verbatim in a retrieved chunk, a web snippet, an account record or an operator's
   answer. Deriving, rounding, averaging or completing from model memory is forbidden.
2. **No promise of a future date.** The system never states when money will be credited,
   released or advanced. An account forecast may be echoed, labelled as a forecast, with the
   reference date of the data. An operator may state a date; the system relays it as the
   operator's.
3. **No verdict on an individual case beyond the data.** A declined sale is answered with the
   reason the issuer recorded in the account data, and nothing more: no inferred cause, no advice
   the data does not carry. With no declined sale on record, or none with a recorded reason, it
   goes to a human. Chargebacks, refunds, blocks, fraud and pending registration go to a human
   even when account data exists.
4. **Rephrasing adds nothing.** A composed answer, a handoff draft or an operator reply that
   contains a number or a status absent from the text it was built from is discarded in favour of
   that text, delivered verbatim. Statuses are the account data's (aprovada, negada, estornada,
   cancelada, bloqueada, liquidado, previsto, a terminal's ativo or sem comunicação), read as
   whole words, with gender, number and synonyms folded together ("negada" and "recusada" are one
   status), so "estornada" reworded as "aprovada" is caught even with every number intact. Numbers are compared by value, not by spelling: currency symbols
   and spaces are dropped and both pt-BR (`1.234,56`) and en (`1,234.56`) separators are read,
   so "R$ 1.234,56" and "1234.56" are the same value. A token whose separator is ambiguous
   ("1.234") matches under either reading.

5. **A person is promised only with a protocol.** An answer that did not open a case never
   mentions an agent, a team or a human; such a sentence is dropped, and an answer made only of
   it escalates instead.

Amounts reach the model already written as the merchant reads them ("R$ 1.240,00"), and the mock
account records are read as of the turn's date, so "yesterday" and "tomorrow" always find data.

Feature-by-feature product comparison remains allowed: it is what the corpus documents.

## 10. Guardrail

Runs before routing, deterministic, no model call.

| Outcome | Behaviour |
| --- | --- |
| `prompt_injection` | An instruction to drop the rules, play another role or reveal the prompt gets a short refusal and a log event. |
| `identity_swap` | A request for another merchant's data, named as another merchant or by an id that is not the authenticated caller's, gets a short refusal and a log event. A merchant naming their own id is allowed. Defence in depth: injected state already neutralises it. |
| `allow` | Proceeds to the router. |

CPF, CNPJ, card number and phone are masked on every message before anything is logged or
stored; the message still proceeds. Masking never alters an id the system issued: trace ids,
case protocols and transaction ids reach logs and history as generated. v1's `out_of_domain` outcome is removed: weather and
exchange-rate questions now have an answer (§7).

## 11. Escalation and human review

**Escalate** stays deterministic and model-free: it emits a protocol, a typed reason and a masked
summary, and writes one structured log line. The typed reason doubles as the operational metric.

Reasons: `low_router_confidence`, `repeated_misroute`, `low_retrieval_confidence`,
`insufficient_sources`, `individual_case`, `tool_unavailable`, `model_unavailable`,
`budget_exhausted` (§5.8). A failed model
call (the router, a specialist's plan or answer) escalates as `model_unavailable`, with a notice
that says something failed, never as low confidence or as nothing found. A case stored as v2's
earlier `agent_not_implemented` reads as `model_unavailable`.

**human_review** takes every escalated turn:

1. It builds a case for the operator: the masked summary, the intent, what the agents tried and
   why they stopped, the pages retrieved if any, and a proposed draft reply. The draft may quote
   the case's evidence (the merchant's message, tool results, retrieved chunks); if it cannot be
   generated, or states a number or a status found in none of that evidence, the case opens
   without one.
   The case is recorded in the queue before the graph pauses.
2. It pauses the graph with `interrupt()`. The merchant receives a pt-BR notice with the protocol
   and `status = pending_human`.
3. While a case is open, `/chat` on that conversation returns `pending_human` with the protocol,
   records the message as a note on the case, and runs nothing. Other conversations are
   unaffected.
4. The operator lists open cases and answers one (§3): approving sends the draft as written;
   replying gives the agent the only new facts it may use. It may rephrase them, never add to
   them (hard rule 4). If the rephrasing fails, adds a number or changes a status, the
   operator's literal text is sent. Cancelling closes the case with a notice and no new fact.
5. The reply cites the operator as its source, is saved to the conversation history, and is held
   until the merchant's next message delivers it. The next turn builds on it. Open cases and
   paused threads survive a restart.
6. Every case expires after `HANDOFF_TTL_HOURS`. Past that, the merchant's next message closes it
   as expired, gets the notice delivered, and is answered as usual, so no conversation waits
   forever.

## 12. Language

Code, docstrings, logs, commits, README and specs are English. System prompts under
`app/prompts/` and every end-user answer are Brazilian Portuguese. No file mixes the two, which
is why user-facing message templates live under `app/prompts/` rather than inline in modules.

## 13. Operations

**Run from a clean clone.** `cp .env.example .env && docker compose up` serves the API. The image
needs no API key at build time. On first boot the container builds the index from the committed
snapshot into a named volume and skips that step when the index exists. Without the key the
default embedder needs, the first boot stops with a one-line fix and builds nothing. Conversation state lives
in a second named volume, so rebuilding the index never deletes a conversation. A `Makefile`
wraps up, test, eval and demo.

**Verify offline.** `make verify` from a clean clone needs only `uv` and `make`: a frozen sync, the
one step with network, then lint, the suite and the deterministic evals with no key, recorded in
`reports/verify.md`. Nothing downloads at runtime: the tokenizer ships in the repository and the
local embedding model is fetched only by the image build or `make models`. The suite runs with
sockets blocked.

**Scale out.** `STORAGE=postgres` moves checkpoints, the handoff queue and the per-conversation
lock into one Postgres every replica shares; the read-only index stays per node. A case escalated
on one replica is answered on another, a turn whose replica dies is finished from its last
checkpoint by the retry on another, and turns on one conversation never overlap across replicas.
`/ready` reports whether a replica can take traffic, and shutdown lets in-flight turns finish.
Every graph node runs under LangGraph's own timeout and retry, and a model that keeps failing
escalates as `model_unavailable` through the node's error handler.

**CI.** A GitHub Actions workflow runs the offline checks on ubuntu, with the network cut after
the sync, and on macOS; scans the history for secrets; builds the image, writes its SBOM and fails
on an unaccepted critical vulnerability; then starts the container with a fake LLM backend and
smoke-tests `/health`, `/metrics` and one `/chat`. Base images are pinned by digest and
Dependabot keeps lockfile, actions and digests current.

**Observability.** JSON logs, masked by a processor, whose `trace_id` is the request's
OpenTelemetry trace. Spans follow the GenAI conventions (`invoke_agent`, `chat`, `execute_tool`,
with provider, model and token counts, never content) and go over OTLP when an endpoint is set.
`/metrics` exposes turns by intent and outcome, model tokens and spend (so cost per resolution and
cache hit rate), escalations by reason, node latency and vendor failures after retries. Static
prompts end at an explicit cache breakpoint. The `observability` compose profile adds Prometheus
with four alert rules (p95, escalation spike, vendor failures, cost per resolution), a Grafana
dashboard and Jaeger. Each vendor failure (model error, model hang, web search down) has a test
that stubs it.
LangSmith tracing stays optional, off by default, and masked.

**Evals.** The routing golden set gains multi-turn, human-review and compound cases. An
LLM-as-judge run scores groundedness and answer relevance over the ten challenge scenarios, two
follow-ups, a compound request and the human-review loop, and its report is committed. Neither runs inside pytest.

## 14. Non-functional

- No answer reaches the user without a retrieved source, a tool result or an operator's answer.
  Handoff notices, refusals and degraded-dependency messages are templates, not answers.
- Sensitive data is masked before any log, trace, stored history or outbound web query.
- Tool errors become an answer or an escalation, never a stack trace.
- Every external dependency degrades with a message rather than an exception.
