# Eval report

`uv run python -m evals.run_evals --live` on 2026-10-23, at `5454791` plus the changes committed with this report. Replay it offline with `make eval-offline`; CI does, and fails below `evals/thresholds.toml`.

## traces

| Check | Passed | Rate | Failing |
| --- | --- | --- | --- |
| jargon | 100/100 | 100% | — |
| small_talk | 5/5 | 100% | — |
| plan_fit | 7/8 | 88% | C08 |
| freshness | 3/4 | 75% | S02 |
| missing_info | 41/43 | 95% | F11, R02 |
| pass_all | 96/100 | 96% | — |

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

## Held-out set (T-57)

From `evals/heldout_report.md` (`uv run python -m evals.run_heldout`): 80 requests no prompt was
tuned on, labelled by the owner, blind, no model draft. Pass **63/80** (79%): failure mode 4 bait
21/25, the rest 42/55. The `missing_info` judge is **not validated** on them: the labels hold no
failure of its mode (TP 0, FN 0), and it called 2 of 80 answers failures that the labels pass (TNR
78/80).

## Speaking as the acquirer (T-60)

The jargon check also fails an answer that credits the site, a page or the sources. On the held-out
requests, run live before and after knowledge prompt v11, such answers went from 7/80 to 0/80
([failure modes](failure_modes.md#speaking-as-the-acquirer-t-60)).
