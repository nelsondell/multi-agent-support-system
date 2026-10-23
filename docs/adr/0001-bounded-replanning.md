# ADR 0001: Bounded replanning, not an open agent loop

- **Status:** accepted, pending the planning eval (see Validation)
- **Date:** 2026-10-21
- **Task:** T-52

## Context

The router plans a turn up front: one step, or two for a compound request. That covers most
requests. It misses the ones whose next step depends on what a step finds. "Quanto custa a
mensalidade da minha maquininha?" is a knowledge question, but the published fee depends on the
terminal model, and only the merchant's account says which model it is. "Minha maquininha está
funcionando? Se não, o que faço?" reads the account first, and the procedure depends on what the
account shows. The router cannot know either dependency before a specialist looks.

The usual answer is an agent loop: a model picks the next action after every observation until it
decides it is done. This system answers merchants of a payments acquirer, and three properties
of the domain weigh against that:

- **Every path must be auditable and testable.** A loop's path is decided at run time by a model,
  so the set of paths a test suite can pin down is open.
- **Content is untrusted.** Web pages and, in principle, corpus pages can carry instructions
  (OWASP ASI01). In a loop, any observation can steer the next action.
- **Cost and latency must be bounded per turn.** A loop stops when the model says so.

## Decision

Keep the router's plan, and add agency only at the one point where the next step depends on a
finding. Split who decides what:

- **A specialist decides that something is missing.** Its structured answer may carry a `Need`: a
  question only the other specialist can answer. This is the model's judgment, made where the
  evidence is.
- **Code decides how the plan changes** (`app/graph/supervisor.py`):
  - **Add.** The step for the need goes right after the current one, at most `MAX_PLAN_CHANGES`
    (3) times a turn. It is never added for a step that did not answer, for the same specialist,
    or when it repeats a step already planned.
  - **Drop.** An added step that does not answer from a read is dropped. The answer stands on the
    steps that did, as it would have without the addition.
  - **Stop.** Before each step, the turn's budgets are checked: `MAX_HOPS`, `MAX_MODEL_CALLS`
    (12) and `MAX_COST_USD` (0.02). A spent turn starts no more steps. Its answered part goes out,
    the router's remaining steps go to a person as `budget_exhausted`, and the log names the
    budget that stopped it.
- **An added step only reads.**
  - A write it proposes never reaches the merchant.
  - A knowledge step added this way searches the corpus, never the web, because its question may
    carry what the account showed.
  - A web answer's `needs` is ignored, so a page can never grow the plan.
  - Only code sets `requested_by`: the field is absent from the router's schema, and the router
    clears it if a vendor sends it anyway.

The supervisor never rewrites a step the router planned. Its only rewrite would be a model
re-deciding the merchant's question, and the router already does that once, with its identifier
check.

## Consequences

- **The paths stay enumerable.** The router's plan, plus at most three read steps that a finding
  added, plus a stop. `tests/test_replanning.py` covers each rule.
- **A request that needs nothing pays almost nothing.** No extra model call; the schemas have one
  more optional field.
- **A request that needs a lookup pays for it.** The added step's calls (two for support, one for
  knowledge), plus a compose call to join the answers.
- **The budget is soft by one step.** It is checked between steps, so a turn can go over by the
  calls of the step already running. The exit path (compose, the handoff draft, memory) always
  runs, so a stopped turn still reaches a person.
- **Budgets are per graph run.** They are read from a spend opened around each `/chat` turn and
  each operator resume. A turn that paused for a person starts a fresh budget when the operator
  answers; by then a person has taken over.

## Alternatives considered

- **An open ReAct-style loop over both specialists.** Rejected for the three reasons above. Its
  flexibility is what this domain cannot audit.
- **A model call that replans after every step.** It is more flexible than code, but it adds a
  call to every multi-step turn. It would also put step answers, including account data and page
  content, into the planner's input, which widens the injection surface for little gain over the
  one revision these requests need.
- **A larger router plan.** Letting the router plan three steps does not help: the router cannot
  know the dependency before a step finds it.
- **Independent steps in parallel with `Send`.** Deferred. The T-52 brief allows it only where the
  eval shows a latency gain, and the eval has not run yet.

## Validation

`evals/run_planning_eval.py` runs `evals/planning_set.jsonl`, 20 requests whose next step depends
on a finding, through the real model twice: with `MAX_PLAN_CHANGES=0`, the router's plan as it is
and the baseline, and with the default. It measures task completion (checked in code), steps,
model calls, p50 and p95 latency, the p95 of simple requests, and cost, and writes
`evals/planning_report.md`.

Replanning stays on by default only if it completes more tasks without worsening the p95 of
simple requests. If the p95 gets worse, the default becomes `MAX_PLAN_CHANGES=0`, behind the
flag, and this ADR records the numbers. At the time of writing the eval has not run, because no
valid model key was available. Until it does, the default is a decision this ADR still owes
evidence for.
