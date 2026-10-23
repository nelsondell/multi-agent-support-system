"""Nothing is downloaded at runtime: the tokenizer ships in the repository, and the local embedder
refuses to fetch its model outside the image build and `make models`. The whole suite runs with
sockets blocked (pyproject), so any download attempt here would fail the test that made it."""

import hashlib
import os

import pytest
import tiktoken
from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

from app.providers.embeddings import LocalModelMissing, build_embedding_function
from app.rag import ingest
from app.rag.chunk import ASSETS, tokenizer
from app.settings import Settings

CL100K = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
CL100K_SHA256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"


def test_the_committed_tokenizer_is_the_one_tiktoken_expects() -> None:
    assert hashlib.sha256((ASSETS / CL100K).read_bytes()).hexdigest() == CL100K_SHA256


def test_the_tokenizer_loads_from_the_repository_and_leaves_the_environment(monkeypatch) -> None:
    monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)
    monkeypatch.setattr(tiktoken.registry, "ENCODINGS", {})  # force a load from disk
    tokenizer.cache_clear()

    text = "Quando cai a antecipação?"
    encoder = tokenizer()

    assert encoder.decode(encoder.encode(text)) == text
    assert "TIKTOKEN_CACHE_DIR" not in os.environ
    assert sorted(path.name for path in ASSETS.iterdir()) == [CL100K, "README.md"]
    tokenizer.cache_clear()


def test_the_local_embedder_never_fetches_its_model_at_runtime(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ONNXMiniLM_L6_V2, "DOWNLOAD_PATH", tmp_path)

    with pytest.raises(LocalModelMissing, match="make models"):
        build_embedding_function(Settings(embedding_backend="local"))


def test_a_build_without_the_local_model_names_the_fix(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ONNXMiniLM_L6_V2, "DOWNLOAD_PATH", tmp_path / "models")
    settings = Settings(index_path=str(tmp_path / "db" / "chroma"), embedding_backend="local")
    monkeypatch.setattr(ingest, "get_settings", lambda: settings)

    with pytest.raises(SystemExit, match="make models.*Nothing was written"):
        ingest.main(["--if-missing"])

    assert not (tmp_path / "db").exists()
