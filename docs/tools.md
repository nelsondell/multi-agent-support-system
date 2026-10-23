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

The ticket would make a support turn all three of Meta's Rule of Two (untrusted input, private
data, a state change), so the merchant approves it.

| Step | What happens |
| --- | --- |
| Proposal | the planner answers `propose_technical_ticket`; code checks the merchant's own terminal is offline (`sem_comunicacao`), or the turn goes to a person |
| Pause | `confirm_action` interrupts; `/chat` answers `pending_confirmation` with an id and the operation rendered by code, e.g. `open_technical_ticket(terminal_id="GC-1180-7742", symptom="...")` |
| Confirmation | only a `/chat` with that `confirm_action_id` runs it; any other message cancels it (audited) and runs as a turn of its own |
| Replay | the same id returns the same receipt without writing; another merchant's or an unknown id is a 404 |

Proposals, tickets and cancellations are audited ([security.md](security.md)). Tickets are
business records of the terminal; erasing a conversation does not delete them. A step the
supervisor added for a finding only reads: its write proposals are dropped, and a knowledge step
added that way never searches the web ([ADR 0001](adr/0001-bounded-replanning.md)).
