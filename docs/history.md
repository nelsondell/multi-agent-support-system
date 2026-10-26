# History

v1 was built in the challenge's 60-minute timebox and scored 8.7/10. v2 (T-15 to T-45) and v3
(T-47 to T-56, after the V4 evaluation) are outside that timebox, at the evaluator's invitation.
What v3 changed is in [the README's Since V4](../README.md#since-v4) and [REVIEW.md](../REVIEW.md).

## What changed since v1

31 tasks (T-15 to T-45), each one commit with a done criterion.

| Criterion (v1 score) | v2 | Tasks |
| --- | --- | --- |
| Memory / multi-turn (1.5) | Persistent threads keyed by merchant and conversation; follow-ups rewritten into standalone queries with history as data; rolling summary; history endpoint; survives restarts | T-19, T-20, T-34, T-42 |
| Human in the loop (5.8) | A fourth agent: case and draft before `interrupt()`; approve, reply or cancel; guarded rephrasing; reply delivered with the next message; notes while waiting; expiry; the plan finishes after the reply; the merchant confirms the one write action | T-21, T-42, T-43, T-50 |
| Infra / Docker (6.8) | One command from a clean clone; index built on first boot; separate state volume; healthcheck; CI builds and smoke-tests the container; `make verify` offline; SBOM, vulnerability and secret scans; digest-pinned base images; replicas over Postgres behind a balancer | T-16, T-17, T-18, T-33, T-47, T-48 |
| Product / completeness (7.5) | Web search for general questions, dated; compound requests; sale vs credit dates; declines explained from the data; partial answers | T-22, T-23, T-30, T-32, T-35, T-38, T-40 |
| Multi-agent (8.8) | Plans of up to two specialists joined by compose; a step that needs a person pauses the plan, which resumes after the operator; hop budget across the plan; bounded replanning: a finding adds a read step for the other specialist, within plan-change, model-call and cost budgets | T-21, T-22, T-43, T-52 |
| Observability / evals (9.1) | Error analysis with one check per failure mode and a 40-case red team, replayed in CI against thresholds; LLM judge over 3 runs; model benchmark; embedder calibration; OpenTelemetry GenAI traces; cost per resolution and cache hit rate; Grafana dashboard and four alerts with runbooks; vendor failures tested | T-25, T-26, T-28, T-29, T-39, T-44, T-51, T-54 |
| Tests / quality (9.2) | 78 → 379 tests; HTTP flows; concurrency; every invariant mapped to a test; suite isolated from `.env` and clock | T-24, T-31, T-44 |
| Architecture / code (9.5) | Same shape, no new framework; one vendor seam; structured output everywhere | every task |
| Security / isolation (9.5) | Merchant identity from a signed JWT; operator roles; append-only audit; LGPD erasure and retention; rate limit; threads bound to the merchant; restricted checkpoint deserializer; masked web queries | T-19, T-21, T-23, T-31, T-44, T-49 |
| RAG / knowledge (9.8) | Embedder chosen by calibration; per-product searches; three-state coverage; conditions stated with prices; FAQ category pages and terminal manuals for the answers left partial; rerankers benchmarked, one opt-in | T-29, T-33, T-37, T-53 |
| Trade-offs (10.0) | Each choice measured and committed: model, embedder, fusion, router effort | T-28, T-29, T-34, T-45 |

## Process

Spec-driven development with Claude Code: `specs/spec.md` (what), `specs/plan.md` (how) and
`specs/tasks.md` (one task per commit, each with a done criterion) were approved before code, and
`CLAUDE.md` carries the non-negotiables. Every commit follows `<type>(<T-id>): <imperative>`, so the
history is the task list. The v1 core went from scaffold to a packaged service in 60 minutes of
commits (T-00 to T-14). v2 came from the v1 evaluation (T-15 to T-27); three fix passes (T-28 to
T-39) followed from real runs in Docker, and later passes (T-40 to T-45) from reviews of the
result. Each finding became a task, and each decision kept its measurement in `evals/`.
