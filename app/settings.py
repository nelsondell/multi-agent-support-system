"""Application configuration, read once from the environment."""

from datetime import timedelta, timezone
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh"]

MERCHANT_TIMEZONE = timezone(timedelta(hours=-3), "America/Sao_Paulo")
"""The merchants' calendar. Brazil has had no daylight saving time since 2019, so a fixed offset is
exact and needs no timezone database in the image."""


class Settings(BaseSettings):
    """Runtime configuration. Absent secrets are tolerated until a provider is built."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    llm_backend: Literal["openai", "fake"] = "openai"
    """`fake` answers every structured call deterministically with no vendor account. It exists
    for the CI smoke test and is never meant to serve a merchant."""
    openai_api_key: str | None = None
    llm_model: str = "gpt-6-luna"
    """Chosen by measurement: `evals/router_benchmark.md`."""
    llm_temperature: float | None = None
    """Sent only when set, and only to models without reasoning (gpt-4o-mini takes 0). Reasoning
    models such as gpt-6-luna reject any value but their own default."""
    llm_reasoning_effort: ReasoningEffort | None = None
    """Sent only to reasoning models. Resolves to `low` unless a temperature is set, because a
    temperature means a model without reasoning, which rejects this parameter."""
    judge_model: str = "gpt-6-sol"
    """Scores the eval runs only, never a merchant's turn. A tier above the chat model, so the
    model being graded is not grading itself."""
    embedding_model: str = "text-embedding-3-large"
    """Chosen by measurement: `evals/retrieval_calibration.md`."""
    embedding_backend: Literal["auto", "local", "openai"] = "auto"
    """Which embedder the index is built and queried with.

    Pinned separately from the chat key on purpose: the embedder is part of the index's
    identity, so adding an API key must not silently change it under a built index."""

    router_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    retrieval_confidence_threshold: float = Field(default=0.52, ge=0.0, le=1.0)
    """Calibrated for text-embedding-3-large in `evals/retrieval_calibration.md`: the midpoint of
    the gap between the highest out-of-domain score (0.464) and the lowest in-domain one (0.574).
    It belongs to that embedder. The local fallback embedder separates nothing, so with it no
    threshold can do this job and the knowledge answer's sufficiency flag is the only gate."""
    max_hops: int = Field(default=2, ge=1)
    """Router passes per turn: the first plan, and one more after a specialist hands it back."""
    max_plan_changes: int = Field(default=3, ge=0)
    """Steps the supervisor may add to the router's plan in one turn, each asked for by what a step
    found. 0 runs the router's plan as it is, which is the planner the eval compares against."""
    max_model_calls: int = Field(default=12, ge=1)
    """Model calls one turn may make before the supervisor stops starting steps."""
    max_cost_usd: float = Field(default=0.02, gt=0)
    """Model spend one turn may reach, at LLM_PRICE_*, before the supervisor stops starting
    steps. A stopped turn sends what it answered and hands the rest to a person."""
    llm_call_timeout_s: float = Field(default=20.0, gt=0)
    """Seconds one model call may take. A slower one fails like any vendor error, so the node that
    made it retries or degrades, instead of hanging until the node's own timeout."""
    node_timeout_s: float = Field(default=45.0, gt=0)
    """Wall-clock cap on one attempt of a graph node: a model call that hangs is cut here."""
    node_attempts: int = Field(default=2, ge=1)
    """Attempts per node on a model failure or a timeout, before the node's fallback runs."""

    index_path: str = "data/processed/chroma"
    collection_name: str = "getnet_kb"
    retrieval_top_k: int = 5

    storage: Literal["sqlite", "postgres"] = "sqlite"
    """Where conversations, cases and per-conversation locks live: SQLite files on one node, or
    Postgres shared by every replica."""
    database_url: str | None = None
    """Postgres connection string, for `STORAGE=postgres`. It carries a password: never logged."""
    database_pool_size: int = Field(default=10, ge=2)
    """Connections per replica, in each of its two pools: one for data, one for turn locks."""

    state_dir: str = "data/state"
    """Conversation checkpoints. Apart from the index on purpose: in the container this is its own
    named volume, so a restart keeps conversations and rebuilding the index never deletes them."""
    history_window: int = Field(default=6, ge=2)
    """Most recent messages kept verbatim. Older ones are folded into the rolling summary."""
    history_max_messages: int = Field(default=12, ge=3)
    """Once the history would pass this length, everything before the window is summarised."""

    tavily_api_key: str | None = None
    """Web search credential. Without it, general-purpose questions get a pt-BR message saying
    the system cannot look them up right now."""
    web_search_max_results: int = Field(default=3, ge=1, le=10)
    web_search_timeout_s: float = Field(default=8.0, gt=0)

    langsmith_tracing: bool = False
    """Uploads masked traces to LangSmith. Off by default; on without a key, it stays off."""
    langsmith_api_key: str | None = None
    langsmith_project: str = "getnet-support"
    otel_exporter_otlp_endpoint: str | None = None
    """OTLP gRPC collector for the OpenTelemetry spans (`make observe` points it at Jaeger).
    Unset, spans are still created, and name the request in the logs, but go nowhere."""

    llm_price_input: float = 0.10
    llm_price_cached_input: float = 0.01
    llm_price_output: float = 0.50
    """USD per million tokens of the chat model, for the cost metrics. gpt-6-luna's input and
    output list prices; the cached-input price is an assumption until the price list states it."""
    prompt_cache: bool = True
    """Mark the end of each static system prompt as an explicit cache breakpoint."""
    fault_injection: str = ""
    """Drills only: comma-separated faults to inject, `model_error`, `model_timeout`, `web_down`."""

    def faults(self) -> set[str]:
        return {fault.strip() for fault in self.fault_injection.split(",") if fault.strip()}

    handoff_ttl_hours: float = Field(default=24.0, gt=0)
    """How long a case waits for an operator. After that, the merchant's next message closes it
    as expired and the conversation goes on, so none waits forever."""

    auth_jwks_url: str | None = None
    """The identity provider's public keys: an https URL, or the file `make dev-keys` writes. The
    API refuses to start without them."""
    auth_issuer: str = "getnet-support-dev"
    merchant_audience: str = "support-api"
    operator_audience: str = "support-operators"
    rate_limit_per_minute: int = Field(default=30, ge=1)
    """Requests a merchant may make in any 60 seconds, per replica; past it, 429."""
    retention_days: int = Field(default=90, ge=1)
    """Conversations idle for longer, and the cases they opened, are erased by `make purge`."""

    @model_validator(mode="after")
    def pick_sampling_control(self) -> "Settings":
        """A temperature and a reasoning effort belong to different kinds of model."""
        if self.llm_temperature is not None and self.llm_reasoning_effort is not None:
            raise ValueError("set LLM_TEMPERATURE or LLM_REASONING_EFFORT, not both")
        if self.llm_temperature is None and self.llm_reasoning_effort is None:
            self.llm_reasoning_effort = "low"
        return self

    @model_validator(mode="after")
    def check_history_bounds(self) -> "Settings":
        """A window as large as the limit would never fold anything."""
        if self.history_max_messages <= self.history_window:
            raise ValueError("history_max_messages must be larger than history_window")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
