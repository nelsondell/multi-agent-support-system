# Multi-agent support system

[![CI](https://github.com/nelsondell/multi-agent-support-system/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/nelsondell/multi-agent-support-system/actions/workflows/ci.yml)

A support service for a payments acquirer. One HTTP endpoint takes a merchant message and returns
an answer in Brazilian Portuguese that is grounded in a retrieved source or in the merchant's own
account data — or it hands the case to a human. Three agent roles cooperate behind a LangGraph
orchestration: a **router** that decides, a **knowledge** agent that answers from the indexed
website corpus, and a **support** agent that reads the caller's account through two tools.

The governing rule is that no answer leaves the system without evidence. When retrieval is weak,
when the request is an individual financial case, or when routing fails twice, the system
escalates instead of guessing.

## 1. Build, configure, run, test

Requirements: Python 3.12 and [uv](https://docs.astral.sh/uv/). Docker is optional.

```bash
uv sync                                   # install dependencies
uv run python -m app.rag.ingest           # build the knowledge index (data/raw/ or the snapshot)
uv run uvicorn app.main:app --reload      # serve on http://localhost:8000
uv run pytest -q                          # offline test suite, no vendor calls, no cost
uv run ruff check . && uv run ruff format --check .
```

The `Makefile` wraps the usual commands: `make up`, `make test`, `make eval`, `make demo`.

### Configuration

Settings are read from the environment or `.env` (`app/settings.py`). `.env.example` documents
every one of them; `cp .env.example .env` and an `OPENAI_API_KEY` are the whole setup.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_BACKEND` | `openai` | `fake` answers every structured call deterministically with no key. CI only. |
| `OPENAI_API_KEY` | unset | Chat model credential. With the `openai` backend the API fails fast at start-up without it. |
| `LLM_MODEL` | `gpt-4o-mini` | Chat model. |
| `LLM_TEMPERATURE` | `0.0` | Sampling temperature. |
| `EMBEDDING_BACKEND` | `auto` | `auto` follows the key, `local` uses Chroma's bundled model, `openai` requires the key. |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Used when the backend is `openai`. |
| `ROUTER_CONFIDENCE_THRESHOLD` | `0.6` | Below this, the request escalates without reaching a specialist. |
| `RETRIEVAL_CONFIDENCE_THRESHOLD` | `0.35` | Below this, the knowledge agent abstains. Not calibrated — see §9. |
| `MAX_HOPS` | `2` | Router passes allowed per request; bounds the single hand-back. |
| `INDEX_PATH` | `data/processed/chroma` | Persisted Chroma directory. |
| `COLLECTION_NAME` | `getnet_kb` | Chroma collection. |
| `RETRIEVAL_TOP_K` | `5` | Chunks passed to generation. |
| `STATE_DIR` | `data/state` | Conversation checkpoints (SQLite); the `state` named volume in Docker. |
| `HISTORY_WINDOW` | `6` | Most recent messages kept verbatim. |
| `HISTORY_MAX_MESSAGES` | `12` | Past this length, older messages are folded into a rolling summary. |
| `OPERATOR_TOKEN` | unset | Bearer token for `/handoffs`. Unset, those endpoints answer 503. |

The embedder is deliberately configurable apart from the chat key: it is part of the index's
identity, and adding a key must not silently change it under an index built without one.

### Docker

From a fresh clone, nothing has to be built on the host:

```bash
cp .env.example .env                      # then set OPENAI_API_KEY
docker compose up --build                 # or `make up`: detached, returns once healthy
curl -s localhost:8000/health             # {"status":"ok"}
```

The image needs no API key to build. On first boot the container builds the knowledge index from
the committed snapshot into the named volume `index`, then starts the API; every later boot finds
the index and goes straight to the API. The chunker's tokenizer and the local embedding model are
fetched at build time, so that first build downloads nothing. The compose healthcheck polls
`/health` with a start period that covers the first build. The image runs as a non-root user.
Conversation checkpoints live in a second named volume, `state`, so a restart keeps every
conversation and rebuilding the index never deletes one.

`API_PORT` in `.env` changes the host port when 8000 is taken. Changing `EMBEDDING_BACKEND`
changes the index's embedder, so it needs a rebuild: `docker compose down`, then remove only the
index volume (`docker volume ls` lists it as `<project>_index`); the next boot rebuilds it.
`docker compose down -v` would drop both volumes, conversations included.

Verified on Docker 29.5 (Colima, linux/arm64): the image builds with no key; from a clean state
the first boot indexes 148 chunks from the snapshot and turns healthy in about 10 seconds; a
second boot logs `index already present at data/processed/chroma; build skipped`.

### Demo

With the service running and the index built:

```bash
./scripts/demo.sh                         # five requests, one per path through the graph
```

## 2. Orchestration architecture

```
POST /chat
    |
    v
+-----------+   blocked (out of scope / identity)  -> answer, END
| guardrail |-------------------------------------------------+
+-----------+                                                 |
    | allow                                                    |
    v                                                          |
+-----------+   confidence < ROUTER_CONFIDENCE_THRESHOLD       |
|  router   |-----------------------------------+              |
+-----------+                                   |              |
    |  target_agent                             |              |
    +-------------------+                       |              |
    v                   v                       v              |
+-----------+     +-----------+           +-----------+        |
| knowledge |     |  support  |           | escalate  |        |
+-----------+     +-----------+           +-----------+        |
    |                   |                       ^              |
    | answer -> END     | answer -> END          |              |
    | refusal ----------+-----------------------+              |
    | handoff, hops < MAX_HOPS -> back to router                |
    | handoff, budget spent ------------------- >               |
                                                               v
                                                           response
```

Three decisions are worth naming:

- **The confidence threshold is a conditional edge, not an `if` inside a node.** Routing policy is
  readable in one place (`app/graph/build.py`), and a low-confidence request never touches a
  specialist.
- **A failed model call is not a parallel error path.** The router turns a provider error into a
  `RouterDecision` with `confidence = 0.0`, which the threshold edge already handles.
- **Hand-back is bounded by `hops`.** A specialist that believes the message is not its own returns
  it to the router once; a second miss escalates with `repeated_misroute`. Two agents cannot pass
  the same message back and forth.

### The agent boundary

The dividing question is *does answering require reading this `user_id`'s account data?*
Possessive phrasing decides nothing.

| Message | Agent |
| --- | --- |
| "What's the difference between Get Clássica and Get Smart?" | knowledge |
| "My card machine won't connect" | knowledge (published procedure) |
| "When will yesterday's sales be deposited?" | support (reads the account) |
| "Why was my sale declined?" | support, which escalates it |

## 3. Message flow, from POST to response

1. `POST /chat` validates `{message, user_id, conversation_id?}`. `user_id` comes from the
   request envelope and is treated as already authenticated. Without a `conversation_id` the
   server starts a new conversation and mints one.
2. A `trace_id` is minted and bound to the logging context for the whole request. The thread key
   is the SHA-256 of the `(user_id, conversation_id)` pair, so the same conversation id sent with
   another `user_id` opens a different, empty thread. The turn input resets every per-turn field.
3. **Guardrail** masks PII into `masked_message`, then classifies. `out_of_domain` and
   `identity_swap` are answered on the spot and never routed.
4. **Router** reads the message and, as data in a separate JSON field, the rolling summary and
   the last `HISTORY_WINDOW` messages. It emits a validated `RouterDecision` — `intent`,
   `target_agent`, `confidence`, `rationale`, `lang`, `query_pt` — and increments `hops`.
   `query_pt` is the message as a standalone Portuguese question, with references to earlier
   turns resolved ("e o de amanhã?" becomes a question about tomorrow's settlement). A rewrite
   that carries an identifier absent from the conversation (a number, date, protocol or
   merchant id) is discarded in favour of the original message. Both versions are logged, masked.
5. The **conditional edge** sends the request to `knowledge`, `support`, or straight to `escalate`.
6. **Knowledge** retrieves on `query_pt`, and either answers citing the retrieved URLs or abstains
   with `low_retrieval_confidence`. **Support** plans an action from `query_pt` and the turn's
   date (Brazil time, so "ontem" and "amanhã" become date ranges), calls at most one tool with
   the injected identity, and answers from the result — or escalates an individual case.
   Neither specialist ever sees the history.
7. **Escalate** is deterministic and model-free: it mints a protocol, attaches a typed reason and a
   masked summary, and composes the handoff notice. **Human review** takes over from there (see
   below): the turn pauses until an operator answers, then continues with the operator's reply.
8. **Remember** appends the masked merchant turn and the reply to the thread's `messages`. A
   message the guardrail refused never gets here. Once the history passes
   `HISTORY_MAX_MESSAGES`, everything before the last `HISTORY_WINDOW` messages is folded into a
   rolling `summary` by one structured call; if that call fails, nothing is dropped.
9. The terminal state is projected onto `ChatResponse`, which validates its own consistency: the
   `escalated` flag must match the payload, an escalated response cites no sources, a
   non-escalated one must carry an answer, and a router-level escalation may not name a specialist.

Response shape:

```json
{
  "conversation_id": "9f2c4e1ab3d84f0e8a7c6b5d4e3f2a1b",
  "status": "answered",
  "answer": "...",
  "intent": "product_info",
  "target_agent": "knowledge",
  "confidence": 0.91,
  "sources": [{"url": "https://site.getnet.com.br/crediario/", "title": "..."}],
  "escalated": false,
  "escalation": null
}
```

**Memory without coupling.** The checkpointer (`AsyncSqliteSaver`) is opened in the API lifespan
and attached when the graph is compiled; no node imports it, and a test enforces that. Only
`messages` and `summary` outlive a turn. The history stores the masked text, because a
checkpoint is a persistent store and gets the same treatment as a log. Its decoder is restricted
(`app/checkpoints.py`): LangGraph would otherwise import any type a checkpoint names, so only its
safe built-ins and the application types the state holds are rebuilt; anything else comes back
as plain data.

### Human review: agent → human → agent

Every escalated turn goes to a fourth agent, `human_review`, which pauses the graph with
LangGraph's `interrupt()` and continues it when an operator answers. It is two nodes because
`interrupt()` re-runs its whole node on resume:

- `prepare_handoff` does every side effect, once: it builds the case (masked summary, intent,
  what the agents tried and why they stopped, the pages retrieved, a proposed draft from one
  model call) and records it in the handoff queue, a SQLite table next to the checkpoints.
- `await_operator` only reads the state and pauses. On resume it turns the operator's input into
  the merchant's reply: an approved draft goes out as written; a reply is rephrased, and if the
  rephrasing carries a number the operator did not write (compared by value, so "R$ 1.234,56"
  and "1234.56" match), the operator's literal text goes out instead. The reply cites the
  operator, lands in the history, and the next turn builds on it.

While a case is open, `/chat` on that conversation answers `status: "pending_human"` with the
protocol and starts no run; other conversations are unaffected. Cases and paused threads live on
the `state` volume, so both survive a restart.

```bash
# 1. an individual case pauses
curl -s localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message": "Fui cobrado duas vezes na mesma venda", "user_id": "cliente1988", "conversation_id": "c1"}'
# 2. the operator lists open cases, or reads one
curl -s localhost:8000/handoffs -H "Authorization: Bearer $OPERATOR_TOKEN"
curl -s localhost:8000/handoffs/ESC-XXXXXXXXXX -H "Authorization: Bearer $OPERATOR_TOKEN"
# 3. and answers it: approve the draft, or reply in their own words
curl -s -X POST localhost:8000/handoffs/ESC-XXXXXXXXXX/resume \
  -H "Authorization: Bearer $OPERATOR_TOKEN" -H 'Content-Type: application/json' \
  -d '{"action": "reply", "answer": "Estorno aprovado, cai em 2 dias úteis."}'
# 4. the next turn knows what the operator said
curl -s localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message": "E quando cai mesmo?", "user_id": "cliente1988", "conversation_id": "c1"}'
```

Operator errors: 401 without a valid token, 503 when no token is configured, 404 for an unknown
protocol, 409 for a case already closed, 422 when approving a case that has no draft.

## 4. RAG pipeline

Ingestion runs on the host (`uv run python -m app.rag.ingest`); the service only reads.

**Source** — when `data/raw/` and its fetch manifest are both present, ingestion re-extracts the
pages and refreshes the committed snapshot `data/corpus/documents.jsonl`: extracted text, one
document per line, never raw HTML. Otherwise it reads the snapshot, so a fresh clone builds the
same index, with the same chunk ids, without the 16 MB of fetched pages. An extraction that
yields nothing raises instead of overwriting the snapshot.

**Parse** (`app/rag/parse.py`) — two routes, each confirmed against a real file in `data/raw/`
before the parser was written:

- *FAQ* (`/duvidas/`): questions are `a.c-all-faq__question`, answers are separate
  `div.o-modal__content--faq` nodes, paired by their shared `data-modal` id. The answer node
  repeats the question as its first line, which is stripped.
- *Article* (Beaver Builder): body text under `.fl-col-content`. Generic extraction returns mostly
  footer on these pages, which is why the selector is explicit. Meta descriptions are ignored
  everywhere: they repeat verbatim across pages.

**Chunk** (`app/rag/chunk.py`) — 600-token windows with 100 tokens of overlap, measured with
`tiktoken` (`cl100k_base`). Each chunk keeps `url`, `title`, `section` and `route` so it can be
cited.

**Dedup** — by SHA-256 of the normalized chunk text. This is not hygiene. Of the 11 duplicate
chunks in the last run, 4 are shared between `/` and `/contato/`, 3 between
`/taxas-personalizadas/` and `/todas-as-maquininhas/` — different routes rendering the same body —
and 4 are answers repeated across categories inside `/duvidas/`. Without dedup the index would
rank the same text twice.

**Index** — Chroma persisted to `data/processed/chroma` with cosine space, plus a `chunks.jsonl`
corpus that BM25 is rebuilt from at query time. Chunk ids are content hashes and Chroma upserts
them, so re-running is idempotent.

**Retrieve** (`app/rag/retrieve.py`) — hybrid, because the two retrievers fail differently. Dense
vectors carry a question into Portuguese prose; BM25 pins exact product names, where embeddings
blur near-identical marketing copy. Both lists are fused with hand-written Reciprocal Rank Fusion
at `k = 60`; only positions enter the fusion, never raw BM25 or cosine values, which live on
incomparable scales.

Abstention keys off **dense similarity, not the RRF score**. RRF is a ranking signal: a document
that tops both lists scores the same whether the corpus answers the question or not. The absolute
similarity is what can say "this is not in the base".

**Generate** (`app/graph/knowledge.py`) — the prompt receives only the surviving chunks, each
labelled with its URL, plus the financial hard rules. Sources are taken from what was retrieved,
never from URLs the model claims to have used.

### Corpus and fixture

| | Corpus | Fixture |
| --- | --- | --- |
| Path | `data/raw/`, `data/processed/` (gitignored); `data/corpus/documents.jsonl` (committed) | `tests/fixtures/kb_sample.jsonl` (committed) |
| Size | 18 pages fetched, 133 documents, 148 chunks | 32 chunks |
| Used by | the running service | the offline test suite |

The fixture is selected by BM25 against the ten challenge scenarios, four chunks each, including
the out-of-scope ones on purpose so a test can prove the abstain path on a corpus that genuinely
does not answer them. Tests never load the full corpus. Ingestion rewrites it only when asked,
with `--refresh-fixture`, so building an index never changes the test data by accident.

Ingestion counters from the last run (`uv run python -m app.rag.ingest --refresh-fixture`):

```
documents extracted from data/raw, snapshot refreshed at data/corpus/documents.jsonl
documents loaded: 133
  skipped: site-getnet-com-br-get-ajuda-get-conta.html (no route matched)
  skipped: site-getnet-com-br-get-ajuda.html (no route matched)
  skipped: www-getnet-net-en-our-solutions-agentic-commerce.html (no route matched)
  skipped: www-getnet-net-en.html (no route matched)
chunks generated=159 discarded=11 (duplicates) total=148
indexed into data/processed/chroma (collection getnet_kb)
fixture chunks written to tests/fixtures/kb_sample.jsonl: 32
```

Four of eighteen pages match neither route: the two `get-ajuda` pages render their content with
JavaScript, and the two English pages come from a different stack with no `.fl-col-content`. They
are reported, not silently dropped.

## 5. How the LLM is used

**Structured output everywhere.** Every model call returns a validated Pydantic object:
`RouterDecision`, `KnowledgeAnswer`, `SupportPlan`, `SupportAnswer`. Nothing in the system parses
model prose into a decision, so a malformed generation is a validation error rather than a
mis-route.

**Tool calling with injected identity.** The two support tools are LangChain tools whose
`user_id` parameter is annotated with LangGraph's `InjectedState`. It therefore appears in
`args_schema` but not in `tool_call_schema` — the schema the model sees:

```
get_settlement_schedule    model sees: ['date_from', 'date_to']
get_transactions           model sees: ['limit', 'status']
```

There is no argument a prompt could set to reach another merchant. `call_tool` additionally
filters the model's arguments down to the allowed fields before injecting the caller's id, so a
smuggled `user_id` is dropped rather than trusted. The support agent plans in one structured call,
the code executes exactly one tool, and a second structured call writes the answer — which keeps
tool orchestration out of vendor-specific code.

**One provider seam.** `app/providers/llm.py` defines an `LLMProvider` protocol with a single
`structured()` method; `app/providers/embeddings.py` does the same for embeddings. No other module
imports a vendor SDK, so moving to Azure AI Foundry is a rewrite of one file. Provider errors are
wrapped in `LLMProviderError` and become an answer or an escalation, never a stack trace.

**Prompts are versioned files.** Every prompt lives in `app/prompts/*.md` with a `version` in front
matter and is loaded by id. No prompt literal appears in Python — which also keeps each file
single-language, since prompts and user-facing messages are Portuguese while the code is English.

## 6. Tests and evaluation

Two levels, deliberately separate.

**Offline suite** — provider mocked, no network, no cost, always runs:

```
$ uv run pytest -q
99 passed in 1.31s
```

One module per graph node (`test_router`, `test_knowledge`, `test_support`, `test_guardrail`,
`test_escalation`), the two tools (`test_tools`), masking and the log pipeline
(`test_observability`), retrieval and fusion (`test_retrieve`), three adversarial paths
(`test_adversarial`: prompt injection, another customer's data, out of scope) and HTTP integration
(`test_api`, which injects a scripted graph instead of running the lifespan).

**CI** — `.github/workflows/ci.yml` runs on every push and pull request: ruff and the offline
suite, then `docker compose build`, the container started from `.env.example` with
`LLM_BACKEND=fake`, and a smoke test of `/health` and one `/chat` that must come back answered,
with sources and without escalation. The fake backend (`app/providers/fake.py`) derives a valid
instance of any schema from its declaration, so the smoke test proves the image, the index built
on first boot and the graph work together without a vendor account. It asserts the response
contract, never the content.

**Routing golden set** — 15 labelled rows (the 10 challenge scenarios, 3 ambiguous, 2 out of
scope). It is a measurement, not a test: a score that dips one row in fifteen is a signal to read,
not a red build, so it never runs inside pytest.

```
$ uv run python -m evals.run_routing_eval
guardrail (deterministic)
  overall: 86.7%  (13/15)
  by expected class:
    allow            100.0%  (11/11)
    out_of_domain     50.0%  (2/4)
  by group:
    ambiguous        100.0%  (3/3)
    challenge        100.0%  (10/10)
    out_of_scope       0.0%  (0/2)
  misses:
    O01  expected=out_of_domain  got=allow
    O02  expected=out_of_domain  got=allow

router: skipped, OPENAI_API_KEY is not set (this stage calls a real model)
```

The guardrail catches every out-of-scope case the challenge anticipates and none of the two
open-ended ones written to probe it. That is the honest limit of a pattern-based scope check.

**Router accuracy against a real model: [preencher].** The stage requires an API key and was never
run. Per-class accuracy, confusion between `knowledge` and `support`, and behaviour on the three
ambiguous rows are all unmeasured.

## 7. Observability and guardrails

**Structured logs.** `structlog` renders JSON to stdout. `mask_pii` is installed as a processor,
not called at each site, so no node can log an unmasked value even by accident. `instrument_node`
wraps every graph node, so timing and outcome fields are uniform and an agent that forgets to log
cannot exist.

```json
{"agent": "support", "latency_ms": 1.5, "trace_id": "591f8d5b7aa5475eb1d31e4a9056fa7b",
 "intent": "account_data", "target_agent": "support", "router_confidence": 0.93,
 "guardrail": null, "refusal_reason": null, "handoff_reason": null, "escalated": false,
 "event": "node.completed", "level": "info", "timestamp": "2026-09-01T18:22:47.886994Z",
 "user_id": "u_a9f6bd311e2b"}
```

The merchant id is pseudonymised with a stable SHA-256 prefix, so traces correlate without
carrying the identifier. `escalation.reason` is a typed enum and doubles as the operational metric:
`low_router_confidence`, `repeated_misroute`, `low_retrieval_confidence`, `individual_case`,
`agent_not_implemented`.

Production latency and escalation rates: **[preencher]** — the figures above come from runs with a
stubbed model and say nothing about real inference time.

**Guardrail.** Deterministic patterns, before any model call: prompt injection and identity-swap
attempts get a short refusal, out-of-scope questions get a scope reply, and PII is masked. It is
defence in depth for identity, since injected state already blocks the data path, and it is the
only thing that can answer an out-of-scope question — there is no web search to fall back on.

**Financial hard rules**, carried in the prompts and asserted in tests:

1. No rate, term, limit or amount unless it is literally present in a retrieved chunk or an account
   record. No deriving, rounding or averaging.
2. No promise of a future date. An account forecast is echoed as a forecast, with the reference
   date of the data.
3. No verdict on an individual case — declined transactions, chargebacks, blocks, fraud, pending
   registration — even when the data exists. Those escalate.

## 8. Process

**Spec-driven, with Claude Code.** The specification was written and approved before any
application code: `specs/spec.md` (what the system does), `specs/plan.md` (how it is built) and
`specs/tasks.md` (14 tasks, one commit each, every task with a done criterion). The architecture
was settled first in a structured interview covering the agent boundary, mis-routing recovery,
low-confidence retrieval, per-user authorization and what a financial-domain system must never
assert. `CLAUDE.md` carries the non-negotiables the assistant had to honour on every task.

Every commit follows `<type>(<T-id>): <imperative>`, one per task, from `docs(T-00)` through
`build(T-14)`. The history is the task list.

**Preparation, before the core window.** The corpus was fetched in advance (`data/raw/`, with
`docs/_local/corpus-manifest.tsv` and `corpus-urls.txt` recording what was fetched from where), an
earlier exploration session produced the parsing hypotheses in `docs/_local/site-findings.md`, and
the repository was scaffolded with uv. The hypotheses were treated as hypotheses: each selector was
confirmed against a real file before its parser was written, and one of them — "article pages are
Beaver Builder" — turned out to describe every Portuguese page, not just articles.

**Actual development time: [preencher].**

**Out of scope, deliberately.** Web search, OpenTelemetry and MCP were excluded when the scope was
closed, and were never re-proposed. Web search would have made the out-of-scope scenarios
answerable, but abstaining is the correct behaviour for a system whose only warrant is its corpus.
OpenTelemetry would duplicate what structured logs already carry at this size. A fourth agent and a
human-handoff queue were left out; escalation emits a protocol and a typed reason, which is the
part a queue would consume.

## 9. Limitations and next steps

**The retrieval threshold is not calibrated.** Measured on the local fallback embedder, in-domain
queries score 0.59–0.70 and out-of-domain ones 0.50–0.55 — a separation of about 0.05. The default
of 0.35 therefore catches almost nothing, and no threshold on this signal can reject out-of-scope
questions; the guardrail has to. With `text-embedding-3-small` the numbers change entirely and must
be re-measured: **[preencher]**.

**`query_pt` is load-bearing.** A raw English question scores 0.375 against this corpus, below an
off-topic Portuguese one. Retrieval works because the router rewrites the query into Portuguese
first. Without that step the English path degrades badly.

**Article chunking cuts tables.** A 600-token window splits the fee tables on the offers page
mid-row, so a percentage can survive while the plan and condition that qualify it do not — which is
in direct tension with hard rule 1. Structural chunking (by heading, block or table row) for the
article route is the highest-value fix, and it costs little because the 120 FAQ pairs never reach
the window.

**The scope check is a denylist.** It scores 0/2 on open-ended out-of-scope questions. A small
classifier, or an in-domain allowlist checked against the corpus, would generalise where patterns
cannot.

**The test fixture is generated by the pipeline it tests.** If extraction is wrong, the fixture is
wrong in the same way and the suite still passes. Hand-curating the sampled chunks once would break
that circularity.

**Router confidence is self-reported.** The escalation gate keys off a number the model produces
about itself, which is a weak signal. The golden set exists to calibrate or discredit it and has
not yet been run against a real model.

**Not exercised:** the router half of the golden set never ran, and no request has ever been served by a real model. Everything reported here that involves the
model came from scripted providers.

**Next, in order:** run the golden set against a real model and calibrate both thresholds; switch
the article route to structural chunking; expose terminal status,
which exists in `data/customers.json` but no tool reads; then revisit the scope check.
