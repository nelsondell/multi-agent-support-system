# Plan — how the system is built

## 1. Module layout

```
app/
  settings.py          pydantic-settings: models, thresholds, index path
  contracts.py         API and cross-node Pydantic models, typed enums
  main.py              FastAPI: POST /chat, GET /health
  graph/
    state.py           GraphState — what crosses node boundaries
    router.py          RouterDecision + router node
    build.py           StateGraph assembly, conditional edges
    nodes/             knowledge, support, guardrail, escalation
  rag/                 extraction, chunking, index build, hybrid retriever
  tools/               the two support tools
  providers/           llm.py (LLM port), account repository
  prompts/             versioned pt-BR markdown, loaded by id
  observability/       structlog setup, PII masking
scripts/ingest.py      host-side corpus build
evals/routing.py       golden-set runner, real LLM, on demand
tests/                 offline suite, mocked provider
```

## 2. Graph

```
START -> guardrail -> router -> [conditional on confidence]
                                  |-- knowledge -> END
                                  |-- support   -> END
                                  |-- escalate  -> END
guardrail short-circuits to END for out_of_domain and identity_swap.
knowledge/support may hand back to router while hops < 2, else escalate.
```

`GraphState` carries only what crosses a node boundary: `message`, `masked_message`, `user_id`,
`trace_id`, `guardrail_outcome`, `route`, `hops`, `handoff_reason`, `sources`, `answer`,
`escalation`. Retrieved chunks stay inside the knowledge node; only the `sources` that back the
answer are promoted to state.

The confidence threshold lives in the conditional edge function, so routing policy is readable
in one place in `build.py` instead of hiding inside a node.

## 3. LLM provider

`app/providers/llm.py` defines a `LLMProvider` protocol with one method, `structured()`, that
takes a system prompt, a user message and a Pydantic schema, and returns an instance of it. The
current implementation wraps `ChatOpenAI.with_structured_output`. Swapping to Azure AI Foundry
means editing that one file and nothing else: no other module imports the vendor SDK.

Structured output is the only calling convention. Nothing in the system parses model prose into a
decision.

## 4. RAG pipeline

Extraction → chunking → embedding → persistence happen offline in `scripts/ingest.py`; retrieval
and generation happen at request time.

- **Extract.** `data/raw/` holds 18 pre-fetched pages and is gitignored, so ingestion must be
  re-runnable from `docs/_local/corpus-urls.txt`. Per-route parsers, because generic extraction
  fails on this site: the FAQ route is a lazy-loaded accordion with question and answer in
  separate nodes, article pages are Beaver Builder with content under `.fl-col-content`, and meta
  descriptions repeat as boilerplate across pages. Each selector is confirmed against a real file
  in `data/raw/` before its parser is written.
- **Chunk.** Section-aware, keeping FAQ pairs intact; each chunk keeps url and title for citation.
- **Index.** Chroma persisted to disk plus a BM25 artifact, both written by the same script and
  rebuilt idempotently.
- **Retrieve.** Dense and lexical top-k fused with RRF, then the threshold check.
- **Generate.** The knowledge prompt receives only the surviving chunks and the hard rules from
  spec §8; the answer cites the chunks it used.

## 5. Prompts

Markdown files under `app/prompts/`, each with a `version` in front matter, loaded by id. No
prompt literal appears in Python. End-user message templates live here too, which keeps every
module single-language.

## 6. Configuration and startup

`pydantic-settings` reads `.env`: model names, temperature, router and retrieval thresholds,
index path. The API builds the provider once at startup and fails fast with an explicit message
when a required setting is missing. The API never builds the index: a missing index is a startup
error that names the command to build it.

## 7. Testing

Two levels, deliberately separate.

- `uv run pytest -q` — offline, provider mocked, no cost, always runs. Covers contracts, router
  decisions, graph edges, guardrail, masking, tool authorization and the API. Knowledge tests use
  `tests/fixtures/kb_sample.jsonl`, never the full corpus.
- `uv run python -m evals.routing` — the routing golden set against a real LLM, on demand. Reports
  per-intent accuracy and a confusion matrix over the challenge scenarios plus ambiguous,
  out-of-domain and injection cases. It never runs inside pytest: a statistical measure that dips
  one case in thirty is not a red test suite.

## 8. Docker

Multi-stage build with uv, no API key at build time. Compose mounts the persisted index as a
volume, so the image stays reproducible and the index is rebuilt on the host by
`uv run python scripts/ingest.py`.

## 9. Deliberately not abstracted

No repository interface over the account fixture beyond the single in-memory implementation, no
agent base class, no message bus. The one port that exists, `LLMProvider`, exists because the
vendor is expected to change.
