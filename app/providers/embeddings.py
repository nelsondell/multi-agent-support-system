"""The embedding vendor seam, kept separate from the chat model.

Ingestion and retrieval must embed with the same function, so both ask for it here. With an API
key configured this is OpenAI, which handles an English question against a Portuguese corpus
well. Without one it falls back to Chroma's bundled multilingual-weak local model, so the system
can be built and tested with no account at all.
"""

from chromadb.api.types import EmbeddingFunction

from app.settings import Settings


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
    return embedding_functions.DefaultEmbeddingFunction()
