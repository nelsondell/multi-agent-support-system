"""A deterministic stand-in for the LLM, selected with `LLM_BACKEND=fake` and used only by CI.

It lets the container start and serve a request with no vendor account, so the smoke test can
prove the image, the index and the graph work together. It decides nothing: every schema is
filled from its own declaration, so a schema added later needs no change here. The answers it
produces are placeholders, never something to show a merchant.
"""

import types
from enum import Enum
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from app.providers.llm import SchemaT

FAKE_TEXT = "fake structured output"


class FakeProvider:
    """Builds a valid instance of whatever schema is requested, the same one every time."""

    async def structured(self, *, system: str, user: str, schema: type[SchemaT]) -> SchemaT:
        """Ignore the prompt and derive the answer from the schema alone."""
        return build_instance(schema)


def build_instance(schema: type[SchemaT]) -> SchemaT:
    """Fill every field, then let the schema's own validation confirm the result."""
    values = {name: _field_value(field) for name, field in schema.model_fields.items()}
    return schema.model_validate(values)


def _field_value(field: FieldInfo) -> Any:
    """A declared default wins; otherwise the value is derived from the type."""
    if not field.is_required():
        return field.get_default(call_default_factory=True)
    return _type_value(field.annotation, field.metadata)


def _type_value(annotation: Any, metadata: list[Any]) -> Any:
    """The first enum member or literal, the upper bound of a constrained number, and so on."""
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        members = get_args(annotation)
        return None if type(None) in members else _type_value(members[0], [])
    if origin is Literal:
        return get_args(annotation)[0]
    if origin in (list, set, tuple):
        members = get_args(annotation)
        return [_type_value(members[0], []) for _ in range(_min_length(metadata))]
    if origin is dict:
        return {}
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return next(iter(annotation))
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return build_instance(annotation)
    if annotation is bool:
        return False
    if annotation in (int, float):
        return annotation(_bound(metadata))
    return FAKE_TEXT


def _min_length(metadata: list[Any]) -> int:
    """The shortest list the field accepts: an empty one unless it declares `min_length`."""
    return next((item.min_length for item in metadata if hasattr(item, "min_length")), 0)


def _bound(metadata: list[Any]) -> float:
    """The inclusive upper bound when there is one, else the inclusive lower bound, else zero."""
    for attribute in ("le", "ge"):
        for constraint in metadata:
            if getattr(constraint, attribute, None) is not None:
                return getattr(constraint, attribute)
    return 0
