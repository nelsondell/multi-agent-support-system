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

`evals/eval_report.md` reports the recording of knowledge prompt v11 and web prompt v9, with RRF
and the per-query rounds of T-62.

| Mode | What changed | Check | Rate now |
| --- | --- | --- | --- |
| 1. Jargon | The answer prompts forbid "trecho", "resultados", "consulta" and field names; since T-60 the knowledge prompts speak as the acquirer and never credit the site (below) | code: no jargon in the answer, and no answer that credits the site, a page, the sources or the results (T-62) | 99/100 (G01) |
| 2. Plan | Router and support planner rules: a terminal still offline after the basic steps proposes the ticket (D12 passes); a request for a person or for an account action escalates as an individual case | code: expected status, escalation, plan or source | 7/8 (C08: a compound plan's weather step still reads the corpus, by design one intent per plan) |
| 3. Small talk | `other` now means small talk, answered with what the assistant can help with | code: answered, not escalated, no web search | 5/5 |
| 4. Missing when present | T-53's pages fixed S04 and K04; knowledge prompt v9 then states a fact whenever an excerpt has it and names every difference in a comparison (below) | binary judge, `claude-opus-5-5`, not validated (5 labelled failures) | 41/43 (F11, R02) |
| 5. Freshness | Fixed: the provider never fills a structured date for general searches, so code now shows the day the cited page names, labelled as the source's own claim (a news-topic search was tried first and reverted: dated pages without the forecast) | code: the notice matches the evidence, measured on answers that came from the web | 3/4 (S02 cites the one page with no date while another result names one) |

## Mode 4 in the knowledge prompt (T-53)

| Knowledge prompt, with RRF | pass-all | missing info | jargon |
| --- | --- | --- | --- |
| v7 | 93/100 | 41/46 (S07, F11, C04, R02, R06) | 100/100 |
| v8: "the site does not state" only when no excerpt has the fact asked | 95/100 | 39/42 (S01, S04, S07) | 99/100 (S01) |
| v9, accepted: v8 plus every difference in a comparison | 94/100 | 41/44 (S07, R06, E08) | 99/100 (X08) |

**Open failures in the accepted T-62 recording:**
- C08: plan;
- S02: freshness;
- F11 and R02: missing info;
- G01: jargon ("Os resultados não informam as temperaturas"), flipped by resampling (T-62 below).

S07, E08, X08 and R06, open in v9, pass since v11.

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

When the held-out was labelled, the tuning traces passed 94/100 by the gated checks; by their own
labels, before the fixes, they scored 73/100, and the held-out's 63/80 sat between the two. Most
held-out failures were ones no check saw then: 11 answers that kept pointing at "o site" (the jargon
check looked for "trecho" and field names, and passed all 80; T-60 below extends it), then 4 plan
and 2 wrong-fact failures.

## Speaking as the acquirer (T-60)

The held-out labels found answers that credit the site ("o site informa", "o site não informa")
instead of stating the fact, and the jargon check passed them. The check now also fails an answer
that credits the site, a page or the sources (`SITE_VOICE` in `checks.py`), in the phrasings the
tuning traces show; the source-date notices the system appends do not trip it. On the labelled
held-out export it flags 13 answers: 9 of the owner's 11 jargon failures and 4 of the other 69.

The knowledge prompt was then changed against the tuning traces only, with web prompt v8 carrying
the same rule:

| Knowledge prompt, re-recorded | jargon | missing info | pass-all |
| --- | --- | --- | --- |
| v9 (`main`), replayed with the new check | 89/100 | 41/44 | 87/100 |
| v10: speaks as the acquirer ("A Get Smart custa..."); a missing fact is "Não há informação sobre..." | 96/100 (S01, F07, K02, K12: "os trechos" became the subject) | 40/43 | 91/100 |
| v11, accepted: v10, and never the site, a page, a manual or the excerpts as the subject | 100/100 | 41/43 (F11, R02) | 96/100 |

The held-out requests ran live once before and once after, through the code checks only; no answer
was read. A held-out run on v10 was stopped unread, so it could not steer v11.

| Held-out, live | answers that credit the site | jargon check | freshness | safe | sent to a person |
| --- | --- | --- | --- | --- | --- |
| before: knowledge v9, web v7 | 7/80 | 73/80 | 0/2 | 80/80 | 5 |
| after: knowledge v11, web v8 | **0/80** | 79/80 | 0/1 | 80/80 | 6 |

The jargon failure left (H24) is the older pattern, not the site. The labelled export, recorded
earlier on v9, has 13 answers that credit the site: the count varies between runs. T-60 was
prompted by the held-out's labels, so for this mode the held-out is no longer untouched; its 63/80
stays the measure of the version it labelled.

## Comparisons and exchange rates (T-62)

The live demo on #12's dependencies answered two challenge scenarios wrong:
- **S01 left out the Get Smart price.** With the router's searches ("maquininha Get Clássica",
  "maquininha Get Smart"), the merge interleaved three rankings and cut at five, so the last
  item's search kept one slot (the Get Smart manual), and its second hit, the Get Smart page with
  R$ 129,90 and the exemption tiers, was cut. The merge now runs in rounds: every query adds its
  best hit not yet taken, for the same number of rounds (six hits for three queries). recall@5 on
  the 82 in-domain questions is 0.939 (77/82) before and after, with MRR 0.851; they search with
  one query, a path the change keeps. I01 with the router's searches now brings both gold pages.
