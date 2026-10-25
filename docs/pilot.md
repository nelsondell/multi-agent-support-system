# Pilot plan

The assistant goes live one intent at a time, in three stages. Each move is decided by numbers the
repository already measures: the eval gates, which CI replays on every release, and the
production metrics in [operations](operations.md).

| Stage | The merchant gets | A person |
| --- | --- | --- |
| **Shadow** | today's human answer; the assistant runs on a copy of the traffic and nothing it writes leaves | answers every contact; samples are labelled against the assistant's answer |
| **Assisted** | the assistant's answer, after an operator approves it, edits it or cancels it (the human-review flow, [human in the loop](human-in-the-loop.md)) | approves every answer |
| **Autonomous** | the assistant's answer, or a protocol when it escalates | answers escalations only |

**Order of intents.** The order follows what each intent touches (Meta's Rule of Two,
[security](security.md)). Public data comes first: `product_info`, then `general_info`. Then the
merchant's private data, read-only: `account_data`. Then `troubleshooting`, which can propose the
one write (a confirmed ticket). `individual_case` (refunds, chargebacks, blocks, fraud) never goes
autonomous: hard rule 3 sends it to a person.

## Gates, per intent

| Move | Gate, all must hold | Source |
| --- | --- | --- |
| into shadow | The release passes its eval gates: pass-all ≥ 0.95, missing info ≥ 0.95, jargon ≥ 0.99, exchange rates in reais, red team 40/40. The routing golden set scores ≥ 54/56 over two runs. | `evals/thresholds.toml`, `evals/eval_report.md`, `evals/router_benchmark.md` |
| shadow → assisted | ≥ 300 shadow turns of the intent. On a sample of 100 labelled with the T-51 criteria, pass-all ≥ 0.94. No answer reads another merchant's data. | `evals/failure_modes.md` (the labelling criteria), audit log |
| assisted → autonomous | Over 2 weeks, operators approve ≥ 90% of answers unedited, and the escalation rate is no higher than in shadow. Node p95 < 8 s; cost per resolution < US$ 0.01. No hard-rule violation in a 100-answer audit sample. | audit log (approve, reply, cancel); `support_turns_total{intent,outcome}`; the `NodeLatencyP95High` and `CostPerResolutionHigh` thresholds |

The 90% approval rate and the 300- and 100-turn samples are the pilot's own choices (assumptions),
to be tuned with the operations team.

## Rollback

An intent drops back one stage as soon as one of these fires:
- `EscalationSpike` (over 40% of turns to a person for 2 minutes), when its escalations come from
  the intent and not from a vendor;
- `CostPerResolutionHigh` or `NodeLatencyP95High` firing for more than an hour;
- a hard-rule violation confirmed in an audit sample.

**All intents go back to shadow** on any answer with another merchant's data, or any write without
its confirmation. A release whose eval replay falls below its gates never deploys: CI fails it.

## Cost and benefit, per month

| Input | Value | Source |
| --- | --- | --- |
| A. Model spend per resolved turn | US$ 0.00048 | **measured**: all spend over the 82 turns answered without a person, `evals/traces.csv` (100 requests on `gpt-6-luna`, 2026-10-22), at the list prices in `.env.example` |
| B. Embedding and web search spend per turn | US$ 0.001 | **assumption** (A covers chat tokens only) |
| C. Turns resolved without a person | 82% | **measured on the sample** above; the real mix comes from shadow |
| D. Contacts per month in the piloted intents | 100,000 | **assumption** |
| E. Cost of one contact a person handles | R$ 6.00 | **assumption** |
| F. Hosting, monitoring and on-call | R$ 3,000 | **assumption** |
| G. USD/BRL | 5.40 | **assumption** |
| **Contacts a person no longer handles** | D × C = 82,000 | |
| **Person cost avoided** | 82,000 × E = R$ 492,000 | |
| **Assistant cost** | 82,000 × (A + B) × G + F ≈ R$ 3,655 | |

The assumptions dominate the result, so it is illustrative. The pilot's job is to replace B to G
with Getnet's own numbers. Only A and C are measured, and C only on a sample. Latency on that
sample was 6.2 s at p50 and 15.1 s at p95 per turn, so the gate above applies per node, not per
turn.
