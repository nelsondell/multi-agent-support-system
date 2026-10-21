# Architecture

The README has the diagram and the four agents. This page covers the rest: the full path of a
message, the code layout, how the model and the tools are used, memory, failure handling, and the
design questions behind them. The spec ([`specs/spec.md`](../specs/spec.md)) is the contract, and
the plan ([`specs/plan.md`](../specs/plan.md)) maps every invariant to the test that pins it.

## Message workflow

1. **`POST /chat`** takes `{message, conversation_id?}` and a merchant's bearer token; the
   merchant is the token's `sub` ([security.md](security.md)). The thread key is SHA-256 of
   `(merchant, conversation_id)`, so another merchant using the same conversation id never reaches
   this one. Messages on one conversation run one at a time: a
   second message, or an operator's answer to the conversation's case, waits for the turn in
   progress and then runs on its result. If a human has this conversation's case, the answer is the
   open protocol and nothing runs.
2. **Guardrail** masks CPF, CNPJ, card and phone numbers, and refuses prompt injection
   (`prompt_injection`) and requests for another merchant's data (`identity_swap`) before any model
   call. An id in the message is compared with the caller's, so a merchant naming their own id
   passes. A refused message is never remembered.
3. **Router** reads the message, and the last messages and a rolling summary as a separate JSON
   field, never as instructions. It returns a validated `RouterDecision`: intent, confidence, and
   one or two steps, each with a standalone Portuguese query that resolves references ("e o de
   amanhã?", "dessa venda") and, for a comparison, one search per product. A rewrite that invents
   a number or id absent from the conversation is discarded.
4. **Specialists** run step by step, each seeing only its own standalone query, never the history.
   Between steps the **supervisor** (`app/graph/supervisor.py`), which is code and not a model,
   may revise the plan. A step that answered can report a `Need`, a question only the other
   specialist can answer (a fee that depends on the merchant's terminal model; the procedure for
   the state the account shows). The step for it goes right after the current one, at most
   `MAX_PLAN_CHANGES` times a turn, and it only reads: no web, no write. An added step that does
   not answer is dropped. Before each step the turn's budgets are checked (`MAX_MODEL_CALLS`,
   `MAX_COST_USD`). A spent turn starts no more steps: its answered part goes out, and the rest
   goes to a person as `budget_exhausted`. [ADR 0001](adr/0001-bounded-replanning.md) says why
   this is bounded, not an open loop.
5. **Compose** copies a single answer as is, or merges two with one model call that may not add a
   number or change a status. A step that needs a person pauses the turn there, under one
   protocol, and any part already answered goes out now; once the operator answers, the remaining
   steps run and compose joins their answers with the operator's. A sentence promising a person is
   dropped unless a protocol was opened.
6. **Escalate → human review** when a step needs a person ([human-in-the-loop.md](human-in-the-loop.md)).
7. **Remember** stores the masked turn and the reply; the response is validated against its own
   contract before it leaves.

```json
{"conversation_id": "c1", "status": "answered", "answer": "...", "intent": "product_info",
 "agents": ["knowledge"], "confidence": 0.98,
 "sources": [{"source_type": "kb", "url": "https://site.getnet.com.br/crediario/", "title": "Crediário - Getnet"}],
 "escalated": false, "escalation": null, "delivered": []}
```

`status` is `answered` or `pending_human`. Sources are typed: `kb` and `web` carry a url, `account`
names the tool that read the merchant's data, `operator` names the protocol a human answered.
`delivered` carries an operator's answer held since the merchant's last message.

## Code layout

```
app/
  main.py, operator.py      FastAPI: /chat, /conversations, /health, /metrics; /handoffs for operators
  storage.py                SQLite on one node or Postgres for replicas: checkpoints, queue, locks
  locks.py                  one turn at a time per conversation, in-process or advisory in Postgres
  handoffs.py               the queue of cases waiting for a human, one SQL for both databases
  graph/                    one module per node, plus build.py (edges), state.py, memory.py,
                            numbers.py and statuses.py (the hard-rule-4 guards)
  rag/                      parse, chunk, ingest, hybrid retrieval
  tools/                    account.py (two support tools), web.py (Tavily)
  providers/                llm.py (the only vendor import), embeddings.py, fake.py (CI)
  prompts/                  every prompt and pt-BR template, versioned markdown
  observability/            masking, structured logs, metrics, tracing
evals/                      golden set, model benchmark, retrieval calibration, judge, reports
specs/                      spec, plan and task list (spec-driven development)
```

Policy is readable in two files. In `app/graph/build.py`, the confidence threshold is a
conditional edge, not an `if` inside a node, and the hop budget spans the whole plan, so two
agents cannot pass a message back and forth. `app/graph/supervisor.py` holds every rule by which a
plan may change, and the budgets that stop it.

## Tools and how the model is used

- **Three support tools**, `get_settlement_schedule`, `get_transactions` and
  `get_account_profile` (plan, terminal model and whether it is communicating), are LangChain tools
  whose `user_id` is injected from graph state with `InjectedState`. The model never sees it:

  ```
  get_settlement_schedule    model sees: ['date_from', 'date_to', 'date_field']
  get_transactions           model sees: ['limit', 'status']
  get_account_profile        model sees: []
  ```

  `call_tool` also drops any argument the model is not allowed to set, so a smuggled `user_id` never
  reaches the lookup. The support agent plans in one structured call, the code runs exactly one
  tool, and a second call writes the answer from its result. Its one write, a technical ticket,
  waits for the merchant's confirmation in `confirm_action` ([tools.md](tools.md)).
