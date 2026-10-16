# RAG pipeline and web search

| Stage | What happens | Where |
| --- | --- | --- |
| **Ingestion** | 18 fetched pages → 133 documents → 148 chunks. Two parsers, each confirmed against a real page: the FAQ (question and answer paired by `data-modal` id) and the article layout (`.fl-col-content`). Extracted text goes to a committed snapshot, so a fresh clone builds the same index without the raw HTML. 600-token chunks with 100 of overlap, deduplicated by content hash (11 duplicates: the same body served under different routes). Four pages match no parser and are reported, not dropped silently. | `app/rag/parse.py`, `chunk.py`, `ingest.py` |
| **Storage** | Chroma (cosine) for vectors, plus `chunks.jsonl` that BM25 is rebuilt from. Chunk ids are content hashes, so re-indexing is idempotent. The index lives in its own Docker volume. Ingestion keeps both halves or neither: a failed first boot leaves nothing a later boot could mistake for an index. | `app/rag/ingest.py` |
| **Retrieval** | Hybrid: dense vectors carry a question across languages, BM25 pins exact product names ("Get Smart" vs "Get Mini"). The two rankings are fused with Reciprocal Rank Fusion by position only. A comparison adds one search per product; their rankings are interleaved, best hit of each first. The whole question is always one of the queries. Confidence is the best dense similarity any query reached; below 0.52 the agent abstains without a model call. | `app/rag/retrieve.py` |
| **Generation** | The model gets only the retrieved excerpts, each labelled with its url, and the financial hard rules, and returns a structured answer with `coverage`: `full`, `partial` (answer what is supported, name what the site doesn't cover) or `none` (abstain, a person takes it). A price that depends on a tier is stated with its condition. Sources are taken from what was retrieved, never from urls the model claims. | `app/graph/knowledge.py`, `app/prompts/knowledge.md` |

## Why these choices, with numbers

[`evals/retrieval_calibration.md`](../evals/retrieval_calibration.md) runs 22 questions the corpus
answers against 15 it doesn't, through three embedders. Chroma's local model can't separate them
(the best out-of-domain question outscores the worst in-domain one by 0.100);
`text-embedding-3-large` separates them completely with a gap of 0.110, so it is the default and the
threshold sits in the middle of that gap. Fusing every query's list at once was measured and
rejected for comparisons: RRF rewards consensus, so generic offer pages crowded out each product's
own page.

**Why RRF with BM25 instead of dense retrieval only?** They fail differently. Embeddings carry an
English question into Portuguese prose but blur near-identical product pages; BM25 pins "Get Smart"
against "Get Mini". RRF fuses them by rank, so their incomparable scores never meet. Dense
similarity is still what decides abstention, because a rank says nothing about whether the corpus
answers at all, and that signal was calibrated per embedder rather than assumed.

## Web search

Web search answers only steps the router labels `general_info` (weather, exchange rates), never a
product question the corpus can't answer. The query that leaves the process is masked and keeps no
number the merchant didn't type in that message, so account data can't leak into it. Pages are
untrusted data: the answer may carry no number they lack and no link of its own. A question about
a day asks for pages from the last day first, and its answer closes with the source's publication
date: taken only from the search provider's structured field, never from page text the model
reads, in the merchant's calendar and without a time of day. A source older than the day in
question gets a notice with its date. Without a structured date, the answer closes with "Não
consegui confirmar a data de publicação desta informação": it never says the source has no date,
because its page may show one the provider did not structure. No key, a timeout or a
failed check degrade to a pt-BR message, never an exception.
