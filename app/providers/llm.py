"""The single seam to the LLM vendor.

Every agent talks to the model through `LLMProvider.structured`. No other module imports a
vendor SDK, so moving to Azure AI Foundry means rewriting this file and nothing else. The second
implementation, `app/providers/fake.py`, exists only for the CI smoke test.
"""

import asyncio
import hashlib
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from app.observability.metrics import MODEL_COST, MODEL_TOKENS
from app.observability.otel import operation
from app.settings import Settings

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class LLMProviderError(RuntimeError):
    """Raised when the vendor call fails or returns something unusable."""


class LLMTimeout(LLMProviderError):
    """The vendor did not answer within `LLM_CALL_TIMEOUT_S`."""


class LLMProvider(Protocol):
    """Structured output is the only calling convention: no module parses model prose."""

    async def structured(
        self, *, system: str, user: str, schema: type[SchemaT]
    ) -> SchemaT:  # pragma: no cover - protocol definition
        """Return an instance of `schema` decided by the model."""
        ...


class OpenAIProvider:
    """Current implementation, backed by OpenAI chat completions.

    The static system prompt always comes first and, with `cache`, ends at an explicit cache
    breakpoint, the request's own data after it, so each prompt's calls reuse its cached prefix.
    Every call runs in a `chat` span and records its tokens and cost.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        temperature: float | None = None,
        reasoning_effort: str | None = None,
        max_retries: int = 2,
        prices: tuple[float, float, float] = (0.0, 0.0, 0.0),
        cache: bool = False,
        http_client: Any = None,
        timeout: float | None = None,
    ) -> None:
        from langchain_openai import ChatOpenAI

        # Each control is sent only when set: a reasoning model rejects a temperature, and a model
        # without reasoning rejects an effort.
        controls = {"temperature": temperature, "reasoning_effort": reasoning_effort}
        options = {name: value for name, value in controls.items() if value is not None}
        if cache:
            options["prompt_cache_options"] = {"mode": "explicit", "ttl": "30m"}
        if http_client is not None:
            options["http_async_client"] = http_client
        if timeout is not None:
            options["timeout"] = timeout
        self._client = ChatOpenAI(api_key=api_key, model=model, max_retries=max_retries, **options)
        self._model, self._prices, self._cache = model, prices, cache

    async def structured(self, *, system: str, user: str, schema: type[SchemaT]) -> SchemaT:
        """Call the model and validate its answer against `schema`."""
        prefix: dict[str, Any] = {"type": "text", "text": system}
        if self._cache:
            prefix["prompt_cache_breakpoint"] = {"mode": "explicit"}
        key = hashlib.sha256(system.encode()).hexdigest()[:16]
        attributes = {"gen_ai.provider.name": "openai", "gen_ai.request.model": self._model}
        with operation("chat", self._model, **attributes) as span:
            try:
                result = await self._client.with_structured_output(
                    schema, include_raw=True, prompt_cache_key=key
                ).ainvoke([("system", [prefix]), ("human", user)])
            except Exception as exc:  # vendor SDKs raise a wide and unstable set of errors
                failure = LLMTimeout if "Timeout" in type(exc).__name__ else LLMProviderError
                raise failure(f"llm call failed: {exc}") from exc
            self._account(span, result["raw"].usage_metadata or {})
        parsed = result["parsed"]
        if not isinstance(parsed, schema):
            raise LLMProviderError(
                f"llm returned {type(parsed).__name__}, expected {schema.__name__}"
            )
        return parsed

    def _account(self, span: Any, usage: dict) -> None:
        """One call's tokens and cost, on its span and in the metrics."""
        inputs, outputs = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        cached = (usage.get("input_token_details") or {}).get("cache_read") or 0
        span.set_attributes(
            {
                "gen_ai.usage.input_tokens": inputs,
                "gen_ai.usage.output_tokens": outputs,
                "gen_ai.usage.cache_read.input_tokens": cached,
            }
        )
        for kind, count in (("input", inputs), ("cached", cached), ("output", outputs)):
            MODEL_TOKENS.labels(kind=kind).inc(count)
        price_input, price_cached, price_output = self._prices
        cost = (inputs - cached) * price_input + cached * price_cached + outputs * price_output
        MODEL_COST.inc(cost / 1_000_000)


class FaultyProvider:
    """Drills only: a vendor that fails the way `FAULT_INJECTION` says. A timeout takes the whole
    `timeout` first, as a vendor that never answers does."""

    def __init__(self, inner: LLMProvider, faults: set[str], timeout: float = 20.0) -> None:
        self._inner, self._faults, self._timeout = inner, faults, timeout

    async def structured(self, *, system: str, user: str, schema: type[SchemaT]) -> SchemaT:
        if "model_timeout" in self._faults:
            await asyncio.sleep(self._timeout)
            raise LLMTimeout("injected fault: the model vendor did not answer in time")
        if "model_error" in self._faults:
            raise LLMProviderError("injected fault: the model vendor answered 500")
        return await self._inner.structured(system=system, user=user, schema=schema)


def build_provider(settings: Settings) -> LLMProvider:
    """Build the configured provider, failing fast when a required secret is missing."""
    provider = _vendor(settings)
    faults = settings.faults() & {"model_error", "model_timeout"}
    if not faults:
        return provider
    return FaultyProvider(provider, faults, settings.llm_call_timeout_s)


def _vendor(settings: Settings) -> LLMProvider:
    if settings.llm_backend == "fake":
        from app.providers.fake import FakeProvider

        return FakeProvider()
    if not settings.openai_api_key:
        raise LLMProviderError("OPENAI_API_KEY is not set; export it or add it to .env")
    prices = (settings.llm_price_input, settings.llm_price_cached_input, settings.llm_price_output)
    return OpenAIProvider(
        api_key=settings.openai_api_key,
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        reasoning_effort=settings.llm_reasoning_effort,
        max_retries=0,  # the graph retries the node (build.py), so one layer of retries, not two
        prices=prices,
        cache=settings.prompt_cache,
        timeout=settings.llm_call_timeout_s,
    )
