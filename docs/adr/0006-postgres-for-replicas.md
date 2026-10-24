# ADR 0006: Postgres for replicas, SQLite for one node (T-48, accepted)

**Context.** V4 named horizontal scale and HA as a gap: conversations, cases and turn locks lived
in SQLite files on one node.

**Decision.** `STORAGE=sqlite|postgres`. Postgres holds three things that every replica shares:
- the checkpoints (`AsyncPostgresSaver`);
- the handoff queue;
- the per-conversation lock (`pg_advisory_xact_lock`, on its own pool).

SQLite stays the default for one node. `make scale` runs Postgres, two replicas and a round-robin
balancer. The knowledge index stays per node: it is read-only and rebuilt from the committed
snapshot.

**Evidence.** `tests/test_replicas.py` runs in CI over a Postgres service, with three tests:
- a case escalated on one replica is answered on the other;
- a turn whose replica dies resumes from its checkpoint;
- turns on one conversation serialize across replicas.

**Considered.**
- **Sticky sessions:** they do not survive a dead replica.
- **Redis for the locks:** a second store for something Postgres already does.
- **A shared Chroma server:** a read-only index needs no sharing.
