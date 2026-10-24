# Planning eval

`uv run python -m evals.run_planning_eval` on 2026-10-23, at `400fa41` plus the changes committed with this report, on `gpt-6-luna`: 20 requests that depend on a finding and 11 simple ones, latency and cost per request.

| Mode | Completed | Steps | Model calls | p95 | p95, simple | USD |
|---|---|---|---|---|---|---|
| MAX_PLAN_CHANGES=0 | 7/20 | 1.00 | 2.60 | 11.97 s | 7.20 s | 0.00043 |
| MAX_PLAN_CHANGES=3 | 10/20 | 1.45 | 3.90 | 15.08 s | 12.46 s | 0.00056 |

The same command run twice more that night, for the spread:

| Run | Mode | Completed | Steps | Model calls | p95 | p95, simple | USD |
|---|---|---|---|---|---|---|---|
| 1 | MAX_PLAN_CHANGES=0 | 7/20 | 1.00 | 2.60 | 11.97 s | 7.20 s | 0.00043 |
| 1 | MAX_PLAN_CHANGES=3 | 10/20 | 1.45 | 3.90 | 15.08 s | 12.46 s | 0.00056 |
| 2 | MAX_PLAN_CHANGES=0 | 5/20 | 1.05 | 2.80 | 13.72 s | 7.07 s | 0.00046 |
| 2 | MAX_PLAN_CHANGES=3 | 11/20 | 1.50 | 4.10 | 14.07 s | 9.15 s | 0.00056 |
| 3 | MAX_PLAN_CHANGES=0 | 7/20 | 1.05 | 2.65 | 17.81 s | 8.37 s | 0.00043 |
| 3 | MAX_PLAN_CHANGES=3 | 11/20 | 1.50 | 3.75 | 18.33 s | 7.64 s | 0.00058 |

Across the three runs, replanning completes 32/60 against 19/60. The p95 of the 11 simple requests
moved +5.3 s, +2.1 s and −0.7 s: one or two slow requests set it. Replanning stays on, by the
owner's call recorded in `docs/adr/0001-bounded-replanning.md`.

## Why replanning still misses

A diagnostic pass ran the 20 dependent requests three more times with replanning on. Code
classified each of its 19 misses:

| Cause | Misses | Example |
| --- | --- | --- |
| answered from the site without the account fact that decides it: no step was added | 9 | P17 never reads the terminal's status, which the account shows active |
| escalated: an individual case (4), the same misroute twice (3), low retrieval confidence (1) | 8 | P13: the published duplicate-sale rule read as an individual case, 3 of 3 |
| quoted another terminal model's value | 2 | P06 and P07 list every model's exemption, the Get Smart's 60 mil included, instead of the merchant's own |

That pass completed 41/60, more than any of the three runs above: the spread between runs is wide.

