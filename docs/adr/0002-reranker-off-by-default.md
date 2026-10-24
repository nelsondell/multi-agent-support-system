# ADR 0002: Reranker measured, off by default (T-53, accepted)

**Context.** Hybrid retrieval fuses BM25 and dense rankings with RRF and takes the top 5. A
cross-encoder or a rerank API could reorder the fused candidates. The rule was set before
measuring: a reranker goes on only if it raises recall@5 or MRR and adds at most 1 s at p95.

**Decision.** RRF alone is the default; `RERANKER=cohere` turns Cohere's `rerank-v4.0-fast` on. It
reorders each query's 20 fused candidates, gets the masked query and public excerpts only, and
leaves the fused order in place when its key is missing or a call fails.

**Why off, although it met the rule.** On the 82 reviewed questions it took recall@5 from 0.939
to 0.963 and MRR from 0.851 to 0.894 at 618 ms p95 (`evals/rerank_benchmark.md`). That is 2 of
82 questions. It adds a vendor that would need compliance approval at an acquirer before any
merchant query reached it. And a trial key allows 10 calls a minute, so under load the default
would fall back to RRF anyway.

**Considered.** `BAAI/bge-reranker-v2-m3` and `Qwen/Qwen3-Reranker-0.6B` order almost as well
but take 5.7 s and 11.8 s at p95 on the CPU the container has; an image built with
`--build-arg RERANKER=bge` or `qwen` carries torch and their weights.

**Revisit** when a paid key and the vendor's approval exist, or when the retrieval set shows a
larger gap than two questions.
