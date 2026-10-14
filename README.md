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
| `LLM_MODEL` | `gpt-6-luna` | Chat model, chosen in [`evals/router_benchmark.md`](evals/router_benchmark.md). |
| `LLM_REASONING_EFFORT` | `low` | For reasoning models. Left unset when a temperature is set. |
| `LLM_TEMPERATURE` | unset | For models without reasoning only, e.g. `gpt-4o-mini` with `0`. Setting both fails at start-up. |
| `JUDGE_MODEL` | `gpt-6-sol` | Scores the LLM-as-judge eval only; never answers a merchant. |
| `EMBEDDING_BACKEND` | `auto` | `auto` follows the key, `local` uses Chroma's bundled model, `openai` requires the key. `.env.example` pins `openai`; CI uses `local`. |
| `EMBEDDING_MODEL` | `text-embedding-3-large` | Used when the backend is `openai`. Chosen in [`evals/retrieval_calibration.md`](evals/retrieval_calibration.md). |
| `ROUTER_CONFIDENCE_THRESHOLD` | `0.6` | Below this, the request escalates without reaching a specialist. |
| `RETRIEVAL_CONFIDENCE_THRESHOLD` | `0.52` | Below this, the knowledge agent abstains without a model call. Calibrated for `text-embedding-3-large`. |
| `MAX_HOPS` | `2` | Router passes allowed per request; bounds the single hand-back. |
| `INDEX_PATH` | `data/processed/chroma` | Persisted Chroma directory. |
| `COLLECTION_NAME` | `getnet_kb` | Chroma collection. |
| `RETRIEVAL_TOP_K` | `5` | Chunks passed to generation. |
| `STATE_DIR` | `data/state` | Conversation checkpoints (SQLite); the `state` named volume in Docker. |
| `HISTORY_WINDOW` | `6` | Most recent messages kept verbatim. |
| `HISTORY_MAX_MESSAGES` | `12` | Past this length, older messages are folded into a rolling summary. |
| `LANGSMITH_TRACING` | `false` | Masked LangSmith tracing. On without `LANGSMITH_API_KEY`, it stays off. |
| `LANGSMITH_API_KEY` | unset | LangSmith credential. |
| `LANGSMITH_PROJECT` | `getnet-support` | LangSmith project the traces go to. |
| `OPERATOR_TOKEN` | unset | Bearer token for `/handoffs`. Unset, those endpoints answer 503. |
| `TAVILY_API_KEY` | unset | Web search for general questions. Unset, they get a "cannot look this up now" message. |
| `WEB_SEARCH_MAX_RESULTS` | `3` | Pages per web search. |
| `WEB_SEARCH_TIMEOUT_S` | `8.0` | Seconds before a web search counts as unavailable. |

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

The default embedder is OpenAI's, so the first boot needs `OPENAI_API_KEY`. Without it the index
build stops before reading or writing anything, with the fix in one line
(`docker compose logs api`): set the key, or set `EMBEDDING_BACKEND=local`. A key the API rejects
stops it the same way, after dropping the half-created collection, so no boot ever finds half an
index. The error is named, never echoed, because a vendor message can quote part of the key.

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
POST /chat  (a conversation waiting for a human answers "pending_human" and stops here)
    |
    v
 guardrail --blocked (injection / identity swap)--> refusal, END (not remembered)
    |
    v
 router (plan: 1 or 2 steps) --confidence < ROUTER_CONFIDENCE_THRESHOLD--> escalate
    |
    v  one step at a time, in plan order
 knowledge | support --hand-back, hops < MAX_HOPS--> router (replans the whole turn)
    |
    v  answer, refusal, or hand-back with the budget spent
 collect --more steps--> next specialist
    |
    v
 compose --a step needs a human--> escalate (answered part kept)
    |                                  |
    |                                  v
    |                          prepare_handoff -> await_operator (interrupt; resumed by an operator)
    |                                  |
    v                                  v
 remember <----------------------------+
    |
    v
 response
