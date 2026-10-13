"""The single seam to the LLM vendor.

Every agent talks to the model through `LLMProvider.structured`. No other module imports a
vendor SDK, so moving to Azure AI Foundry means rewriting this file and nothing else. The second
implementation, `app/providers/fake.py`, exists only for the CI smoke test.
"""

from typing import Protocol, TypeVar

from pydantic import BaseModel

from app.settings import Settings

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMProviderError(RuntimeError):
    """Raised when the vendor call fails or returns something unusable."""


class LLMProvider(Protocol):
    """Structured output is the only calling convention: no module parses model prose."""

    async def structured(
        self, *, system: str, user: str, schema: type[SchemaT]
    ) -> SchemaT:  # pragma: no cover - protocol definition
        """Return an instance of `schema` decided by the model."""
        ...


class OpenAIProvider:
    """Current implementation, backed by OpenAI chat completions."""

    def __init__(self, *, api_key: str, model: str, temperature: float) -> None:
        from langchain_openai import ChatOpenAI

        self._client = ChatOpenAI(api_key=api_key, model=model, temperature=temperature)

    async def structured(self, *, system: str, user: str, schema: type[SchemaT]) -> SchemaT:
        """Call the model and validate its answer against `schema`."""
        try:
            result = await self._client.with_structured_output(schema).ainvoke(
                [("system", system), ("human", user)]
            )
        except Exception as exc:  # vendor SDKs raise a wide and unstable set of errors
            raise LLMProviderError(f"llm call failed: {exc}") from exc
        if not isinstance(result, schema):
            raise LLMProviderError(
                f"llm returned {type(result).__name__}, expected {schema.__name__}"
            )
        return result


def build_provider(settings: Settings) -> LLMProvider:
    """Build the configured provider, failing fast when a required secret is missing."""
    if settings.llm_backend == "fake":
        from app.providers.fake import FakeProvider

        return FakeProvider()
    if not settings.openai_api_key:
        raise LLMProviderError("OPENAI_API_KEY is not set; export it or add it to .env")
    return OpenAIProvider(
        api_key=settings.openai_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
    )
