# ADR 0008: Considered, not doing (T-55, accepted)

| Option | Why not now |
| --- | --- |
| Open agent loop | Paths stop being enumerable and tested, and an untrusted page could steer what runs next (OWASP ASI01). The dependent requests it would cover are covered by bounded replanning ([ADR 0001](0001-bounded-replanning.md)). |
| MCP server | The tools are three reads and one write, in process, with identity injected from state. Exposing them adds an authenticated surface with no consumer, and LangChain's MCP adapter is still beta. |
| Kubernetes | `make scale` shows replicas over Postgres behind a balancer. One service with one database gains nothing from a cluster, and the digest-pinned image runs on any orchestrator. |
| Model cascade | `gpt-6-luna` at low effort matched `gpt-6-sol` on routing, 54/56 each, at about 1/19 of the cost (`evals/router_benchmark.md`). There is no cheaper tier worth falling back from. |
| Semantic cache | Answers depend on the merchant's account and on the day, so a near-duplicate question can need a different answer. Static prompt prefixes already use the provider's prompt cache. |

Each is revisited if its reason stops holding: a consumer for the tools, a second service, a
cheaper model that passes the routing set, or questions that do not depend on the merchant.
