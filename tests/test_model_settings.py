"""Which sampling control reaches the chat model: an effort or a temperature, never both."""

import langchain_openai
import pytest
from pydantic import ValidationError

from app.providers.llm import build_provider
from app.settings import Settings


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in ("LLM_MODEL", "LLM_TEMPERATURE", "LLM_REASONING_EFFORT"):
        monkeypatch.delenv(name, raising=False)


def settings(**values) -> Settings:
    """Settings from the arguments alone, never from the developer's `.env`."""
    return Settings(_env_file=None, openai_api_key="sk-test", **values)


@pytest.fixture
def client_options(monkeypatch) -> list[dict]:
    """Records the keyword arguments the provider hands to the vendor client."""
    calls: list[dict] = []

    class Recorder:
        def __init__(self, **kwargs) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", Recorder)
    return calls


def test_the_default_is_gpt_6_luna_at_low_effort_without_a_temperature(client_options):
    build_provider(settings())

    assert client_options[0]["model"] == "gpt-6-luna"
    assert client_options[0]["reasoning_effort"] == "low"
    assert "temperature" not in client_options[0]


def test_gpt_4o_mini_keeps_working_with_temperature_zero(client_options):
    configured = settings(llm_model="gpt-4o-mini", llm_temperature=0)

    build_provider(configured)

    assert configured.llm_reasoning_effort is None
    assert client_options[0]["temperature"] == 0
    assert "reasoning_effort" not in client_options[0]


def test_an_explicit_effort_is_kept(client_options):
    build_provider(settings(llm_reasoning_effort="medium"))

    assert client_options[0]["reasoning_effort"] == "medium"


def test_a_temperature_and_an_effort_together_fail_at_start_up():
    with pytest.raises(ValidationError, match="not both"):
        settings(llm_temperature=0, llm_reasoning_effort="low")


def test_an_empty_temperature_in_the_environment_counts_as_unset(monkeypatch):
    monkeypatch.setenv("LLM_TEMPERATURE", "")

    configured = settings()

    assert configured.llm_temperature is None
    assert configured.llm_reasoning_effort == "low"
