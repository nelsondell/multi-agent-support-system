"""The conversation store decodes the types the graph state holds, and nothing else."""

from enum import Enum
from typing import get_args

from pydantic import BaseModel

from app.checkpoints import STATE_TYPES, checkpoint_serializer
from app.contracts import Intent, Source
from app.graph.state import GraphState


class Smuggled(BaseModel):
    """A type no graph state holds, standing in for whatever a tampered checkpoint might name."""

    payload: str


def application_types(annotation: object) -> set[type]:
    """Every enum or model defined under `app` that can appear inside a value of `annotation`."""
    found: set[type] = set()
    pending = [annotation]
    while pending:
        current = pending.pop()
        pending.extend(get_args(current))
        if not isinstance(current, type) or not current.__module__.startswith("app."):
            continue
        if current in found or not issubclass(current, Enum | BaseModel):
            continue
        found.add(current)
        if issubclass(current, BaseModel):
            pending.extend(field.annotation for field in current.model_fields.values())
    return found


def test_the_allowlist_matches_the_types_the_state_can_hold():
    reachable = set().union(
        *(application_types(field.annotation) for field in GraphState.model_fields.values())
    )

    assert reachable == set(STATE_TYPES)


def test_allowed_types_come_back_as_themselves():
    serializer = checkpoint_serializer()

    for value in (Source(url="https://example.test/", title="Example"), Intent.PRODUCT_INFO):
        loaded = serializer.loads_typed(serializer.dumps_typed(value))
        assert loaded == value
        assert type(loaded) is type(value)


def test_an_unlisted_type_comes_back_as_plain_data():
    serializer = checkpoint_serializer()

    loaded = serializer.loads_typed(serializer.dumps_typed(Smuggled(payload="import me")))

    assert not isinstance(loaded, Smuggled)
    assert loaded == {"payload": "import me"}
