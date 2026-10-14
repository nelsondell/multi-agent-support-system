#!/usr/bin/env bash
# The ten challenge scenarios, a follow-up, a compound request and the human-review loop, sent to
# a running service with curl. The README's demo outputs are this script's output.
#
#   OPERATOR_TOKEN=... ./scripts/demo.sh [base_url]
#
# Expects the service to be up (docker compose up) with real keys. Without OPERATOR_TOKEN the
# human-review loop is skipped. Needs curl and jq.
set -euo pipefail

BASE="${1:-http://localhost:8000}"
USER_ID="cliente1988"
RUN="demo-$(date +%s)"
LAST="$(mktemp)"
trap 'rm -f "$LAST"' EXIT

# One merchant turn: the question, then the response trimmed to what a reader checks.
chat() {
  local label="$1" conversation="$2" message="$3"
  printf '\n=== %s ===\n> %s\n' "$label" "$message"
  curl -sS -X POST "$BASE/chat" -H 'Content-Type: application/json' \
    -d "$(jq -nc --arg m "$message" --arg u "$USER_ID" --arg c "$RUN-$conversation" \
      '{message: $m, user_id: $u, conversation_id: $c}')" \
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

if [ -z "${OPERATOR_TOKEN:-}" ]; then
  printf '\nOPERATOR_TOKEN is not set: human-review loop skipped.\n'
  exit 0
fi
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
curl -sS "$BASE/conversations/$RUN-hitl?user_id=$USER_ID" | jq '{status, messages: [.messages[] | "\(.role): \(.content)"]}'

