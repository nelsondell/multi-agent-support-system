"""Ingestion's two document sources: the raw corpus and the committed snapshot."""

import shutil
from pathlib import Path

import pytest

from app.rag.ingest import load_documents, read_snapshot, write_snapshot
from app.rag.parse import Document, Route

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
