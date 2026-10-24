# Failure modes

Error analysis, T-51 session 1:
- **Traces:** `scripts/sample_traces.py` ran the 100 requests in `evals/trace_requests.jsonl`
  through `/chat` on `gpt-6-luna` on 2026-10-22, and exported them masked to `evals/traces.csv`.
- **Labels:** each trace was labelled pass or fail with a one-line note (`evals/labels.csv`).
- **Clustering:** the notes were grouped by the first problem in each failure.

**Who labelled.** Each trace's label and note in `evals/labels.csv` were drafted by a reasoning
model, run with written criteria; every row reviewed by the owner, 0 edits.

**Result:** 27 of 100 traces fail.

| Mode | Fails | Traces | Example note |
| --- | --- | --- | --- |
| 1. Internal jargon reaches the merchant | 8 | S06, F08, K03, K12, K14, K15, X08, X14 | "falou em 'trechos' para o cliente" |
| 2. The plan does not fit the request | 5 | C08, D12, E08, K07, R06 | "não propôs o chamado com a maquininha offline depois dos passos básicos" |
| 3. Small talk or a question about the assistant goes to a person or to the web | 5 | E01, E02, E09, E11, E12 | "escalou um simples 'oi' para atendente" |
| 4. Says the information is missing when the evidence has it | 4 | K02, K04, S05, S07 | "disse que o site não informa o prazo do crédito, mas a evidência traz até 2 dias úteis" |
| 5. The web answer's freshness notice is wrong or missing | 3 | G02, G03, S02 | "disse que não confirmou a data de publicação, mas a evidência traz 22/10/2026" |
| Other | 2 | E10, K10 | K10 credits the Get Smart's CNPJ rule to the Get Clássica; E10 gives no total (summing is forbidden by design) and does not say which period it read |

## Notes on each mode

1. **Jargon.** "Trecho" appears in 10 answers, all of them corpus or web answers:
   - the 8 above, where it is the problem;
   - S05 and S07, where it is a second problem.
   
   One pass (X04) says "resultado".
2. **Plan.**
   - D12: a terminal still offline after the basic steps was routed to knowledge, so the ticket of T-50 was never proposed.
   - K07: knowledge listed every model's fee and asked for no account lookup, so T-52's replanning did not fire.
   - C08: a compound request with a weather question escalated instead of searching.
   - E08: four questions were merged into one step.
   - R06: the card's chip was read as the terminal's SIM chip.
3. **Small talk.**
   - "oi", "?" and "obrigado" escalate as low router confidence.
   - "você é um robô?" and "me conta uma piada" go to the web search.
4. **Missing when present.** The model answered with coverage `partial` or `none` although the
   excerpts held the fact. It is the opposite of hallucination, and it costs a resolution.
5. **Freshness.** The notice uses only the search provider's structured publication date, never a
   date read from page text. That is deliberate, since pages are untrusted, but those results
   carried no structured date. G03's sources also disagreed on the last match.

Notes on passes that matter for the next steps:
- **Truncated evidence (9 passes).** The note says a fact could not be verified, because the
  export cuts the evidence at 1,500 characters. A judge needs the whole evidence.
- **Borderline escalations (K09, R03).** The site answers both questions, but they escalated.
- **Smaller issues:**
  - D02 shows internal transaction ids;
  - F07 repeats the previous turn;
  - E03 says "não consegui resolver" to a merchant who only asked for a person.

## After the fixes

`evals/eval_report.md` reports the recording of knowledge prompt v9 with RRF, on 2026-10-23
(T-53).

| Mode | What changed | Check | Rate now |
| --- | --- | --- | --- |
| 1. Jargon | The answer prompts forbid "trecho", "resultados", "consulta" and field names | code: no jargon in the answer | 99/100 (X08) |
| 2. Plan | Router and support planner rules: a terminal still offline after the basic steps proposes the ticket (D12 passes); a request for a person or for an account action escalates as an individual case | code: expected status, escalation, plan or source | 7/8 (C08: a compound plan's weather step still reads the corpus, by design one intent per plan) |
| 3. Small talk | `other` now means small talk, answered with what the assistant can help with | code: answered, not escalated, no web search | 5/5 |
| 4. Missing when present | T-53's pages fixed S04 and K04; knowledge prompt v9 then states a fact whenever an excerpt has it and names every difference in a comparison (below) | binary judge, `claude-opus-5-5`, not validated (5 labelled failures) | 41/44 (S07, R06, E08) |
| 5. Freshness | Fixed: the provider never fills a structured date for general searches, so code now shows the day the cited page names, labelled as the source's own claim (a news-topic search was tried first and reverted: dated pages without the forecast) | code: the notice matches the evidence, measured on answers that came from the web | 3/4 (S02 cites the one page with no date while another result names one) |

## Mode 4 in the knowledge prompt (T-53)

| Knowledge prompt, with RRF | pass-all | missing info | jargon |
| --- | --- | --- | --- |
| v7 | 93/100 | 41/46 (S07, F11, C04, R02, R06) | 100/100 |
| v8: "the site does not state" only when no excerpt has the fact asked | 95/100 | 39/42 (S01, S04, S07) | 99/100 (S01) |
| v9, accepted: v8 plus every difference in a comparison | 94/100 | 41/44 (S07, R06, E08) | 99/100 (X08) |

**Open failures in the v9 recording:**
- C08: plan;
- S02: freshness;
- S07: missing info, from the web prompt, which is unchanged;
- E08: missing info;
- X08: jargon ("o trecho");
- R06: missing info. The owner reviewed its label and kept it.

The prompt fixes were made against these same 100 traces, so they are no longer a held-out test;
the red team and the routing golden set are untouched.

## Held-out set (T-57)

80 requests no prompt was tuned on, run on the accepted version and labelled by the owner, blind, no
model draft (`evals/heldout_labels.csv`). Weather and exchange-rate questions answered from the web
pass, as the challenge's spec asks.

| Slice | Pass |
| --- | --- |
| all labelled | 63/80 (79%) |
| failure mode 4 bait | 21/25 (84%) |
| the rest | 42/55 (76%) |

The labeller's modes: jargon 11, plan 4, wrong fact 2; no `missing_info`.

The 94/100 is the gated checks on traces the prompts were then fixed against; by their own labels,
before the fixes, those traces scored 73/100, and the held-out's 63/80 sits between the two. Most
held-out failures are ones no check sees: 11 answers that keep pointing at "o site" (the jargon
check looks for "trecho" and field names, and passes all 80), then 4 plan and 2 wrong-fact failures.
