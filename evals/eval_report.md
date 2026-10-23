# Eval report

`uv run python -m evals.run_evals --live` on 2026-10-23, at `2dffed6` plus the changes committed with this report. Replay it offline with `make eval-offline`; CI does, and fails below `evals/thresholds.toml`.

## traces

| Check | Passed | Rate | Failing |
| --- | --- | --- | --- |
| jargon | 100/100 | 100% | — |
| small_talk | 5/5 | 100% | — |
| plan_fit | 7/8 | 88% | C08 |
| freshness | 3/4 | 75% | S02 |
| missing_info | 40/43 | 93% | S04, S07, K04 |
| pass_all | 95/100 | 95% | — |

## red_team

| Check | Passed | Rate | Failing |
| --- | --- | --- | --- |
| safe | 40/40 | 100% | — |
| pass_all | 40/40 | 100% | — |

## Judge: missing_info, against the labels

| Split | TPR | TNR |
| --- | --- | --- |
| train | 1/2 | 38/38 |
| dev | 1/2 | 27/28 |
| test | 0/1 | 29/29 |

**Not validated.** The labels hold 5 failures of this mode, 1 of them in the held-out split: a TPR needs at least 10 there. The judge runs as a regression check only.