- **S07 quoted the euro as 1,1203, with no currency** (EUR/USD). Web prompt v9 quotes "1 EUR =
  R$ X" from a result in reais, or says none was found, and the `fx` check fails an exchange-rate
  answer with no "R$" that is not a not-found (S07 and G02 are tagged). "em reais" in the router's
  query was tried and reverted: a router change re-records every route.
- A forecast gives the maximum and minimum from an hourly table, and the jargon check also fails
  "os resultados/os dados não informam".

Every change to the web prompt resamples every web answer. Each row re-recorded from the v11
recording:

| Recording | pass-all | missing info | jargon | Failing |
| --- | --- | --- | --- | --- |
| v11 (`main`) | 96/100 | 41/43 | 100/100 | F11, R02 |
| web v9 and router v12 ("em reais") | 93/100 | 39/44 | 100/100 | S07 (the judge read the system's date notice), F11, K04, R02, R06 (a connection error was recorded) |
| web v9, router reverted | 95/100 | 40/43 | 100/100 | F11, R02, G01 |
| plus "look in every result" | 95/100 | 40/43 | 100/100 | F11, R02, G01 |
| **plus maximum and minimum from the hourly table, accepted** | **95/100** | **41/43** | **99/100** | F11, R02, G01 (jargon) |

The jargon of the first four rows is counted without "os resultados". The trade: S01 and S07 are
fixed, and G01, a weather question, flipped by resampling ("Os resultados não informam as
temperaturas"). The floors are set from the accepted recording: pass-all 0.95, jargon 0.99,
missing info 0.95, `fx` 1.0.

Live, three runs each, on 2026-10-25, a Sunday:
- **S01:** the Get Smart price, R$ 129,90, in 3 of 3.
- **S07:**
  - run 1: "1 EUR = R$ 5,6422, no fechamento de 23/10/2026", saying today's rate is not given;
  - run 2: not found;
  - run 3: "Hoje, 1 EUR = R$ 5,6422", Friday's close called today.

  The freshness check fails runs 1 and 3: the notice says the date could not be confirmed while
  the page names it. No run quoted a rate without the real.

The held-out requests ran once, at the end, through the code checks:
- jargon 80/80 with the extended pattern, and no answer credits the site;
- freshness 1/1, small talk 4/4, safe 80/80;
- 7 sent to a person;
- no held-out request asks for an exchange rate, so `fx` did not apply.

## Amounts in reais (T-65)

A probe after the T-61 demo answered "1 EUR = R$ 1,1205" from a table of dollar rates. Every
digit was on the page, so neither the number guard nor the `fx` check, which only looked for "R$",
stopped it. Code now rejects a web answer that gives an amount in reais no cited result writes next
to "R$", "real", "reais" or "BRL", and returns the not-found message; `fx` applies the same rule.
The recorded answers already met it, so the replay is unchanged: pass-all 95/100, `fx` 2/2.
