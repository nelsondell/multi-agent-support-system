# ADR 0005: One write action, behind the merchant's confirmation (T-50, accepted)

**Context.** Every tool used to read. A terminal still offline after the basic steps needs a
technical ticket. That turn mixes untrusted input, the merchant's private data and a state change:
Meta's Agents Rule of Two says a human must approve it.

**Decision.** `open_technical_ticket` runs in three steps:
1. **Proposal.** Support proposes the ticket as structured output, and the graph pauses with
   `interrupt(response_schema=...)`.
2. **Rendering.** The reply carries `pending_action`: the operation rendered by code from the
   arguments, never a model summary.
3. **Confirmation.** Only `POST /chat` with its `confirm_action_id` runs it; anything else cancels
   it.

Eligibility is checked in code, the proposal id is the idempotency key, and the write is audited.

**Evidence.** `tests/test_actions.py`: no confirmation, no write; a replayed confirmation writes
once; another merchant's id is a 404; a backend failure mid-write leaves no ticket.

**Considered.**
- **The model calls the write tool directly:** this breaks the Rule of Two.
- **An operator approves every ticket:** slower, and the decision belongs to the merchant whose
  terminal it is.
