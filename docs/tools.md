# Tools

Every tool an agent can reach. Identity is never an argument: the merchant comes from the
verified token's `sub` and is injected from graph state (`InjectedState`), so it appears in no
schema the model sees, and `call_tool` drops any argument the model may not set.

| Tool | Agent | Model sets | Side effects | Idempotency | Failure modes |
| --- | --- | --- | --- | --- | --- |
| `get_settlement_schedule` | support | `date_from`, `date_to`, `date_field` (`sale_date` or `credit_date`) | none: reads the merchant's settlements | yes, a read | unreadable account store: the step escalates as `tool_unavailable`; unknown merchant: an empty result, answered as "não localizei seu cadastro" |
| `get_transactions` | support | `limit`, `status` (`aprovada`, `negada`, `estornada`) | none: reads the merchant's recent transactions | yes, a read | as above; a decline with no reason on record goes to a person (`individual_case`) |
| `get_account_profile` | support | nothing | none: reads the merchant's plan, terminal model, whether the terminal is communicating and when it last did; never the serial | yes, a read | as above |
| corpus search | knowledge | the standalone query and per-item searches | none | yes | unreachable embedder: nothing retrieved, the step abstains and escalates |
| web search (Tavily) | knowledge, for `general_info` | the standalone query, masked and stripped of numbers the merchant did not type | one outbound search | yes, a read | no key, a timeout or an error status: a pt-BR "cannot look this up now", no escalation |
| `open_technical_ticket` | support proposes; the merchant confirms | `symptom` only, at most 200 characters, masked; the terminal is the merchant's own, chosen by code | **writes**: opens a ticket in the ticket backend, and audits it | yes, on the proposal id: a second confirmation returns the first ticket, and two at once open one | ticket backend down: no ticket (a single insert), the turn escalates as `tool_unavailable`, and a replay finds nothing to return |

## The write, step by step

A support turn already combines untrusted input (the message) with private data (the account).
Adding a state change would make it all three of Meta's Agents Rule of Two, so a human approves
it: the merchant whose terminal it is.

1. **Proposal.** The support planner may answer `propose_technical_ticket` when the merchant
   reports a terminal still offline after the basic steps. Code checks eligibility: the merchant
   has a terminal, and the account data shows it offline (`sem_comunicacao`). If not, the turn
   goes to a person instead.
2. **Pause.** The graph pauses in `confirm_action` with
   `interrupt(response_schema=ActionDecision)`. `/chat` answers `pending_confirmation`, and
   `pending_action` carries an id and the operation rendered by code from the arguments, for
   example `open_technical_ticket(terminal_id="GC-1180-7742", symptom="...")`. It is never a
   model's summary.
3. **Confirmation.** Only a `/chat` whose `confirm_action_id` is that id runs the write. Any
   other message cancels the proposal, which is audited, and then runs as a turn of its own.
4. **Receipt and replay.** The receipt names the ticket. Sending the same confirmation again
   returns the same receipt without writing. Another merchant's id, or one that matches nothing,
   is a 404.

Proposals, tickets opened and cancellations are in the audit log ([security.md](security.md)).
Tickets are business records of the terminal; erasing a conversation does not delete them.

## Steps a finding adds

A step the supervisor added for another step's `Need` reaches the same tools with one
difference: it only reads. A write it proposes never reaches the merchant; the step is dropped
instead. A knowledge step added that way searches the corpus and never the web, because its
question may carry what the account showed ([ADR 0001](adr/0001-bounded-replanning.md)).
