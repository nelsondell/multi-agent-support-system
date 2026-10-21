# Vendor drills

A drill breaks one vendor on purpose and checks that the merchant still gets an answer, that the
right alert fires, and how fast everything recovers once the vendor is back.

```bash
make drill FAULT=model_error     # the model vendor answers 500 to every call
make drill FAULT=model_timeout   # the model vendor never answers
make drill FAULT=web_down        # the web search vendor is down
```

`scripts/drills/run.sh` starts its own compose project (`support-drill`, separate volumes and
ports) with the `observability` profile and `deploy/drill.yml`. That override sets the fault
through `FAULT_INJECTION`, short timeouts (`LLM_CALL_TIMEOUT_S=2`, `NODE_TIMEOUT_S=10`) so a drill
takes minutes, and spans sent to Jaeger. The script:

1. sends six turns and prints each one's status, escalation reason and time;
2. waits for the `VendorFailures` alert to fire;
3. lists every alert a minute later;
4. restarts the API without the fault and times the API's return, the first answered turn and
   the alert clearing.

The model drills run keyless, with the fake model and the local embedder.

## Results

Run on 2026-10-21 against the image built from this branch (macOS, Docker through Colima).
Prometheus scrapes every 5 s and evaluates every 15 s.

| Drill | Expected | Observed while failing | Alert fired | Recovery |
| --- | --- | --- | --- | --- |
| `model_error` | Every turn escalates as `model_unavailable` after one retry; no 5xx | 6 of 6 turns `pending_human`, `model_unavailable`, in 0.6 to 1.4 s | `VendorFailures` after 76 s; `EscalationSpike` firing a minute later | API healthy 6 s after the fault was removed; first answered turn at 7 s; `VendorFailures` inactive 160 s later |
| `model_timeout` | Each call cut at `LLM_CALL_TIMEOUT_S`, retried once, then escalated; no 5xx | 6 of 6 turns `pending_human`, `model_unavailable`, in 6.6 to 7.3 s (two 2 s attempts, the backoff, and the draft's own 2 s) | `VendorFailures` after 55 s; `EscalationSpike` firing a minute later | API healthy after 6 s; first answered turn at 7 s; `VendorFailures` inactive 176 s later |
| `web_down` | A general question gets the pt-BR "cannot look this up now", no escalation; `VendorFailures{vendor="web"}` fires | **Not run yet**: only a real model routes a question to the web, and the OpenAI key available on 2026-10-21 was rejected. Run with `DRILL_LLM_BACKEND=openai DRILL_EMBEDDING_BACKEND=openai` and a valid key. | — | — |

The alert clears five minutes of failures after they stop, because `VendorFailures` counts them
over a 5-minute window. The turns' traces in Jaeger show each failed router attempt, the retry,
then `escalate`, `prepare_handoff` and `await_operator`, under one `POST /chat`.

## What the drills changed

The first `model_timeout` run found a real failure. A vendor that hangs hangs every call, the
operator's draft included. The draft runs inside `prepare_handoff`, which then hit the node's own
timeout, and that node has no fallback, so the run failed and the merchant got a 500 instead of
the handoff notice.

The fix is a per-call timeout (`LLM_CALL_TIMEOUT_S`, 20 s by default), below the node's 45 s:

- a hung call now fails like any vendor error;
- the router, knowledge and support nodes retry it and then escalate;
- the best-effort calls degrade (no draft, the operator's own text);
- the node timeout stays as the backstop.

`test_otel.py` pins it: with the model hanging, the turn still ends in the escalation notice.
