"""The judge seam: every eval grades through `judge_for(settings)`, which speaks the chat model's
own `structured` contract, so a judge is swapped by `JUDGE_PROVIDER` and `JUDGE_MODEL` alone.

The default is `claude-opus-5-5`, another model family than the chat model being graded. No
server-side model fallback is enabled: a judge that silently switched models would no longer be
the judge its TPR and TNR were measured on, so a refusal is an error instead.
"""

from typing import TypeVar

from pydantic import BaseModel

from app.providers.llm import LLMProviderError, OpenAIProvider
from app.settings import Settings

SchemaT = TypeVar("SchemaT", bound=BaseModel)
EFFORT = "medium"


class AnthropicJudge:
    def __init__(self, model: str) -> None:
        from anthropic import AsyncAnthropic

        self._client, self._model = AsyncAnthropic(), model

    async def structured(self, *, system: str, user: str, schema: type[SchemaT]) -> SchemaT:
        response = await self._client.messages.parse(
            model=self._model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": EFFORT},
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise LLMProviderError(f"judge gave no verdict: {response.stop_reason}")
        return response.parsed_output


def judge_for(settings: Settings):
    if settings.judge_provider == "anthropic":
        return AnthropicJudge(settings.judge_model)
    key = settings.openai_api_key or ""
    return OpenAIProvider(api_key=key, model=settings.judge_model, reasoning_effort=EFFORT)
