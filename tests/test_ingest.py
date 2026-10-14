"""Ingestion: its two document sources, and the flags the container and the host use."""

import shutil
from pathlib import Path

import chromadb
import pytest

from app.rag import ingest
from app.rag.chunk import Chunk, ChunkStats
from app.rag.ingest import index_exists, load_documents, read_snapshot, write_snapshot
from app.rag.parse import Document, Route
from app.settings import Settings

URL = "https://example.test/card-machines/"
BODY = "The card machine accepts debit and credit cards and settles sales in one business day. " * 6
PAGE = (
    "<html><head><title>Card machines</title></head><body>"
    f"<div class='fl-col-content'><p>{BODY}</p></div></body></html>"
)
STALE = [Document(url=URL, title="old", section="", text="outdated body", route=Route.ARTICLE)]


def raw_corpus(root: Path) -> tuple[Path, Path]:
    """One fetched page and the manifest line that names its url."""
    raw_dir = root / "raw"
    raw_dir.mkdir()
    (raw_dir / "card-machines.html").write_text(PAGE, encoding="utf-8")
    manifest = root / "manifest.tsv"
    manifest.write_text(
        f"OK\t200\t{len(PAGE)}\tdata/raw/card-machines.html\t{URL}\n", encoding="utf-8"
    )
    return raw_dir, manifest


def test_snapshot_round_trips_documents(tmp_path):
    documents = [
        Document(url=URL, title="FAQ", section="Sales", text="Q\nA", route=Route.FAQ),
        Document(url=URL, title="Page", section="", text="body", route=Route.ARTICLE),
    ]
    write_snapshot(documents, tmp_path / "documents.jsonl")

    assert read_snapshot(tmp_path / "documents.jsonl") == documents


@pytest.mark.parametrize("missing", ["raw", "manifest"])
def test_reads_the_snapshot_when_part_of_the_raw_corpus_is_missing(tmp_path, missing):
    raw_dir, manifest = raw_corpus(tmp_path)
    if missing == "manifest":
        manifest.unlink()
    else:
        shutil.rmtree(raw_dir)
    snapshot = tmp_path / "documents.jsonl"
    write_snapshot(STALE, snapshot)
    before = snapshot.read_bytes()

    documents, _, skipped = load_documents(raw_dir, manifest, snapshot)

    assert documents == STALE
    assert skipped == []
    assert snapshot.read_bytes() == before


def test_refreshes_the_snapshot_from_the_raw_corpus_idempotently(tmp_path):
    raw_dir, manifest = raw_corpus(tmp_path)
    snapshot = tmp_path / "corpus" / "documents.jsonl"
    write_snapshot(STALE, snapshot)

    documents, _, _ = load_documents(raw_dir, manifest, snapshot)
    first = snapshot.read_bytes()
    load_documents(raw_dir, manifest, snapshot)

    assert [document.url for document in documents] == [URL]
    assert read_snapshot(snapshot) == documents
    assert snapshot.read_bytes() == first


def test_both_sources_yield_the_same_documents(tmp_path):
    raw_dir, manifest = raw_corpus(tmp_path)
    snapshot = tmp_path / "documents.jsonl"

    from_raw, _, _ = load_documents(raw_dir, manifest, snapshot)
    from_snapshot, _, _ = load_documents(tmp_path / "absent", manifest, snapshot)

    assert from_raw == from_snapshot


def test_an_empty_extraction_leaves_the_snapshot_untouched(tmp_path):
    raw_dir, manifest = raw_corpus(tmp_path)
    (raw_dir / "card-machines.html").write_text("<html><body></body></html>", encoding="utf-8")
    snapshot = tmp_path / "documents.jsonl"
    write_snapshot(STALE, snapshot)
    before = snapshot.read_bytes()

    with pytest.raises(ValueError, match="left untouched"):
        load_documents(raw_dir, manifest, snapshot)
    assert snapshot.read_bytes() == before


def test_fails_with_the_fix_when_there_is_nothing_to_index(tmp_path):
    with pytest.raises(FileNotFoundError, match="no snapshot"):
        load_documents(tmp_path / "raw", tmp_path / "manifest.tsv", tmp_path / "documents.jsonl")


