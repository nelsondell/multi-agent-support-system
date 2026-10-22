# ADR 0001: Bounded replanning, not an open agent loop (T-52, accepted)

**Context.** The router plans a turn up front, so it misses requests whose next step depends on
what a step finds: "quanto custa a mensalidade da minha maquininha?" needs the terminal model,
which only the account has. An open agent loop would cover them, but here every path must be
testable, untrusted pages must not steer actions (OWASP ASI01), and cost per turn is bounded.

**Decision.** A specialist says what is missing (a structured `Need`); code decides how the plan
changes (`app/graph/supervisor.py`): it adds one read step, at most `MAX_PLAN_CHANGES` a turn,
drops an added step that does not answer, and stops the turn on `MAX_HOPS`, `MAX_MODEL_CALLS` or
`MAX_COST_USD`, handing the rest to a person. An added step never writes or searches the web; a
web answer never adds one; only code marks a step as added.

**Consequences.** Paths stay enumerable and tested (`tests/test_replanning.py`). A request that
needs nothing costs no extra call. Budgets are checked between steps, so a turn can exceed one by
the step already running.

**Considered.** An open ReAct loop (paths not auditable); replanning by a model after each step
(an extra call, and page and account text in the planner's input); `Send` for parallel steps
(deferred until an eval shows a latency gain).

**Validation.** `evals/run_planning_eval.py` runs 20 dependent requests against
`MAX_PLAN_CHANGES=0`. Replanning stays on only if completion rises without a worse p95 for simple
requests; otherwise the default becomes 0. Not run yet: no working model key.
