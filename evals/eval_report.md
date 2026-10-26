# Eval report

Recorded with `uv run python -m evals.run_evals --update` on 2026-10-25 (T-62), at `49f04ff` plus the changes committed with this report; the recording's answers are pinned to 2026-10-23, the day it began. Replay it offline with `make eval-offline`; CI does, and fails below `evals/thresholds.toml`.

## traces

| Check | Passed | Rate | Failing |
| --- | --- | --- | --- |
| jargon | 99/100 | 99% | G01 |
| small_talk | 5/5 | 100% | — |
| plan_fit | 7/8 | 88% | C08 |
| freshness | 3/4 | 75% | S02 |
| fx | 2/2 | 100% | — |
| missing_info | 41/43 | 95% | F11, R02 |
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

## Comparisons and exchange rates (T-62)

The merge of a comparison's searches keeps every item's own top hits, and an exchange rate is
quoted against the real or said not to be found (`fx`). S01 had the Get Smart price in 3 of 3 live
runs; S07 never quoted a rate without the real ([failure modes](failure_modes.md#comparisons-and-exchange-rates-t-62)).
