# ADR 0004: Hybrid retrieval fused by rank (T-08, T-29, T-53, accepted)

**Context.** The corpus is Brazilian Portuguese, a merchant may write in English, and product
pages are nearly identical ("Get Smart" against "Get Mini"). The agent must abstain when the site
does not answer.

**Decision.** BM25 and dense vectors (`text-embedding-3-large`), fused with Reciprocal Rank
Fusion (k = 60) by position only. A comparison adds one search per item, and those rankings are
interleaved. Abstention keys off the best dense similarity, 0.50, the midpoint of the calibrated
gap. A reranker is optional ([ADR 0002](0002-reranker-off-by-default.md)).

**Numbers.**
- **Calibration:** on 97 questions the large embedder separates in-domain from out-of-domain
  questions (AUC 1.000, gap 0.067). Chroma's local model does not (gap −0.201).
- **Retrieval:** on 82 questions with reviewed gold chunks, RRF alone reaches recall@5 0.939 and
  MRR 0.851.

Sources: `evals/retrieval_calibration.md`, `evals/rerank_benchmark.md`.

**Considered.** Dense only (blurs product pages); BM25 only (no cross-language match); fusing
raw scores (incomparable scales); fusing every per-item list at once (measured: generic offer pages
crowded out each product's own page).
