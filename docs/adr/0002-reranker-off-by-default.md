# ADR 0002: Hybrid retrieval fused by rank, reranker off by default (T-08, T-29, T-53, accepted)

**Context.** The corpus is Brazilian Portuguese, a merchant may write in English, product pages are
nearly identical ("Get Smart" against "Get Mini"), and the agent must abstain when the site does
not answer. A reranker goes on only if it raises recall@5 or MRR and adds at most 1 s at p95 per
search, a rule set before measuring.

**Decision.** BM25 and dense vectors (`text-embedding-3-large`) are fused with RRF (k = 60) by
position only; a comparison adds one search per item, interleaved. Abstention keys off the best
dense similarity, 0.50, the midpoint of the calibrated gap. RRF alone is the default;
`RERANKER=cohere` turns on `rerank-v4.0-fast` with the masked query and public excerpts, and a
missing key or a failed call keeps the fused order.

**Numbers.** On 97 questions the large embedder separates the domains (AUC 1.000, gap 0.067), the
local one does not (gap −0.201). On 82 questions with reviewed gold, RRF reaches recall@5 0.939 and
MRR 0.851; Cohere 0.963 and 0.894 at 618 ms p95; bge and Qwen order as well but take 5.7 and 11.8
s on the CPU (`evals/retrieval_calibration.md`, `evals/rerank_benchmark.md`).

**Why Cohere stays off though it met the rule.** The gain is 2 of 82 questions; it adds a vendor
that would need compliance approval at an acquirer; a trial key's 10 calls a minute would make the
default fall back under load anyway. Revisit with a paid key and approval, or a larger gap.

**Considered.** Dense only (blurs product pages); BM25 only (no cross-language match); raw-score
fusion (incomparable scales); fusing all per-item lists at once (generic pages won, measured).
