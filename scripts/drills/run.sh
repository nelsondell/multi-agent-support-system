#!/usr/bin/env bash
# One vendor drill against the observability stack (docs/drills.md):
#
#   scripts/drills/run.sh model_error | model_timeout | web_down
#
# Starts its own compose project with the fault injected (deploy/drill.yml), sends traffic,
# waits for the VendorFailures alert, then removes the fault and times the recovery: the first
# turn answered again, and the alert back to inactive. Keyless for the model drills; web_down
# needs DRILL_LLM_BACKEND=openai, DRILL_EMBEDDING_BACKEND=openai and real keys in .env.
set -euo pipefail

FAULT="${1:?usage: scripts/drills/run.sh model_error|model_timeout|web_down}"
PROJECT="support-drill"
export API_PORT="${API_PORT:-18000}" PROMETHEUS_PORT="${PROMETHEUS_PORT:-19090}"
export GRAFANA_PORT="${GRAFANA_PORT:-13000}" JAEGER_PORT="${JAEGER_PORT:-26686}"
BASE="http://localhost:$API_PORT"
PROM="http://localhost:$PROMETHEUS_PORT"
compose() { docker compose -p "$PROJECT" -f docker-compose.yml -f deploy/drill.yml --profile observability "$@"; }

case "$FAULT" in
  web_down) QUESTION="Qual a cotação do euro hoje?" ;;
  *) QUESTION="What is the Get Smart card machine?" ;;
esac
uv run --frozen python scripts/dev_keys.py > /dev/null
TOKEN="$(uv run --frozen python scripts/dev_token.py merchant cliente1988)"

# One turn: its status, escalation reason and seconds, on one line.
turn() {
  curl -sS -X POST "$BASE/chat" -H 'Content-Type: application/json' \
    -H "Authorization: Bearer $TOKEN" -w ' %{time_total}\n' \
    -d "$(jq -nc --arg m "$QUESTION" --arg c "drill-$RANDOM" '{message: $m, conversation_id: $c}')" \
  | sed -E 's/^(.*) ([0-9.]+)$/\2 \1/' \
  | while read -r seconds body; do
      echo "$body" | jq -r --arg s "$seconds" '"\(.status) \(.escalation.reason // "-") \($s)s"'
    done
}

firing() {
  curl -sS "$PROM/api/v1/alerts" \
    | jq -r '[.data.alerts[] | "\(.labels.alertname) \(.state)"] | unique | join(", ")'
}

alert_state() {
  curl -sS "$PROM/api/v1/alerts" \
    | jq -r '[.data.alerts[] | select(.labels.alertname == "VendorFailures") | .state] | first // "inactive"'
}

wait_for_alert() {  # $1 = firing | inactive; prints the seconds it took
  local want="$1" started=$SECONDS
  until [ "$(alert_state)" = "$want" ]; do
    [ $((SECONDS - started)) -gt 600 ] && { echo "timed out"; return; }
    sleep 5
  done
  echo "$((SECONDS - started)) s"
}

echo "== drill: $FAULT"
FAULT_INJECTION="$FAULT" compose up --build --wait --quiet-pull > /dev/null 2>&1
echo "-- turns while the fault is on"
for _ in 1 2 3 4 5 6; do turn; done
printf -- '-- VendorFailures firing after: '; wait_for_alert firing
sleep 60
echo "-- alerts a minute later: $(firing)"

recovered=$SECONDS
FAULT_INJECTION="" compose up -d --wait api > /dev/null 2>&1
echo "-- fault removed: API healthy again after $((SECONDS - recovered)) s"
until [ "$(turn | cut -d' ' -f1-2)" = "answered -" ]; do
  [ $((SECONDS - recovered)) -gt 300 ] && { echo "-- no answered turn within 300 s"; exit 1; }
  sleep 2
done
echo "-- first answered turn $((SECONDS - recovered)) s after the fault was removed"
printf -- '-- VendorFailures inactive after: '; wait_for_alert inactive

[ -n "${KEEP:-}" ] || compose down -v > /dev/null 2>&1
