# RAG pipeline and web search

| Stage | What happens | Where |
| --- | --- | --- |
| **Ingestion** | 28 fetched pages and 3 PDF manuals → 275 documents → 270 chunks. Three routes, each confirmed against a real file: the FAQ (question and answer paired by `data-modal` id, on the main FAQ page and its category pages), the article layout (`.fl-col-content`) and the terminal manuals, read page by page. The category pages, the Pix page, two articles and the Get Smart, Get Clássica and Get Mini manuals were added for the answers the error analysis found partial (T-53); the manuals' error tables cover terminal troubleshooting. Extracted text goes to a committed snapshot, so a fresh clone builds the same index without the raw files. 600-token chunks with 100 of overlap, deduplicated by content hash (40 duplicates: the same body served under different routes). Four pages match no route and are reported, not dropped silently. | `app/rag/parse.py`, `chunk.py`, `ingest.py` |
| **Storage** | Chroma (cosine) for vectors, plus `chunks.jsonl` that BM25 is rebuilt from. Chunk ids are content hashes, so re-indexing is idempotent. The index lives in its own Docker volume. Ingestion keeps both halves or neither: a failed first boot leaves nothing a later boot could mistake for an index. | `app/rag/ingest.py` |
| **Retrieval** | Hybrid: dense vectors carry a question across languages, BM25 pins exact product names ("Get Smart" vs "Get Mini"). The two rankings are fused with Reciprocal Rank Fusion by position only. A comparison adds one search per product; their rankings are interleaved, best hit of each first. The whole question is always one of the queries. A reranker can then reorder each query's 20 fused candidates: off by default, `RERANKER=cohere` turns on Cohere's `rerank-v4.0-fast` with the query masked, and a missing key or a failed call keeps the fused order. Confidence is the best dense similarity any query reached, so a reranker never changes abstention; below 0.50 the agent abstains without a model call. | `app/rag/retrieve.py`, `rerank.py` |
| **Generation** | The model gets only the retrieved excerpts, each labelled with its url, and the financial hard rules, and returns a structured answer with `coverage`: `full`, `partial` (answer what is supported, name what the site doesn't cover) or `none` (abstain, a person takes it). A price that depends on a tier is stated with its condition. Sources are taken from what was retrieved, never from urls the model claims. | `app/graph/knowledge.py`, `app/prompts/knowledge.md` |

## Why these choices, with numbers

[`evals/retrieval_calibration.md`](../evals/retrieval_calibration.md) runs 82 questions the corpus
answers against 15 it doesn't, through three embedders. Chroma's local model can't separate them
(the best out-of-domain question outscores the worst in-domain one by 0.201);
`text-embedding-3-large` separates them completely with a gap of 0.067, so it is the default and the
threshold sits in the middle of that gap. The gap was 0.110 on the first 22 questions; the 60
added in T-53 brought the lowest in-domain score down to 0.532, and the threshold moved from 0.52 to
0.50. Fusing every query's list at once was measured and rejected for comparisons: RRF rewards
consensus, so generic offer pages crowded out each product's own page.

**Why RRF with BM25 instead of dense retrieval only?** They fail differently. Embeddings carry an
English question into Portuguese prose but blur near-identical product pages; BM25 pins "Get Smart"
against "Get Mini". RRF fuses them by rank, so their incomparable scores never meet. Dense
similarity is still what decides abstention, because a rank says nothing about whether the corpus
answers at all, and that signal was calibrated per embedder rather than assumed.

**Is a reranker worth it?** [`evals/rerank_benchmark.md`](../evals/rerank_benchmark.md) runs the 82
in-domain questions, with gold chunks drafted by a reasoning model and every row reviewed by the
owner (0 edits), through RRF alone and with three
rerankers over the same 20 fused candidates. The latency budget was set before measuring: at most
1 s added at p95 per search, an eighth of the 8 s at which the node latency alert fires.

| Reranker | recall@5 | MRR | Rerank p95 | Within the budget |
| --- | --- | --- | --- | --- |
| none, RRF alone | 0.939 (77/82) | 0.851 | — | — |
| Cohere `rerank-v4.0-fast` (API) | 0.963 (79/82) | 0.894 | 618 ms | yes |
| `BAAI/bge-reranker-v2-m3` (CPU) | 0.963 (79/82) | 0.891 | 5,734 ms | no |
| `Qwen/Qwen3-Reranker-0.6B` (CPU) | 0.951 (78/82) | 0.876 | 11,847 ms | no |

Cohere met the rule set before measuring, but stays off by default and opt-in with
`RERANKER=cohere` ([ADR 0002](adr/0002-reranker-off-by-default.md)):

- **The gain is 2 of 82 questions.** It brings three into the top 5 (the Clássica against the
  Smart, when credit sales are paid, whether a CNPJ is needed) and pushes one out ("A Getnet tem
  conta digital?", first to sixth).
- **It adds a vendor.** At an acquirer, a new processor of merchant queries needs compliance
  approval before it is on.
- **A trial key would not hold the default.** It allows 10 calls a minute, so under load the
  default would fall back to RRF anyway.

The local cross-encoders order almost as well but take 5 and 12 s per search on the CPU the
container has; Qwen loaded as bfloat16 was twenty times slower until forced to float32.

## Web search

Web search answers only steps the router labels `general_info` (weather, exchange rates), never a
product question the corpus can't answer. The query that leaves the process is masked and keeps no
number the merchant didn't type in that message, so account data can't leak into it. Pages are
untrusted data: the answer may carry no number they lack and no link of its own. A question about
a day asks for pages from the last day first, and its answer closes with the source's publication
date: the search provider's structured field when there is one, never a date the model reads, in
the merchant's calendar and without a time of day. A source older than the day in question gets a
notice with its date. Without a structured date, code reads the days the cited page names, and the
latest one on the day asked, or between today and it when that day is ahead, closes the answer as
"Data informada pela própria fonte", labelled as the page's own claim. With none, the answer
closes with "Não consegui confirmar a data de publicação desta informação", never a claim that
the source has no date. No key, a timeout or a failed check degrade to a pt-BR message, never an
exception.