```

Three decisions are worth naming:

- **The confidence threshold is a conditional edge, not an `if` inside a node.** Routing policy is
  readable in one place (`app/graph/build.py`), and a low-confidence request never touches a
  specialist.
- **A failed model call is not a parallel error path.** The router turns a provider error into a
  `RouterDecision` with `confidence = 0.0`, which the threshold edge already handles.
- **Hand-back is bounded by `hops`, across the whole plan.** A specialist that believes its step
  is not its own returns it to the router once, and the router replans the turn; a second miss is
  recorded as that step's refusal, `repeated_misroute`. Two agents cannot pass the same message
  back and forth.
- **Two specialists only for a genuinely compound request.** "Quando cai o dinheiro de ontem e
  como funciona a antecipação?" becomes a plan of two steps, each with its own standalone
  sub-question. The schema caps the plan at two. A single-intent request runs one step and makes
  exactly the model calls it made before plans existed.

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
3. **Guardrail** masks PII into `masked_message`, then classifies. `identity_swap` (prompt
   injection, or a request for another merchant's data) is answered on the spot and never
   routed. Scope is not the guardrail's call: a weather or exchange-rate question is routed.
4. **Router** reads the message and, as data in a separate JSON field, the rolling summary and
   the last `HISTORY_WINDOW` messages. It emits a validated `RouterDecision` — `intent`,
   `confidence`, `rationale`, `lang` and `steps`, one or two `{agent, query_pt}` — and increments
   `hops`. Each `query_pt` is that step's part of the message as a standalone Portuguese
   question, with references to earlier turns resolved ("e o de amanhã?" becomes a question
   about tomorrow's settlement). A rewrite that carries an identifier absent from the
   conversation (a number, date, protocol or merchant id) is discarded in favour of the original
   message. Both versions are logged, masked.
5. The **conditional edge** sends the turn to the first step's specialist, or straight to
   `escalate`.
6. **Knowledge** retrieves on its step's `query_pt`, plus one search per item when the step
   compares products, and either answers citing the retrieved URLs or abstains: with
   `low_retrieval_confidence` when nothing retrieved is close enough, or `insufficient_sources`
   when the model reports that the excerpts do not answer the question. **Support** plans an action from its step's
   `query_pt` and the turn's date (Brazil time, so "ontem" and "amanhã" become date ranges),
   calls at most one tool with the injected identity and the turn's date, and answers citing the
   account — or escalates an individual case. A decline question reads the declined sales and
   answers with the reason the issuer recorded; only a decline the data cannot explain escalates. The mock records are written as of one day and read
   as of the turn's, so "ontem" and "amanhã" always land on the same entries, and every amount
   leaves the tool as the merchant writes it ("R$ 1.240,00"), so the model never formats a float.
   Settlements are filtered by the date the question is about: the sale date for "the money from
   yesterday's sales", which credits today, or the credit date for "what lands tomorrow". The
   status comes from the credit date against today, never from a stored flag. Neither specialist ever sees the history. **Collect** records
   each step's result and starts the next one; **compose** joins them: one answered step is
   copied as is, two are merged by one model call that may not add a number (otherwise the step
   answers are joined as written), and every source is kept. If one step answered and the other
   needs a human, the answered part goes out now and the rest escalates under one protocol. Only
   an escalation opens a protocol, so only an escalation may promise a person: compose drops any
   sentence of an answered part that mentions an agent, a team or a human, and an answer that was
   nothing but that promise escalates, so the promise comes true.
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
  "agents": ["knowledge"],
  "confidence": 0.91,
  "sources": [{"url": "https://site.getnet.com.br/crediario/", "title": "...", "source_type": "kb"}],
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

A document's title is its page's `<title>`, except where several pages share one: the site titles
both the Get Clássica and the Get Smart pages "Getnet | Maquininha POS 3G + Wi-Fi | 4020-4011",
so those pages take their `<h1>` instead and cite as two sources. The other headings stay out:
most are slogans ("nós podemos te ajudar!"), worse names than the titles they would replace.

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

A comparison ("Get Clássica or Get Smart?") also carries one short search per item, written by
the router. Each query's two lists are fused on their own and the per-query rankings are then
interleaved, best hit of each first. Fusing every list at once was tried and rejected: RRF rewards
consensus, so the generic offer pages that rank fairly well for every query crowded out the one
page each item is about.

Abstention keys off **dense similarity, not the RRF score**. RRF is a ranking signal: a document
that tops both lists scores the same whether the corpus answers the question or not. The absolute
similarity is what can say "this is not in the base". The threshold is calibrated in
[`evals/retrieval_calibration.md`](evals/retrieval_calibration.md) (`uv run python -m
evals.run_retrieval_calibration`): 22 questions the corpus answers against 15 it does not, through
three embedders. The local model separates nothing (gap −0.100); `text-embedding-3-small` and
`text-embedding-3-large` separate both groups completely, the large one with the wider gap (+0.110,
from 0.464 to 0.574) for the same latency and a negligible cost, so it is the default and the
threshold is the middle of its gap, 0.52. The threshold belongs to the embedder: with the local
one, keep 0.35 as a floor.

**Generate** (`app/graph/knowledge.py`) — the prompt receives only the surviving chunks, each
labelled with its URL, plus the financial hard rules. Sources are taken from what was retrieved,
never from URLs the model claims to have used. The answer carries a structured `coverage`:
`full`, `partial` or `none`, because retrieved text can clear the threshold without answering the
question. A partial answer goes out with the supported part and a plain sentence on what the site
does not cover ("O site não informa se…"). With `none` the model's "I did not find it" is
discarded and the step goes to a human with a protocol, instead of reaching the merchant as an
answer. If the embedding API is unreachable, the step abstains the same way.

**Web search** (`app/tools/web.py`) — only for steps the router labels `general_info`, such as
the weather or an exchange rate; never a fallback for a product question the corpus cannot
answer, which still abstains, and never reachable from the support agent. What leaves the process
is the step's question, PII-masked, with every token carrying a digit removed unless the merchant
typed it in that very message, so an amount read from the account or the history cannot end up
in a web query. One Tavily call, capped at `WEB_SEARCH_MAX_RESULTS` pages, with a timeout. A step whose
answer depends on the day (a forecast, a rate) carries the date of the day it asks about, set by
the router as `target_day`: a search engine cannot resolve "amanhã", undated it returns
yesterday's page, and dated with today it returns today's forecast. The pages reach the
model as untrusted data in a JSON field, with today's date; results that do not answer the
question are reported as such (`coverage: none`) and degrade like a failed search; results that
answer part of it are answered as far as they go. The answer must
carry only numbers found in the pages (hard rule 4) and no link or e-mail of its own, which is how
a planted instruction usually shows; otherwise, and when there is no key, the call times out or
the provider fails, the merchant gets a pt-BR "cannot look this up right now" message instead.
Sources carry `source_type: "web"` with the page's url and title.

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
get_settlement_schedule    model sees: ['date_from', 'date_to', 'date_field']
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
249 passed in 3.96s
```

