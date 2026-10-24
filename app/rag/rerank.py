"""Optional second stage: reorder the fused candidates by how well each answers the query.

`RERANKER=none` keeps the RRF order. `bge` and `qwen` are cross-encoders run on the CPU through
sentence-transformers, from the optional `rerank` dependency group, with weights fetched
beforehand and never at query time. `cohere` calls Cohere's rerank API. Ordering is an
improvement, never a dependency: a reranker that cannot be built or fails on a query leaves the
fused order in place, with a warning.

    python -m app.rag.rerank bge   # fetch a local model's weights: the image build does this
"""

import logging
from collections.abc import Callable, Sequence

import httpx

from app.observability import mask_pii
from app.settings import Settings

logger = logging.getLogger(__name__)

Rerank = Callable[[str, Sequence[str]], list[float]]
"""Scores each text against the query, in the texts' order; higher is more relevant."""

LOCAL_MODELS = {"bge": "BAAI/bge-reranker-v2-m3", "qwen": "Qwen/Qwen3-Reranker-0.6B"}
COHERE_MODEL = "rerank-v4.0-fast"
COHERE_URL = "https://api.cohere.com/v2/rerank"
COHERE_TRIAL_INTERVAL_S = 6.5
"""A Cohere trial key allows 10 rerank calls a minute: live evals space their calls by this."""


def build_reranker(settings: Settings) -> Rerank | None:
    """The configured reranker, or None to keep the fused order."""
    if settings.reranker == "none":
        return None
    if settings.reranker == "cohere":
        if not settings.cohere_api_key:
            logger.warning("RERANKER=cohere without COHERE_API_KEY; keeping the fused order")
            return None
        return cohere_reranker(settings.cohere_api_key, settings.rerank_timeout_s)
    try:
        return local_reranker(LOCAL_MODELS[settings.reranker], download=False)
    except (ImportError, OSError) as exc:
        logger.warning(
            "reranker %s unavailable (%s); keeping the fused order. Install the `rerank` "
            "dependency group and fetch its weights.",
            settings.reranker,
            type(exc).__name__,
        )
        return None


def local_reranker(model: str, download: bool) -> Rerank:
    """A cross-encoder on the CPU, the only device the container has, in float32: Qwen's weights
    load as bfloat16, which ran twenty times slower on the CPU. Only the image build and the
    benchmark pass `download=True`."""
    from sentence_transformers import CrossEncoder

    encoder = CrossEncoder(
        model,
        device="cpu",
        local_files_only=not download,
        model_kwargs={"dtype": "float32"},
    )

    def rerank(query: str, texts: Sequence[str]) -> list[float]:
        return [float(score) for score in encoder.predict([(query, text) for text in texts])]

    return rerank


def cohere_reranker(api_key: str, timeout_s: float) -> Rerank:
    """Cohere's rerank endpoint, which returns a relevance score per document index. The query
    leaves the process masked, as a web search's does; the documents are public site text."""

    def rerank(query: str, texts: Sequence[str]) -> list[float]:
        response = httpx.post(
            COHERE_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": COHERE_MODEL, "query": mask_pii(query), "documents": list(texts)},
            timeout=timeout_s,
        )
        response.raise_for_status()
        scores = [0.0] * len(texts)
        for result in response.json()["results"]:
            scores[result["index"]] = result["relevance_score"]
        return scores

    return rerank


if __name__ == "__main__":
    import sys

    local_reranker(LOCAL_MODELS[sys.argv[1]], download=True)
