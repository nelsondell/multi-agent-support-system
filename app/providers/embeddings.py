"""The embedding vendor seam, kept separate from the chat model.

Ingestion and retrieval must embed with the same function, so both ask for it here. With an API
key configured this is OpenAI, which handles an English question against a Portuguese corpus
well. Without one it falls back to Chroma's bundled multilingual-weak local model, so the system
can be built and tested with no account at all.

The local model is fetched once, by the image build or `make models` (`python -m
app.providers.embeddings`), never at runtime: Chroma would otherwise download it on first use.
"""

from pathlib import Path

from chromadb.api.types import EmbeddingFunction

from app.settings import Settings


class LocalModelMissing(ValueError):
    """The local embedder was chosen before its model was fetched."""


def build_embedding_function(settings: Settings) -> EmbeddingFunction:
    """Return the embedding function matching the configured backend.

    `auto` follows the API key, which is convenient but means the index's embedder depends on
    the environment. Pin `local` or `openai` in anything that outlives one machine.
    """
    from chromadb.utils import embedding_functions

    backend = settings.embedding_backend
    if backend == "auto":
        backend = "openai" if settings.openai_api_key else "local"
    if backend == "openai":
        if not settings.openai_api_key:
            raise ValueError("embedding_backend is openai but OPENAI_API_KEY is not set")
        return embedding_functions.OpenAIEmbeddingFunction(
            api_key=settings.openai_api_key, model_name=settings.embedding_model
        )
    if not local_model_path().is_file():
        raise LocalModelMissing(
            "the local embedding model is not installed; run `make models` once (about 80 MB)"
        )
    return embedding_functions.DefaultEmbeddingFunction()


def local_model_path() -> Path:
    """Where Chroma keeps the local model once fetched."""
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2

    folder = Path(ONNXMiniLM_L6_V2.DOWNLOAD_PATH) / ONNXMiniLM_L6_V2.EXTRACTED_FOLDER_NAME
    return folder / "model.onnx"


def fetch_local_model() -> None:
    """Download the local model, the one step allowed to: the image build and `make models`."""
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    DefaultEmbeddingFunction()(["warm-up"])


if __name__ == "__main__":
    fetch_local_model()