One module per graph node (`test_router`, `test_knowledge`, `test_support`, `test_guardrail`,
`test_escalation`, `test_plans`, `test_human_review`), the two tools (`test_tools`), masking and
the log pipeline (`test_observability`), retrieval and fusion (`test_retrieve`), memory
(`test_memory`, `test_followups`), web search (`test_web`), three adversarial paths
(`test_adversarial`: prompt injection, another customer's data, a web page that gives orders), and
the v2 flows over HTTP through the real app and SQLite stores (`test_flows`: two-turn memory, a
restart, thread isolation, a compound plan, web search with and without a key, the masked web
query, a failing tool). `specs/plan.md` §11 maps every v2 invariant to the test that pins it. The
suite reads neither the developer's `.env`, nor an exported setting, nor the wall clock: each test
sees the defaults, plus what it passes, on the day the mock account records describe.

**CI** — `.github/workflows/ci.yml` runs on every push and pull request: ruff and the offline
suite, then `docker compose build`, the container started from `.env.example` with
`LLM_BACKEND=fake`, and a smoke test of `/health` and one `/chat` that must come back answered,
with sources and without escalation. The fake backend (`app/providers/fake.py`) derives a valid
instance of any schema from its declaration, so the smoke test proves the image, the index built
on first boot and the graph work together without a vendor account. It asserts the response
contract, never the content.

**Routing golden set** — 28 labelled rows: the 10 challenge scenarios, 2 ambiguous, 2 general
questions unrelated to the acquirer, 5 compound requests (A03 among them, relabelled as the
compound request it is), 6 follow-ups and 3 turns after an operator's reply. A follow-up row
carries its earlier turns and the words its rewrite must keep ("dessa venda" has to come out
naming the sale), so the right agent with a query that lost its referent is a miss. It is a
measurement, not a test: a score that dips one row is a signal to read, not a red build, so it
never runs inside pytest. Against the real router (`gpt-6-luna`, low effort, prompt v8):

```
$ uv run python -m evals.run_routing_eval
golden set: 28 rows from evals/routing_golden_set.jsonl

guardrail (deterministic)
  overall: 100.0%  (28/28)

router (model)
  overall: 96.4%  (27/28)
  by group:
    ambiguous         50.0%  (1/2)
    challenge        100.0%  (10/10)
    compound         100.0%  (5/5)
    general          100.0%  (2/2)
    human_review     100.0%  (3/3)
    multi_turn       100.0%  (6/6)
  misses:
    A01  expected=support  got=knowledge
```

A01 ("Quanto eu pago de taxa por venda no crédito?") is ambiguous by design: the published rate
table or this merchant's own rate. The guardrail judges only injection and identity, so every
labelled row passes it; whether a weather or exchange-rate question goes to the web is the
router's call. With `LLM_BACKEND=fake` the router stage also runs, offline: that proves the runner
end to end and measures nothing about routing quality.

**Router accuracy against a real model** is in
[`evals/router_benchmark.md`](evals/router_benchmark.md), written by
`uv run python -m evals.run_model_benchmark`: the golden set routed twice through five candidate
models, with accuracy, latency, tokens and cost. `gpt-6-luna` at low reasoning effort scores
54/56, within one row of `gpt-6-sol` (55/56) at about an eighteenth of the cost, and beats
`gpt-4o-mini` (46/56) while costing less, for under a second more per call. That is the default.
Every luna and sol miss is A01.

Effort was measured where it could pay, the follow-ups, whose rewrite has to carry a referent
across turns: 9 rows, 5 runs each. Low and medium both score 45/45, medium slower, so the router
stays on low and no per-node setting was added; the bar set before measuring was a 10-point win.
What fixed follow-ups was the prompt, not the effort: the first eight follow-up rows through
router prompt v6, before the rule that resolves "dessa venda" to the entity of the last reply, scored 28/40 on
low, losing the sale an operator had just named in 5 runs out of 5. Two runs per row is a small
sample, and one row apart is within noise, so the model choice rests as much on the twentyfold
cost gap as on accuracy.

**LLM-as-judge** — [`evals/report.md`](evals/report.md), written by
`uv run python -m evals.run_judge`: the ten scenarios, two follow-ups, a compound request and the
human-review loop (the pause, the operator's reply, the turn after it) through the real graph.
`gpt-6-sol` scores every answered turn 1 to 5 against the evidence that turn had, the excerpts,
account data, web pages or operator reply, for groundedness and for relevance; a pause for a
human is listed, not scored. The committed run: groundedness 4.87 and relevance 4.73 over 15
scored turns. The report names its own weak spots: S07 quoted a stale euro rate, and S01 abstained
in that run, with a retrieval confidence of 0.495 against 0.52, when the router wrote its
per-item searches without the word "maquininha". S09 retrieved at 0.726, well above the
threshold. The judge's scores move from run to run with live web results and sampling, so one run
is a reading, not a benchmark.

## 7. Observability and guardrails

**Structured logs.** `structlog` renders JSON to stdout. `mask_pii` is installed as a processor,
not called at each site, so no node can log an unmasked value even by accident. It never touches
the system's own ids: `trace_id` and `protocol` skip it by field, a digit run glued to letters (as
in a hex id) is never read as a phone, and protocols and transaction ids are skipped wherever they
appear, because one protocol in a hundred is shaped exactly like a phone number. `instrument_node`
wraps every graph node, so timing and outcome fields are uniform and an agent that forgets to log
cannot exist.

```json
{"agent": "support", "latency_ms": 1.5, "trace_id": "591f8d5b7aa5475eb1d31e4a9056fa7b",
 "intent": "account_data", "plan": "support", "router_confidence": 0.93,
 "guardrail": null, "refusal_reason": null, "handoff_reason": null, "escalated": false,
 "event": "node.completed", "level": "info", "timestamp": "2026-09-01T18:22:47.886994Z",
 "user_id": "u_a9f6bd311e2b"}
```

The merchant id is pseudonymised with a stable SHA-256 prefix, so traces correlate without
carrying the identifier. `escalation.reason` is a typed enum and doubles as the operational metric:
`low_router_confidence`, `repeated_misroute`, `low_retrieval_confidence`, `insufficient_sources`,
`individual_case`, `tool_unavailable`, `agent_not_implemented`.

**Metrics.** `GET /metrics` serves two Prometheus families, both recorded in `instrument_node`
so no node can skip them: `support_escalations_total{reason}`, the typed reasons above as
counters, and `support_node_latency_seconds{node}`, a histogram per graph node. In the last real
run in Docker (`gpt-6-luna` at low effort, `text-embedding-3-large`), a turn took 4 to 6.5
seconds, and 8.9 for the compound request; the router is about half of a single-step turn.

**Tracing.** Optional LangSmith tracing, off by default (`LANGSMITH_TRACING`,
`LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`). LangSmith's switch is global, set once at start-up
(`app/observability/tracing.py`). On, every trace goes through one client whose inputs and
outputs pass through `mask_pii` before upload, so a CPF leaves as `[CPF]`. Off, the switch is set
to off rather than left alone: LangChain would otherwise start its own tracer, unmasked, from a
`LANGSMITH_TRACING=true` it finds in the environment. Off costs nothing per request, and on, an
upload error stays in LangSmith's background thread: tracing cannot fail a request.

**Guardrail.** Deterministic patterns, before any model call: prompt injection and identity-swap
attempts get a short refusal, and PII is masked. It is defence in depth for identity, since
injected state already blocks the data path. It no longer judges scope: general questions are
routed to a cited web search.

**Financial hard rules**, carried in the prompts and asserted in tests:

1. No rate, term, limit or amount unless it is literally present in a retrieved chunk or an account
   record. No deriving, rounding or averaging.
2. No promise of a future date. An account forecast is echoed as a forecast, with the reference
   date of the data.
3. No verdict on an individual case beyond the data. A declined sale is answered with the reason
   the issuer recorded, and nothing more; with no declined sale or no recorded reason, it
   escalates. Chargebacks, refunds, blocks, fraud and pending registration always escalate.
4. Rephrasing adds nothing: a composed answer or an operator reply carrying a number its source
   lacks is replaced by that source, with numbers compared by value ("R$ 1.234,56" = "1234.56").
5. No mention of a human agent unless the turn opened a protocol.

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

**Out of scope, deliberately.** OpenTelemetry and MCP were excluded when the scope was closed, and
were never re-proposed. v1 also left web search out; v2 adds it for general questions only, so a
product question the corpus cannot answer still abstains instead of trusting a third-party page.
OpenTelemetry would duplicate what structured logs already carry at this size. A fourth agent and a
human-handoff queue were left out; escalation emits a protocol and a typed reason, which is the
part a queue would consume.

## 9. Limitations and next steps

**The retrieval threshold rests on 37 questions.** The calibration set was written by hand: 22
questions the corpus answers and 15 it does not. With `text-embedding-3-large` the gap is 0.110,
so a question close to either edge could still land on the wrong side. The sufficiency flag
catches what passes the threshold without being answered, at the cost of one model call.

**`query_pt` is load-bearing.** A raw English question scores 0.375 against this corpus, below an
off-topic Portuguese one. Retrieval works because the router rewrites the query into Portuguese
first. Without that step the English path degrades badly.

**Article chunking cuts tables.** A 600-token window splits the fee tables on the offers page
mid-row, so a percentage can survive while the plan and condition that qualify it do not — which is
in direct tension with hard rule 1. Structural chunking (by heading, block or table row) for the
article route is the highest-value fix, and it costs little because the 120 FAQ pairs never reach
the window.

**The web route depends on one label.** Whether a question goes to the web is the router's
`general_info` call. A product question mislabelled as general would be answered from third-party
pages; the prompt forbids it and the golden set measures it, but no deterministic check backs it.

**The test fixture is generated by the pipeline it tests.** If extraction is wrong, the fixture is
wrong in the same way and the suite still passes. Hand-curating the sampled chunks once would break
that circularity.

**Router confidence is self-reported.** The escalation gate keys off a number the model produces
about itself, which is a weak signal. The model benchmark measures routing accuracy, not whether
that number is calibrated.

**Next, in order:** calibrate the router confidence threshold against the golden set; switch
the article route to structural chunking; expose terminal status,
which exists in `data/customers.json` but no tool reads; then revisit the scope check.
