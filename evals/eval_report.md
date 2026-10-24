# Eval report

`uv run python -m evals.run_evals --live` on 2026-10-23, at `6f93231` plus the changes committed with this report. Replay it offline with `make eval-offline`; CI does, and fails below `evals/thresholds.toml`.

## traces

| Check | Passed | Rate | Failing |
| --- | --- | --- | --- |
| jargon | 99/100 | 99% | X08 |
| small_talk | 5/5 | 100% | — |
| plan_fit | 7/8 | 88% | C08 |
| freshness | 3/4 | 75% | S02 |
| missing_info | 41/44 | 93% | S07, R06, E08 |
| pass_all | 94/100 | 94% | — |

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
