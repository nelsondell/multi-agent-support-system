# ADR 0003: LangGraph for the orchestration (T-05, T-48, accepted)

**Context.** Four agents run a plan of one or two steps. A step may pause for a person, and the
pause must survive a restart and a replica. Nodes need retries and timeouts, and every path must
be testable without a model.

**Decision.** A LangGraph `StateGraph` over a Pydantic state. The checkpointer (SQLite, or
Postgres for replicas) is attached at `compile()`. Human review pauses with `interrupt()`. Every
node runs under LangGraph's `RetryPolicy`, `TimeoutPolicy` and an error handler, and every
decision a model makes is structured output.

**Consequences.**
- **Policy is code.** Thresholds and budgets are conditional edges and the supervisor, not prompt
  text.
- **Retries are LangGraph's own.** T-48 deleted the hand-written retry and timeout code.
- **A paused node re-runs on resume,** so its side effects happen before the pause.
- **Checkpoint types are restricted.** The checkpoint decoder would import any type a checkpoint
  names, so `app/checkpoints.py` allows only the state's own.

**Considered.**
- **A hand-written state machine** would rebuild checkpoints and interrupts.
- **LangChain agents and Deep Agents** are open loops, and Deep Agents is still beta.
- **Another framework** would add a second runtime for the same graph.
