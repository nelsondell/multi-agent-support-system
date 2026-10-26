"""The local model's download is retried, so a slow network does not fail the image build."""

import httpx
import pytest

from app.providers.embeddings import fetch_local_model


@pytest.fixture
def flaky(monkeypatch):
    """Chroma's default embedder, replaced by one that times out `failures` times, then works."""

    def build(failures: int) -> list[int]:
        calls: list[int] = []

        class Flaky:
            def __call__(self, texts: list[str]) -> list[list[float]]:
                calls.append(1)
                if len(calls) <= failures:
                    raise httpx.ReadTimeout("The read operation timed out")
                return [[0.0]]

        monkeypatch.setattr("chromadb.utils.embedding_functions.DefaultEmbeddingFunction", Flaky)
        return calls

    return build


def test_a_timed_out_download_is_retried_with_backoff(flaky) -> None:
    calls, waits = flaky(failures=2), []

    fetch_local_model(sleep=waits.append)

    assert len(calls) == 3
    assert waits == [5.0, 10.0]


def test_the_download_fails_after_three_retries(flaky) -> None:
    calls, waits = flaky(failures=10), []

    with pytest.raises(httpx.ReadTimeout):
        fetch_local_model(sleep=waits.append)

    assert len(calls) == 4
    assert waits == [5.0, 10.0, 20.0]
