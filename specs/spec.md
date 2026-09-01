# Spec — multi-agent support system

## 1. Purpose

A support system for a payments acquirer. One HTTP entry point receives a merchant message and
returns a grounded answer in Brazilian Portuguese, or hands the case to a human. Three agent
roles cooperate: a router that decides, a knowledge agent that answers from the indexed website
corpus, and a support agent that reads the caller's own account data.

## 2. Scope

In scope: router, knowledge agent with RAG, support agent with exactly two tools, escalation,
input guardrail, PII masking, tests, routing golden set, Docker.

Out of scope, and not to be added: web search, OpenTelemetry, MCP.

## 3. API contract

`POST /chat`

```json
{ "message": "string", "user_id": "string" }
```

```json
{
  "answer": "string (pt-BR)",
  "intent": "product_info | troubleshooting | account_data | individual_case | other",
  "target_agent": "knowledge | support",
  "confidence": 0.0,
  "sources": [{ "url": "string", "title": "string" }],
  "escalated": false,
  "escalation": { "protocol": "ESC-...", "reason": "string", "summary": "string" }
}
```

`escalation` is present only when `escalated` is true. `GET /health` returns `{"status": "ok"}`.

`user_id` comes from the request envelope and is treated as authenticated. It is never a field
the model can read or write, and never appears in a tool schema.

## 4. Agent boundary

The dividing question is: **does answering require reading this `user_id`'s account data?**

| Message | Agent | Why |
| --- | --- | --- |
| "What's the difference between Get Clássica and Get Smart?" | knowledge | product facts |
| "My card machine won't connect" | knowledge | generic procedure from the KB |
| "How does antecipação work?" | knowledge | product facts |
| "When will yesterday's sales be deposited?" | support | reads the account |
| "Is my terminal active?" | support | reads the account |

Possessive phrasing does not decide anything. Only the data dependency does.

## 5. Routing and control flow

1. The guardrail inspects the message before routing.
2. The router emits a validated `RouterDecision`; it never returns free text.
3. Below the confidence threshold the request goes straight to escalation. The threshold is a
   conditional edge of the graph, not a branch inside a node.
4. A specialist that concludes the request is not its own may hand back to the router **once**,
   with a typed reason. `hops` counts these passes. A second miss escalates with
   `repeated_misroute`.
5. LLM failure in the router is not a separate error path: it produces a `RouterDecision` with
   `confidence = 0.0`, which the existing threshold edge already sends to escalation.

## 6. Retrieval and abstention

The corpus is 16 pt-BR pages plus 2 English pages from the acquirer's website. Queries arrive in
either language, so the router carries `lang` and `query_pt`, and retrieval runs on `query_pt`.

Retrieval is hybrid: dense (Chroma) crosses an English question into the Portuguese corpus, BM25
pins exact product names such as "Get Smart" versus "Get Mini". Results are fused with
Reciprocal Rank Fusion.

If the fused top score is below the retrieval threshold, the knowledge agent **generates
nothing**. It states in pt-BR that the answer is not in its base and offers the human channel.
There is no web fallback in scope; abstaining is the correct behaviour, not a degradation.

## 7. Support tools and authorization

Exactly two, both read-only:

- `get_settlement_schedule` — recent sales and settlement forecast for the caller.
- `get_account_status` — plan, account situation, linked terminal and its status.

Both take `user_id` from injected graph state. There is no identity parameter in either schema,
so a message naming another merchant's id cannot change what is queried. An unknown id yields an
empty result, which is answered as "não localizei seu cadastro" — not as an error.

## 8. Financial hard rules

These are testable statements, not prompt suggestions.

1. **No number without a literal source.** A rate, term, limit or amount may appear only if it is
   present verbatim in a retrieved chunk or in an account record. Deriving, rounding, averaging
   or completing from model memory is forbidden. Without such a source, the answer points to the
   official channel.
2. **No promise of a future date.** The system never states when money will be credited,
   released or advanced. An account forecast may be echoed, labelled as a forecast, with the
   reference date of the data.
3. **No verdict on an individual case.** Declined transactions, chargebacks, blocks, fraud and
   pending registration are escalated even when account data exists, because the cost of being
   wrong is financial and irreversible.

Feature-by-feature product comparison remains allowed: it is what the corpus documents.

## 9. Guardrail

Runs before routing, three outcomes plus pass-through:

| Outcome | Behaviour |
| --- | --- |
| `out_of_domain` | Short pt-BR scope reply. The graph does not route. Covers weather and exchange-rate style questions, which have no answer without web search. |
| `pii_detected` | CPF, CNPJ, card number and phone are masked before anything is logged. The message still proceeds. |
| `identity_swap` | Request to read another merchant or to override instructions gets a short refusal and a log event. Defence in depth: injected state already neutralises it. |
| `allow` | Proceeds to the router. |

## 10. Escalation

A deterministic terminal node, with no LLM call. It emits a protocol, a typed reason and a masked
summary, returns them with a pt-BR handoff message, and writes one structured log line. The typed
reason doubles as the operational metric.

Reasons: `low_router_confidence`, `repeated_misroute`, `low_retrieval_confidence`,
`individual_case`, `agent_not_implemented`.

## 11. Language

Code, docstrings, logs, commits, README and specs are English. System prompts under
`app/prompts/` and every end-user answer are Brazilian Portuguese. No file mixes the two, which
is why user-facing message templates live under `app/prompts/` rather than inline in modules.

## 12. Non-functional

- No answer reaches the user without a retrieved source or a tool result.
- Sensitive data is masked before any log.
- Tool errors become an answer or an escalation, never a stack trace.
- Every external dependency degrades with a message rather than an exception.
