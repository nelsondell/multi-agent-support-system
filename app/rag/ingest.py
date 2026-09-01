"""Build the knowledge index from the raw corpus.

Runs on the host, never at API start-up: the service reads a prepared index and fails fast if it
is missing. Re-running is idempotent — chunk ids are content hashes and Chroma upserts them, so a
second run rewrites the same rows instead of duplicating them.

    uv run python -m app.rag.ingest
"""

import json
from pathlib import Path

from rank_bm25 import BM25Okapi

from app.rag.chunk import Chunk, chunk_documents, tokenize
from app.rag.parse import Document, parse_page
from app.settings import Settings, get_settings

RAW_DIR = Path("data/raw")
MANIFEST = Path("docs/_local/corpus-manifest.tsv")
FIXTURE = Path("tests/fixtures/kb_sample.jsonl")

FIXTURE_SCENARIOS = [
    "diferença entre a maquininha Get Clássica e a Get Smart",
    "previsão do tempo em Porto Alegre amanhã",
    "quando o dinheiro das vendas de ontem é depositado",
    "preciso de conta bancária para receber minhas vendas via Pix",
    "maquininha não conecta na internet o que fazer",
    "como funciona a antecipação de recebíveis",
    "cotação do euro hoje",
    "maquininha com erro de transação negada",
    "em quantas parcelas posso dividir uma venda no crediário",
    "posso vender pelo WhatsApp usando o link de pagamento",
]
FIXTURE_PER_SCENARIO = 4


def read_manifest(path: Path = MANIFEST) -> dict[str, str]:
    """Map local file to source url. The fetch manifest is the only record of that pairing."""
    if not path.is_file():
        raise FileNotFoundError(
            f"corpus manifest not found at {path}; "
            "re-fetch the corpus from docs/_local/corpus-urls.txt"
        )
    mapping = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) == 5 and fields[0] == "OK":
            mapping[Path(fields[3]).name] = fields[4]
    return mapping


def extract_corpus(raw_dir: Path = RAW_DIR) -> tuple[list[Document], list[str]]:
    """Parse every fetched page, reporting the ones neither route can read."""
    urls = read_manifest()
    documents: list[Document] = []
    skipped: list[str] = []
    for path in sorted(raw_dir.glob("*.html")):
        url = urls.get(path.name)
        if url is None:
            skipped.append(f"{path.name} (absent from manifest)")
            continue
        page_documents = parse_page(path.read_text(encoding="utf-8"), url)
        if not page_documents:
            skipped.append(f"{path.name} (no route matched)")
            continue
        documents.extend(page_documents)
    return documents, skipped


def write_chunks(chunks: list[Chunk], path: Path) -> None:
    """Persist the chunk corpus that BM25 is rebuilt from at query time."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk.__dict__, ensure_ascii=False) + "\n")


def index_chunks(chunks: list[Chunk], settings: Settings) -> None:
    """Upsert every chunk into the persisted Chroma collection."""
    import chromadb

    from app.providers.embeddings import build_embedding_function

    client = chromadb.PersistentClient(path=settings.index_path)
    collection = client.get_or_create_collection(
        name=settings.collection_name,
        embedding_function=build_embedding_function(settings),
        metadata={"hnsw:space": "cosine"},
    )
    collection.upsert(
        ids=[chunk.id for chunk in chunks],
        documents=[chunk.text for chunk in chunks],
        metadatas=[
            {"url": chunk.url, "title": chunk.title, "section": chunk.section, "route": chunk.route}
            for chunk in chunks
        ],
    )


def build_fixture(chunks: list[Chunk], path: Path = FIXTURE) -> int:
    """Pick the chunks the offline tests run against: the top lexical matches per scenario.

    Out-of-domain scenarios are included on purpose, so a test can prove the abstain path on a
    corpus that genuinely does not answer them.
    """
    bm25 = BM25Okapi([tokenize(chunk.text) for chunk in chunks])
    selected: dict[str, Chunk] = {}
    for scenario in FIXTURE_SCENARIOS:
        scores = bm25.get_scores(tokenize(scenario))
        ranked = sorted(zip(chunks, scores, strict=True), key=lambda pair: -pair[1])
        for chunk, score in ranked[:FIXTURE_PER_SCENARIO]:
            if score > 0:
                selected[chunk.id] = chunk
    write_chunks(list(selected.values()), path)
    return len(selected)


def main() -> None:
    """Extract, chunk, index and refresh the test fixture."""
    settings = get_settings()
    documents, skipped = extract_corpus()
    chunks, stats = chunk_documents(documents)

    print(f"pages parsed into documents: {len(documents)}")
    for entry in skipped:
        print(f"  skipped: {entry}")
    print(stats.summary())

    write_chunks(chunks, Path(settings.index_path).parent / "chunks.jsonl")
    index_chunks(chunks, settings)
    print(f"indexed into {settings.index_path} (collection {settings.collection_name})")
    print(f"fixture chunks written to {FIXTURE}: {build_fixture(chunks)}")


if __name__ == "__main__":
    main()
