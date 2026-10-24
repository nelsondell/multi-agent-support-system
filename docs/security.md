# Security

How the service knows who is calling, what each caller may do, what it keeps and for how long,
and how each risk in the OWASP Top 10 for Agentic Applications (2026) is controlled and tested.

## Identity

Every caller sends an RS256 bearer token, checked against the identity provider's keys
(`AUTH_JWKS_URL`, read once at start-up, so rotating them means a restart), issuer, audience and
expiry; anything else is a 401 (`app/auth.py`).

| Caller | Token | May |
| --- | --- | --- |
| Merchant | `MERCHANT_AUDIENCE`; the id is `sub`; a body `user_id` must equal it (403) | chat and read its own conversations; another merchant's is a 404; `RATE_LIMIT_PER_MINUTE` per replica, then 429 with `Retry-After` |
| Operator | `OPERATOR_AUDIENCE`, `role: operator` | list, read and answer cases |
| Supervisor | `OPERATOR_AUDIENCE`, `role: supervisor` | also read `GET /audit`; a token without a role is a 403 |

Locally, `make dev-keys` writes a key pair to `.dev/` (ignored by git and the image; compose mounts
only `jwks.json`), and `uv run python scripts/dev_auth.py token merchant cliente1988` mints a token.

## Audit and personal data (LGPD)

| What | How |
| --- | --- |
| Audit log | one row per operator action and per write: who, role, action, target (protocol or thread hash), when; no message text; append-only by trigger on SQLite and Postgres |
| Masking | CPF, CNPJ, card and phone masked before any log, trace, history entry, web query or rerank query (Cohere, opt-in) |
| Erasure | `DELETE /me/conversations/{id}`: checkpoints and every case it opened, under the conversation's lock; audited |
| Retention | `make purge`: conversations idle past `RETENTION_DAYS` (90), with their cases; audited |

## OWASP Top 10 for Agentic Applications (2026)

| Risk | How it applies here | Control | Tested in |
| --- | --- | --- | --- |
| ASI01 Agent Goal Hijack | A message, a web page or the history tells the agent to do something else | Deterministic guardrail before any model call; history and pages passed as data, never as instructions; web answers may carry no number or link the pages lack; a web answer can never add a step to the plan, and only code marks a step as added | `test_guardrail.py`, `test_adversarial.py`, `test_followups.py`, `test_replanning.py` |
| ASI02 Tool Misuse and Exploitation | The model calls an account tool with arguments it should not set, or in a loop | One tool call per support step; arguments filtered to the schema; per-turn budgets for hops, added steps (`MAX_PLAN_CHANGES`), model calls and cost; a step added for a finding only reads and never searches the web; the one write runs only on the merchant's confirmation, idempotently | `test_tools.py`, `test_plans.py`, `test_actions.py`, `test_replanning.py` |
| ASI03 Identity and Privilege Abuse | The model, or a client, picks whose data to read | Merchant id only from a verified token's `sub`, injected into tools; operator roles; 404 across merchants | `test_security.py`, `test_tools.py`, `test_adversarial.py` |
| ASI04 Agentic Supply Chain Vulnerabilities | A dependency, model asset or base image is compromised or downloaded at runtime | Lockfile with `--frozen`; tokenizer committed and hash-checked; base images by digest; actions by SHA; SBOM, Trivy and gitleaks in CI; Dependabot | `test_offline.py`, CI jobs `test` and `container` |
| ASI05 Unexpected Code Execution | A stored checkpoint names a type for the decoder to import | Checkpoint deserializer restricted to the state's own types; the agent runs no generated code | `test_checkpoints.py` |
| ASI06 Memory and Context Poisoning | An injected turn steers later turns | Refused messages are never remembered; history is masked and read as data; a rewrite that invents an identifier is dropped | `test_memory.py`, `test_followups.py` |
| ASI07 Insecure Inter-Agent Communication | One agent's output alters another's instructions | Agents exchange typed Pydantic objects inside one process; specialists get only their standalone query | `test_router.py`, `test_plans.py` |
| ASI08 Cascading Agent Failures | A failing model, tool or replica takes the turn, or other turns, down | Node timeouts and retries; model failures escalate as `model_unavailable`; tool errors as `tool_unavailable`; per-conversation locks; turns resume across replicas | `test_resilience.py`, `test_replicas.py`, `test_flows.py` |
| ASI09 Human-Agent Trust Exploitation | A fluent rephrasing changes what the operator decided | Hard rule 4: a rephrasing or draft with a new number or a flipped status falls back to the source text; a person is promised only with a protocol | `test_numbers.py`, `test_statuses.py`, `test_human_review.py`, `test_plans.py` |
| ASI10 Rogue Agents | An agent acts outside its role, unobserved | Each agent has a typed set of exits; structured logs and metrics per node; append-only audit of every operator action and write | `test_telemetry.py`, `test_security.py` |

Each row is also attacked by `evals/red_team.jsonl`: 40 cases tagged with their ASI id, 10 of them
adaptive, with planted web pages and corpus passages, replayed in CI ([evals](../evals/README.md)).

## Rule of Two

Meta's Agents Rule of Two: without a human approving, an agent combines at most two of untrusted
input (A), private data (B) and a state change (C).

| Flow | A untrusted input | B private data | C state change | Holds because |
| --- | --- | --- | --- | --- |
| Knowledge, corpus | yes (the message) | no (public site) | no | read-only, public data; an opt-in reranker gets the masked query and public excerpts |
| Knowledge, web | yes (message and pages) | no (masked query, no account data) | outbound search only | the query is masked and carries no number the merchant did not type |
| Support | yes (the message) | yes (the merchant's account) | no (read-only tools) | A and B, and no state change |
| Technical ticket | yes (the message) | yes (the account and terminal) | yes (opens a ticket) | all three, so the merchant confirms the code-rendered operation first ([tools.md](tools.md)) |
| Human review | yes (the message) | yes (the case) | case recorded, reply sent | the reply goes out only after a human decides |
| Erasure | no (a verified merchant on their own thread) | yes | deletes data | no untrusted input decides it; it is audited |
