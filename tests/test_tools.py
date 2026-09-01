"""Support tools: what the model may ask for, and whose data it can reach."""

import re

import pytest

from app.tools.account import SUPPORT_TOOLS, call_tool, get_settlement_schedule, get_transactions

OTHER_CUSTOMER = "cliente7771"
CALLER = "cliente1988"


@pytest.mark.parametrize("tool", [get_settlement_schedule, get_transactions])
def test_user_id_is_not_in_the_schema_the_model_sees(tool) -> None:
    """Authorization cannot be argued with, because there is no argument for it."""
    assert "user_id" not in tool.tool_call_schema.model_fields
    assert "user_id" in tool.args_schema.model_fields


def test_a_smuggled_user_id_cannot_reach_another_customer() -> None:
    """The attack: the model is talked into passing someone else's id as a tool argument."""
    caller_view = call_tool("get_transactions", {"limit": 10}, CALLER)
    victim_view = call_tool("get_transactions", {"limit": 10}, OTHER_CUSTOMER)

    attack = call_tool("get_transactions", {"limit": 10, "user_id": OTHER_CUSTOMER}, CALLER)

    assert attack == caller_view
    assert attack != victim_view
    returned_ids = {item["id"] for item in attack["transactions"]}
    victim_ids = {item["id"] for item in victim_view["transactions"]}
    assert returned_ids.isdisjoint(victim_ids)


def test_a_smuggled_user_id_cannot_reach_another_settlement_schedule() -> None:
    """Same attack on the other tool: the injected identity always wins."""
    attack = call_tool(
        "get_settlement_schedule",
        {"date_from": "2026-01-01", "date_to": "2026-12-31", "user_id": OTHER_CUSTOMER},
        CALLER,
    )

    assert attack == call_tool(
        "get_settlement_schedule",
        {"date_from": "2026-01-01", "date_to": "2026-12-31"},
        CALLER,
    )


def test_unknown_customer_is_an_empty_result_not_an_error() -> None:
    """An id with no record is answerable, so it must not raise."""
    result = call_tool("get_transactions", {}, "nao-existe")

    assert result == {"found": False, "transactions": []}


def test_settlement_schedule_filters_by_date_window() -> None:
    """The date window is the model's to choose; the identity is not."""
    result = call_tool(
        "get_settlement_schedule", {"date_from": "2026-09-01", "date_to": "2026-09-01"}, CALLER
    )

    assert [entry["date"] for entry in result["entries"]] == ["2026-09-01"]
    assert result["entries"][0]["reference_date"] == "2026-08-31"


def test_card_numbers_are_masked_at_the_source() -> None:
    """No unmasked pan ever exists in the process, so none can leak into a log."""
    result = call_tool("get_transactions", {"limit": 50}, CALLER)

    for transaction in result["transactions"]:
        assert re.fullmatch(r"\*{4} \*{4} \*{4} \d{4}", transaction["card"])


def test_both_tools_are_registered() -> None:
    """The support agent is limited to exactly these two."""
    assert set(SUPPORT_TOOLS) == {"get_settlement_schedule", "get_transactions"}
