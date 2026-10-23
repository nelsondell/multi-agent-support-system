#!/usr/bin/env bash
# The ten challenge scenarios, a follow-up, a compound request and the human-review loop, sent to
# a running service with curl. docs/demo.md is this script's output; the README's table sums it up.
#
#   ./scripts/demo.sh [base_url]
#
# Expects the service to be up (make up) with real keys. Callers prove who they are with tokens
# from the `make dev-keys` key pair, minted here unless MERCHANT_TOKEN, OPERATOR_TOKEN and
# SUPERVISOR_TOKEN are given. Needs curl, jq and uv.
set -euo pipefail

BASE="${1:-http://localhost:8000}"
mint() { uv run --frozen python scripts/dev_auth.py token "$@"; }
MERCHANT_TOKEN="${MERCHANT_TOKEN:-$(mint merchant cliente1988)}"
OPERATOR_TOKEN="${OPERATOR_TOKEN:-$(mint operator ana)}"
SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN:-$(mint supervisor bia)}"
# cliente2042's terminal is offline in the mock account data: the write-action walkthrough.
OFFLINE_TOKEN="${OFFLINE_TOKEN:-$(mint merchant cliente2042)}"
RUN="demo-$(date +%s)"
LAST="$(mktemp)"
trap 'rm -f "$LAST"' EXIT

# One merchant turn: the question, then the response trimmed to what a reader checks.
chat() {
  local label="$1" conversation="$2" message="$3"
  printf '\n=== %s ===\n> %s\n' "$label" "$message"
  curl -sS -X POST "$BASE/chat" -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $MERCHANT_TOKEN" \
    -d "$(jq -nc --arg m "$message" --arg c "$RUN-$conversation" '{message: $m, conversation_id: $c}')" \
  | tee "$LAST" \
  | jq '{delivered: (if (.delivered | length) > 0 then [.delivered[] | {protocol, answer}] else null end),
         status, agents, answer,
         sources: [.sources[] | "\(.source_type): \(.url // .title)"],
         escalation: (if .escalation then {reason: .escalation.reason, protocol: .escalation.protocol} else null end)}
       | with_entries(select(.value != null))'
}

operator() {
  curl -sS -H "Authorization: Bearer $OPERATOR_TOKEN" "$@"
}

merchant() {
  curl -sS -H "Authorization: Bearer $MERCHANT_TOKEN" "$@"
}

curl -sS "$BASE/health" | jq -c .

chat "S01 product comparison" s01 "What's the difference between the Get Clássica and the Get Smart?"
chat "S02 weather, from the web" s02 "What's the weather forecast in Porto Alegre tomorrow?"
chat "S03 settlement, from the account" s03 "When will the money from yesterday's sales be deposited?"
chat "S04 Pix without a bank account" s04 "Do I need a bank account to receive my sales via Pix?"
chat "S05 connectivity, not in the corpus" s05 "My card machine won't connect to the internet, what should I do?"
chat "S06 receivables advance" s06 "How does receivables advance (antecipação) work with Getnet?"
chat "S07 euro, from the web" s07 "What's the euro exchange rate today?"
chat "S08 declined transaction" s08 "My card machine is showing a transaction decline error."
chat "S09 crediário installments" s09 "How many installments can I split a sale into with the crediário?"
chat "S10 Payment Link on WhatsApp" s10 "Can I sell through WhatsApp using the Payment Link?"

chat "Follow-up, turn 1" follow "Quando cai o dinheiro das vendas de ontem?"
chat "Follow-up, turn 2" follow "E o de amanhã?"

chat "Compound request" compound "Quando cai o dinheiro de ontem e como funciona a antecipação?"

chat "Human review 1: an individual case pauses" hitl \
  "Por que o estorno da minha venda no Mastercard final 7788 ainda não apareceu?"
PROTOCOL="$(jq -r '.escalation.protocol' "$LAST")"
printf '\n=== Human review 2: the operator reads the case ===\n'
operator "$BASE/handoffs/$PROTOCOL" | jq '{status, case: (.case | {protocol, reason, attempts, proposed_draft})}'
printf '\n=== Human review 3: the operator replies ===\n'
operator -X POST "$BASE/handoffs/$PROTOCOL/resume" -H 'Content-Type: application/json' \
  -d '{"action": "reply", "answer": "Verificamos: o estorno da venda de R$ 89,50 no Mastercard final 7788 foi aprovado hoje, e o valor volta ao comprador em até 2 dias úteis."}' \
  | jq '{status, answer, sources: [.sources[] | "\(.source_type): \(.url // .title)"]}'
chat "Human review 4: the next message receives the reply, then builds on it" hitl \
  "E em que dia foi feita essa venda?"
printf '\n=== Human review 5: the conversation as stored ===\n'
merchant "$BASE/conversations/$RUN-hitl" | jq '{status, messages: [.messages[] | "\(.role): \(.content)"]}'
printf '\n=== Human review 6: a supervisor reads the audit log ===\n'
curl -sS -H "Authorization: Bearer $SUPERVISOR_TOKEN" "$BASE/audit" \
  | jq '[.[:3][] | "\(.actor) (\(.role)) \(.action) \(.target)"]'
printf '\n=== LGPD: the merchant erases the conversation ===\n'
merchant -o /dev/null -w 'DELETE /me/conversations: %{http_code}\n' -X DELETE "$BASE/me/conversations/$RUN-hitl"
merchant -o /dev/null -w 'GET /conversations afterwards: %{http_code}\n' "$BASE/conversations/$RUN-hitl"

# The one write action: proposed by the agent, run only on the merchant's confirmation.
ticket() {
  local label="$1" message="$2" confirm="${3:-}"
  printf '\n=== %s ===\n> %s\n' "$label" "$message"
  curl -sS -X POST "$BASE/chat" -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $OFFLINE_TOKEN" \
    -d "$(jq -nc --arg m "$message" --arg c "$RUN-ticket" --arg a "$confirm" \
      '{message: $m, conversation_id: $c} + (if $a == "" then {} else {confirm_action_id: $a} end)')" \
  | tee "$LAST" | jq '{status, pending_action, answer} | with_entries(select(.value != null))'
}
ticket "Write action 1: the agent proposes a technical ticket" \
  "Minha maquininha continua sem conexão mesmo depois de reiniciar e testar o chip."
ACTION="$(jq -r '.pending_action.id // empty' "$LAST")"
if [ -n "$ACTION" ]; then
  ticket "Write action 2: the merchant confirms it" "Pode abrir, confirmo." "$ACTION"
  ticket "Write action 3: the same confirmation again returns the same receipt" "Confirmo." "$ACTION"
fi