- **The account data** is mock, written as of one day and read as of the turn's, so "ontem" and
  "amanhã" always land on data. Settlements are filtered by sale date ("the money from yesterday's
  sales") or credit date ("what lands tomorrow"); their status comes from the credit date against
  today, so an answer can't call settled money a forecast. Amounts reach the model as
  "R$ 1.240,00". A declined sale is answered with the reason the issuer recorded.
- **Structured output everywhere.** Every model call returns a validated Pydantic object; nothing
  parses model prose into a decision.
- **One provider seam.** `app/providers/llm.py` is the only module that imports a vendor SDK. Every
  provider error becomes an answer or an escalation, never a stack trace.
- **Prompts are versioned files** in `app/prompts/`, pt-BR, loaded by name; none lives in Python.

## Memory

A conversation is a LangGraph thread persisted by `AsyncSqliteSaver` in the `state` volume. Only
two things outlive a turn: `messages` (the masked merchant turns and the replies) and `summary` (a
rolling summary of what left the window, folded by one model call; if it fails, nothing is
dropped). Every other field resets when a turn starts. The router is the only agent that reads
history, and it reads it as data; specialists get a standalone query and stay stateless. No node
imports the checkpointer: it is attached when the graph is compiled, and a test enforces that.

```bash
MERCHANT="Authorization: Bearer $(uv run python scripts/dev_token.py merchant cliente1988)"
curl -s localhost:8000/chat -H "$MERCHANT" -H 'Content-Type: application/json' \
  -d '{"message": "Quando cai o dinheiro das vendas de ontem?", "conversation_id": "c1"}'
curl -s localhost:8000/chat -H "$MERCHANT" -H 'Content-Type: application/json' \
  -d '{"message": "E o de amanhã?", "conversation_id": "c1"}'
curl -s localhost:8000/conversations/c1 -H "$MERCHANT"
```

The second turn reaches support as "Quando cai o dinheiro das vendas de amanhã?". Conversations
survive a restart; another merchant asking for `c1` gets a 404.

## Failure handling

Every dependency has a defined degraded path, and none raises to the merchant:

| Failure | Behaviour |
| --- | --- |
| Chat model | Each node attempt is capped by LangGraph's timeout and retried once. Router, knowledge or support, still failing: their error handler escalates as `model_unavailable`, with a notice that something failed. Compose: the step answers are joined as written. A rephrasing or a draft: the operator's text, or no draft. |
| Embedding API | Nothing retrieved → the step abstains and escalates. |
| Web search | A pt-BR "cannot look this up now"; no escalation. |
| Account store | `tool_unavailable` escalation. |
| API restart | Conversations, open cases and paused threads are in SQLite on a volume, or in Postgres. |
| A replica dies mid-turn | Its lock goes with its connection; the retry on another replica finishes the turn from its last checkpoint. |
| Two requests on one conversation | The second waits for the first; neither overwrites the other's turn. |
| Operator resume fails midway | The case is claimed atomically and reopened on failure, so a second attempt works and two cannot both resume. |
| First boot without a key | One-line fix, nothing written. |
| Tracing | Errors stay in LangSmith's background thread. |

## Design questions

**How do you add multi-turn memory without coupling agents to the database?** The checkpointer is
opened in the API lifespan and handed to `compile()`; no node imports it, and a test fails if one
does. Only `messages` and `summary` persist, and only the router reads them, as data; specialists
get a standalone query and stay stateless, which is also what keeps them testable without a
conversation. Persistence opened one risk worth naming: LangGraph's msgpack decoder imports any type
a checkpoint names, so a writable state store becomes code execution. `app/checkpoints.py`
restricts it to an allowlist of the state's own types (anything else comes back as plain data), and
a test keeps the allowlist in sync with `GraphState`.

**What makes it really multi-agent?** Each agent owns a decision, the evidence behind it and its
own ways of stopping, and they hand work to each other: the router plans one or two steps; a
specialist that finds a step isn't its own hands it back once and the router replans; a specialist
that finds it needs the other's data asks for it, and the supervisor adds that step within the
turn's budgets; compose joins the answers without adding facts; and when one step needs a person, the turn pauses there, and
after the operator answers, the remaining steps run with that answer in hand. It is not a router
dispatching to one specialist and ending.

**Why serialize per conversation instead of per process?** Two turns on one thread read the same
checkpoint, and the later write drops the earlier turn; two conversations share nothing. So each
conversation gets an asyncio lock, held from reading the thread to writing the answer, and the
operator's resume takes the same lock. It lives in the process, which is enough for one worker over
SQLite; several workers would need the lock in a shared store.

**Prompt injection, tested end to end.** The guardrail refuses it before any model call; identity
can't be argued with because no tool exposes it; and the suite proves it three ways: an injection
planted in turn 1 has no effect on turn 2, a web page that gives orders isn't obeyed, and another
merchant's data is unreachable through the graph.
