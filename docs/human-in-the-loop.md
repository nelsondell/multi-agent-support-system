# Human in the loop

Every escalation goes to a fourth agent that pauses the graph with LangGraph's `interrupt()` and
continues the same conversation when an operator answers. It is two nodes because `interrupt()`
re-runs its node on resume: `prepare_handoff` does every side effect once (the case, with what the
agents tried and why they stopped, the pages retrieved and a draft reply, recorded in a SQLite
queue), and `await_operator` only pauses and, on resume, turns the operator's input into the answer
of the step that paused. The graph then goes back into the plan: its remaining steps run with the
operator's text as evidence, within the hop budget, and compose joins everything into the reply.

```bash
# 1. an individual case pauses: status pending_human, with a protocol
curl -s localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message": "Por que o estorno da minha venda no Mastercard final 7788 ainda não apareceu?", "user_id": "cliente1988", "conversation_id": "c2"}'
# 2. the operator lists open cases and reads one
curl -s localhost:8000/handoffs -H "Authorization: Bearer $OPERATOR_TOKEN"
curl -s localhost:8000/handoffs/ESC-ECF4B2E5DF -H "Authorization: Bearer $OPERATOR_TOKEN"
# 3. and answers: approve the draft as written, reply in their own words, or cancel
curl -s -X POST localhost:8000/handoffs/ESC-ECF4B2E5DF/resume \
  -H "Authorization: Bearer $OPERATOR_TOKEN" -H 'Content-Type: application/json' \
  -d '{"action": "reply", "answer": "Verificamos: o estorno da venda de R$ 89,50 no Mastercard final 7788 foi aprovado hoje, e o valor volta ao comprador em até 2 dias úteis."}'
# 4. the merchant's next message receives the reply in `delivered`, then its own answer,
#    which builds on what the operator said
curl -s localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message": "E em que dia foi feita essa venda?", "user_id": "cliente1988", "conversation_id": "c2"}'
# 5. the whole conversation, for its merchant only
curl -s 'localhost:8000/conversations/c2?user_id=cliente1988'
```

**The reply.** It is rephrased for the merchant, but if the rephrasing carries a number or a status
the operator didn't write, the operator's literal text goes out instead (hard rule 4). It cites the
operator and lands in the history, so step 4 resolves "essa venda" to the sale the operator named.

**Delivery.** The reply is held for the merchant: their next `/chat` delivers it in `delivered`,
exactly once, before that message's own answer.

**While a case is open,** the conversation answers `pending_human` with the protocol, records each
message as a masked note the operator sees on the case, and runs nothing; other conversations are
unaffected.

**Compound requests.** When one step of a two-step plan needs a person, the turn pauses at that
step and whatever an earlier step answered goes out now. After the operator answers, the remaining
step runs and compose joins its answer with the operator's, citing both, without repeating the part
already sent.

**Ending without an answer.** An operator can cancel a case, and every case expires after
`HANDOFF_TTL_HOURS` (24): the merchant's next message then closes it with a notice and is answered
as usual, so no conversation waits forever.

**Durability and errors.** Cases and paused threads survive a restart. The resume takes the
conversation's lock, so it never runs alongside a merchant's message, and claims the case
atomically, so two operators cannot both resume it. Errors: 401 without a valid token, 503 with
none configured, 404 for an unknown protocol, 409 for a case already closed, 422 to approve a case
with no draft.
