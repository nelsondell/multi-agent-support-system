"""The embedding vendor seam, kept separate from the chat model.

Ingestion and retrieval must embed with the same function, so both ask for it here. With an API
key configured this is OpenAI, which handles an English question against a Portuguese corpus
well. Without one it falls back to Chroma's bundled multilingual-weak local model, so the system
can be built and tested with no account at all. Its model is fetched by the image build or
`make models`, never at runtime.
"""

import logging
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from chromadb.api.types import EmbeddingFunction

from app.settings import Settings

logger = logging.getLogger(__name__)

DOWNLOAD_RETRIES = 3
"""A slow network times the model download out now and then; one timeout must not fail the
image build."""


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


def fetch_local_model(
    retries: int = DOWNLOAD_RETRIES,
    backoff_s: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Download the local model, the one step allowed to: the image build and `make models`.

    A failed download is retried `retries` times, waiting `backoff_s` and then twice as long each
    time. A partial archive fails Chroma's checksum on the next try and is fetched again.
    """
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    for attempt in range(retries + 1):
        try:
            DefaultEmbeddingFunction()(["warm-up"])
            return
        except (httpx.HTTPError, ValueError) as exc:  # a timeout, or Chroma's checksum mismatch
            if attempt == retries:
                raise
            wait = backoff_s * 2**attempt
            logger.warning("model download failed (%s); retry in %.0f s", type(exc).__name__, wait)
            sleep(wait)


if __name__ == "__main__":
    fetch_local_model()
