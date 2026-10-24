# Evals

| Dataset | Size | Check | Last result | Command |
| --- | --- | --- | --- | --- |
| `trace_requests.jsonl`, sampled requests (scenarios, follow-ups, compound, account, product, troubleshooting, general, edge, adversarial) | 100 | no internal jargon in the answer (code) | 99/100 | `make eval-offline` |
| same | 5 tagged | small talk answered, not escalated or searched (code) | 5/5 | `make eval-offline` |
| same | 8 with an expected outcome | the plan fits the request: status, escalation, plan or source (code) | 7/8 | `make eval-offline` |
| same | 4 day-dependent web answers | the date notice matches the evidence: a date when the cited page has one, "could not confirm" only when none in range is named (code) | 3/4 | `make eval-offline` |
| same | 44 answered from the site or web | does not call missing what the evidence has (binary judge, `claude-opus-5-5`) | 41/44 | `make eval-offline` |
| same | 100 | pass-all: no applicable check fails | 94/100 | `make eval-offline` |
| `red_team.jsonl`, OWASP Agentic ASI01–ASI10, 10 adaptive, web and corpus injection planted | 40 | safe: no other merchant's data, prompt text, card number, write or planted text (code) | 40/40 | `make eval-offline` |
| `traces.csv` + `labels.csv`, the labelled traces | 100 (5 positive) | the judge above against the labels, stratified train/dev/test | **not validated**: TPR 1/2, 1/2, 0/1 on 5 failures; TNR 38/38, 27/28, 29/29 | `make eval-offline` |
| `heldout_requests.jsonl` → `heldout_traces.csv` + `heldout_labels.csv`, requests no prompt was tuned on (T-57) | 80 (25 bait) | the owner's pass or fail, blind; the `missing_info` judge against those labels | pass **63/80**: bait 21/25, the rest 42/55; judge **not validated**: no labelled `missing_info` failure, TNR 78/80 | `uv run python -m evals.run_heldout` |
| `routing_golden_set.jsonl` | 28 | guardrail outcome (code); intent and plan (live model) | guardrail 28/28 | `uv run python -m evals.run_routing_eval` |
| `retrieval_calibration.jsonl` | 97 (82 in-domain, 15 out) | embedder separation and threshold | [`retrieval_calibration.md`](retrieval_calibration.md) | `uv run python -m evals.run_retrieval_calibration` |
| same, in-domain, with gold chunk ids drafted by a reasoning model; every row reviewed by the owner, 0 edits ([`retrieval_review.csv`](retrieval_review.csv)) | 82 | recall@5, MRR and added latency, RRF alone against three rerankers | [`rerank_benchmark.md`](rerank_benchmark.md) | `uv run --group rerank python -m evals.run_rerank_benchmark` |
| `planning_set.jsonl` | 20 | task completion against `MAX_PLAN_CHANGES=0` (code) | [`planning_report.md`](planning_report.md): 32/60 against 19/60 over three runs | `uv run python -m evals.run_planning_eval` |

Last results are from `evals/eval_report.md`, recorded live on 2026-10-23 with `make eval`.

## How it gates PRs

- **Live run.** `make eval` sends every request through the real API, on the real model,
  retrieval, web search and judge, and records each answer in `recording.jsonl`, keyed by what
  was asked. `--update` calls the vendors only for what the recording lacks.
- **Replay.** `make eval-offline` answers the same requests from that recording, with no key and
  no network. It is part of `make verify`, which CI runs on every push, with the network cut on
  ubuntu.
- **Thresholds.** A rate below `thresholds.toml` fails the build. The floors are the accepted
  recording's own rates, so any drop in the replay is a regression. So does a request the recording
  never saw, with the instruction to record again.
- **Reporting.** Reports give the pass-all rate and each check's rate. There is no weighted score.

## Where the checks come from

Error analysis came first (`failure_modes.md`):

1. `scripts/sample_traces.py` ran these 100 requests on the real model and exported masked traces
   to `traces.csv`.
2. Each trace got pass or fail and a note (`labels.csv`), and the notes were grouped into five
   failure modes.
3. The obvious failures were fixed directly:
   - jargon, through the answer prompts;
   - small talk, through a scope reply;
   - the date notice, which now shows the day a cited page names when the provider gives no
     structured date;
   - the offline terminal, and requests for a person or an account action, through the router and
     the support planner.
4. Each mode then got one check. Code decides where it can; the one mode code cannot decide gets a
   binary judge from another model family than the chat model.

**The labels in `labels.csv`** were drafted by a reasoning model, run with written criteria; every
row reviewed by the owner, 0 edits.

**The held-out labels in `heldout_labels.csv`** were labelled by the owner, blind, no model draft,
on 80 requests no prompt was tuned on (`heldout_report.md`). The 94/100 is the gated checks on
traces the prompts were then fixed against; by their own labels, before the fixes, those traces
scored 73/100, and the held-out's 63/80 sits between the two. Most held-out failures are ones no
check sees: 11 answers that keep pointing at "o site" (the jargon check looks for "trecho" and field
names, and passes all 80), then 4 plan and 2 wrong-fact failures.

**The judge is not validated.** The labels hold 5 failures of its mode, 1 in the held-out split,
and a TPR needs at least 10 there (the report says so whenever that is the case). It runs as a
regression check only. It misses 3 of the 5, and reviewing them is the first step before trusting
it:

- **K02:** the note describes a different failure (it offers nothing of what the site does cover).
- **S05 and E08:** the visible evidence, cut at 1,500 characters in the export, may not hold the
  fact.

Its one false positive, K14, reads like a real failure that the labels mark as a pass.
