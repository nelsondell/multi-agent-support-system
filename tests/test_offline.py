"""Nothing is downloaded at runtime: the tokenizer ships in the repository, and the local embedder
refuses to fetch its model outside the image build and `make models`. The whole suite runs with
sockets blocked (pyproject), so any download attempt here would fail the test that made it."""

import gzip
import hashlib
import os

import pytest
import tiktoken
from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

from app.rag import ingest
from app.rag.chunk import TOKENIZER, tokenizer
from app.settings import Settings

CL100K_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"


def test_the_tokenizer_is_the_committed_one_and_leaves_the_environment(monkeypatch) -> None:
    assert hashlib.sha256(gzip.decompress(TOKENIZER.read_bytes())).hexdigest() == CL100K_SHA256
    monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)
    monkeypatch.setattr(tiktoken.registry, "ENCODINGS", {})  # force a load from disk
    tokenizer.cache_clear()

    text = "Quando cai a antecipação?"
    encoder = tokenizer()

    assert encoder.decode(encoder.encode(text)) == text
    assert "TIKTOKEN_CACHE_DIR" not in os.environ
    tokenizer.cache_clear()


def test_the_local_embedder_never_fetches_its_model_and_names_the_fix(monkeypatch, tmp_path):
    monkeypatch.setattr(ONNXMiniLM_L6_V2, "DOWNLOAD_PATH", tmp_path / "models")
    settings = Settings(index_path=str(tmp_path / "db" / "chroma"), embedding_backend="local")
    monkeypatch.setattr(ingest, "get_settings", lambda: settings)

    with pytest.raises(SystemExit, match="make models.*Nothing was written"):
        ingest.main(["--if-missing"])

    assert not (tmp_path / "db").exists()
