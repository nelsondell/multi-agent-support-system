# Held-out report

`uv run python -m evals.run_heldout` on 2026-10-24. 80 of the 80 requests in `evals/heldout_requests.jsonl`, labelled by the owner, blind, no model draft (`evals/heldout_labels.csv`).

| Slice | Pass |
| --- | --- |
| all labelled | 63/80 (79%) |
| failure mode 4 bait | 21/25 (84%) |
| the rest | 42/55 (76%) |

Failures by the labeller's mode: jargon 11, plan 4, wrong_fact 2.

| Judge | TP | FN | TN | FP | TPR | TNR |
| --- | --- | --- | --- | --- | --- | --- |
| `missing_info` | 0 | 0 | 78 | 2 | — (no labelled failure of this mode) | 78/80 |

**not validated**: it needs at least 10 labelled `missing_info` failures and both rates at 0.8 or more; the labels hold 0.