@pytest.fixture
def build_steps(monkeypatch, tmp_path):
    """Replace the expensive build steps with recorders, so main() runs offline."""
    calls: list[str] = []
    monkeypatch.setattr(ingest, "get_settings", lambda: Settings(index_path=str(tmp_path / "db")))
    monkeypatch.setattr(ingest, "load_documents", lambda: (STALE, "from a test", []))
    monkeypatch.setattr(
        ingest, "chunk_documents", lambda documents: ([], ChunkStats(generated=0, discarded=0))
    )
    monkeypatch.setattr(ingest, "write_chunks", lambda chunks, path: calls.append("write_chunks"))
    monkeypatch.setattr(ingest, "index_chunks", lambda chunks, settings: calls.append("index"))
    monkeypatch.setattr(ingest, "build_fixture", lambda chunks: calls.append("fixture") or 0)
    return calls


def test_if_missing_skips_the_build_when_an_index_exists(monkeypatch, build_steps):
    monkeypatch.setattr(ingest, "index_exists", lambda settings: True)

    ingest.main(["--if-missing"])

    assert build_steps == []


def test_if_missing_builds_when_there_is_no_index(monkeypatch, build_steps):
    monkeypatch.setattr(ingest, "index_exists", lambda settings: False)

    ingest.main(["--if-missing"])

    assert build_steps == ["index", "write_chunks"]


def test_the_fixture_is_only_rewritten_on_request(build_steps):
    ingest.main([])
    ingest.main(["--refresh-fixture"])

    assert build_steps == ["index", "write_chunks", "index", "write_chunks", "fixture"]


def test_an_index_without_its_bm25_corpus_does_not_count(tmp_path):
    settings = Settings(index_path=str(tmp_path / "db"))

    assert index_exists(settings) is False
    assert not (tmp_path / "db").exists()


def test_a_first_boot_without_a_key_stops_with_the_fix_and_writes_nothing(monkeypatch, tmp_path):
    """A fresh clone defaults to the OpenAI embedder; without a key nothing may be half built."""
    settings = Settings(index_path=str(tmp_path / "db" / "chroma"), embedding_backend="openai")
    monkeypatch.setattr(ingest, "get_settings", lambda: settings)
    monkeypatch.setattr(ingest, "load_documents", lambda: pytest.fail("read before failing"))

    with pytest.raises(SystemExit, match="OPENAI_API_KEY.*EMBEDDING_BACKEND=local"):
        ingest.main(["--if-missing"])

    assert not (tmp_path / "db").exists()


def test_a_build_that_fails_halfway_keeps_neither_half(monkeypatch, tmp_path):
    """The collection exists by the time the vectors fail, as with a rejected key."""
    settings = Settings(index_path=str(tmp_path / "db" / "chroma"), embedding_backend="local")

    def unreachable(*args, **kwargs):
        raise ConnectionError("embedding api unreachable")

    monkeypatch.setattr("chromadb.api.models.Collection.Collection.upsert", unreachable)
    chunks = [
        Chunk(id="c1", text=BODY, url=URL, title="Card machines", section="", route="article")
    ]

    with pytest.raises(SystemExit, match="ConnectionError.*nothing was kept"):
        ingest.build_index(chunks, settings)

    assert not ingest.chunks_path(settings).exists()
    assert index_exists(settings) is False
    collections = chromadb.PersistentClient(path=settings.index_path).list_collections()
    assert [collection.name for collection in collections] == []


def test_pages_sharing_a_title_are_told_apart_by_their_heading():
    """The site titles both product pages alike; their headings name the product."""
    shared = "Getnet | Maquininha POS 3G + Wi-Fi | 4020-4011"
    pages = {
        "https://example.test/classica/": "Impulsione o seu negócio com a Get Clássica",
        "https://example.test/smart/": "Get Smart o impulso para o seu negócio",
        "https://example.test/duvidas/": "nós podemos te ajudar!",
    }
    documents = [
        Document(url=url, title=shared, section="", text="body", route=Route.ARTICLE)
        for url in list(pages)[:2]
    ] + [
        Document(
            url="https://example.test/duvidas/",
            title="Dúvidas e Perguntas Frequentes | Getnet",
            section="",
            text="Q\nA",
            route=Route.FAQ,
        )
    ]

    titled = ingest.distinct_titles(documents, pages)

    assert [document.title for document in titled] == [
        "Impulsione o seu negócio com a Get Clássica",
        "Get Smart o impulso para o seu negócio",
        "Dúvidas e Perguntas Frequentes | Getnet",
    ]
