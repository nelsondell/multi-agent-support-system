# Security

How the service knows who is calling, what each caller may do, what it keeps and for how long,
and how each risk in the OWASP Top 10 for Agentic Applications (2026) is controlled and tested.

## Identity

Every caller sends `Authorization: Bearer <JWT>`. Tokens are RS256, and the API checks the
signature against the identity provider's public keys (`AUTH_JWKS_URL`: an https JWKS URL, or the
file `make dev-keys` writes), plus the issuer, the audience and the expiry. A missing, forged,
expired or misaddressed token is a 401 (`app/auth.py`). The keys are read once, at start-up:
rotating them means restarting the API.

- **Merchants** carry `MERCHANT_AUDIENCE`. The merchant id is the token's `sub` and comes from
  nowhere else: `/chat` rejects a body that names a user (422). The graph receives the id
  through `InjectedState`, and no tool schema the model sees has an identity field.
- **Operators** carry `OPERATOR_AUDIENCE` and a `role`. `operator` lists, reads and answers
  cases; `supervisor` may also read the audit log. A token without one of those roles is a 403.
- **Local runs:**
  - `make dev-keys` writes a key pair to `.dev/`, which git and the image both ignore. Compose
    mounts only the public `jwks.json`.
  - `uv run python scripts/dev_token.py merchant cliente1988` mints a merchant token;
    `operator ana` and `supervisor bia` mint the other two kinds.
- **Isolation:** a conversation is keyed by SHA-256 of (merchant, conversation id), so another
  merchant's conversation is a 404, never an empty one.
- **Rate limit:** a merchant is held to `RATE_LIMIT_PER_MINUTE` requests in any 60 s, per
  replica. Past it, the answer is 429 with `Retry-After`.

## Audit log

Every operator action (listing cases, reading one, approving, replying, cancelling) and every
write (a case expiring, a merchant erasing a conversation, a retention purge) appends one row:
who, in which role, which action, on which case or conversation (by protocol or thread hash),
and when. No message text is stored, so erasure never has to touch the log. Triggers make the
table append-only on SQLite and on Postgres: an UPDATE or DELETE fails, even from the API's own
connection. Supervisors read it at `GET /audit`.

## Personal data (LGPD)

- **Masking:** CPF, CNPJ, card and phone are masked before anything is logged, traced, stored in
  the history or sent to the web search.
- **Erasure:** `DELETE /me/conversations/{id}` removes the conversation's checkpoints and every
  case it opened, with payloads, notes and held replies. It runs under the conversation's lock,
  so no turn writes it back, and it is audited.
- **Retention:** `make purge` erases every conversation idle for longer than `RETENTION_DAYS`
  (90), with its cases, and audits the count. Run it on a schedule.

## OWASP Top 10 for Agentic Applications (2026)

| Risk | How it applies here | Control | Tested in |
| --- | --- | --- | --- |
| ASI01 Agent Goal Hijack | A message, a web page or the history tells the agent to do something else | Deterministic guardrail before any model call; history and pages passed as data, never as instructions; web answers may carry no number or link the pages lack | `test_guardrail.py`, `test_adversarial.py`, `test_followups.py` |
| ASI02 Tool Misuse and Exploitation | The model calls an account tool with arguments it should not set, or in a loop | One tool call per support step; arguments filtered to the schema; a hop budget per turn; the one write runs only on the merchant's confirmation, idempotently | `test_tools.py`, `test_plans.py`, `test_actions.py` |
| ASI03 Identity and Privilege Abuse | The model, or a client, picks whose data to read | Merchant id only from a verified token's `sub`, injected into tools; operator roles; 404 across merchants | `test_security.py`, `test_tools.py`, `test_adversarial.py` |
| ASI04 Agentic Supply Chain Vulnerabilities | A dependency, model asset or base image is compromised or downloaded at runtime | Lockfile with `--frozen`; tokenizer committed and hash-checked; base images by digest; actions by SHA; SBOM, Trivy and gitleaks in CI; Dependabot | `test_offline.py`, CI jobs `verify`, `secrets`, `container` |
| ASI05 Unexpected Code Execution | A stored checkpoint names a type for the decoder to import | Checkpoint deserializer restricted to the state's own types; the agent runs no generated code | `test_checkpoints.py` |
| ASI06 Memory and Context Poisoning | An injected turn steers later turns | Refused messages are never remembered; history is masked and read as data; a rewrite that invents an identifier is dropped | `test_memory.py`, `test_followups.py` |
| ASI07 Insecure Inter-Agent Communication | One agent's output alters another's instructions | Agents exchange typed Pydantic objects inside one process; specialists get only their standalone query | `test_router.py`, `test_plans.py` |
| ASI08 Cascading Agent Failures | A failing model, tool or replica takes the turn, or other turns, down | Node timeouts and retries; model failures escalate as `model_unavailable`; tool errors as `tool_unavailable`; per-conversation locks; turns resume across replicas | `test_resilience.py`, `test_replicas.py`, `test_flows.py` |
| ASI09 Human-Agent Trust Exploitation | A fluent rephrasing changes what the operator decided | Hard rule 4: a rephrasing or draft with a new number or a flipped status falls back to the source text; a person is promised only with a protocol | `test_numbers.py`, `test_statuses.py`, `test_human_review.py`, `test_plans.py` |
| ASI10 Rogue Agents | An agent acts outside its role, unobserved | Each agent has a typed set of exits; structured logs and metrics per node; append-only audit of every operator action and write | `test_telemetry.py`, `test_security.py` |

## Rule of Two

Meta's Agents Rule of Two: within one session, an agent should combine at most two of these
three properties without a human approving.

- **[A]** processes untrusted input.
- **[B]** reaches sensitive systems or private data.
- **[C]** changes state or communicates externally.

| Flow | A untrusted input | B private data | C state change | Holds because |
| --- | --- | --- | --- | --- |
| Knowledge, corpus | yes (the message) | no (public site) | no | read-only, public data |
| Knowledge, web | yes (message and pages) | no (masked query, no account data) | outbound search only | the query is masked and carries no number the merchant did not type |
| Support | yes (the message) | yes (the merchant's account) | no (read-only tools) | A and B, and no state change |
| Technical ticket | yes (the message) | yes (the account and terminal) | yes (opens a ticket) | all three, so the merchant confirms the code-rendered operation first ([tools.md](tools.md)) |
| Human review | yes (the message) | yes (the case) | case recorded, reply sent | the reply goes out only after a human decides |
| Erasure | no (a verified merchant on their own thread) | yes | deletes data | no untrusted input decides it; it is audited |
