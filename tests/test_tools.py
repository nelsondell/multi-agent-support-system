"""Support tools: what the model may ask for, and whose data it can reach."""

import re
from datetime import date

import pytest

from app.tools.account import (
    SUPPORT_TOOLS,
    brl,
    call_tool,
    get_settlement_schedule,
    get_transactions,
)

OTHER_CUSTOMER = "cliente7771"
CALLER = "cliente1988"
TODAY = date(2026, 10, 14)


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

    assert [entry["credit_date"] for entry in result["entries"]] == ["2026-09-01"]
    assert result["entries"][0]["sale_date"] == "2026-08-31"


def test_card_numbers_are_masked_at_the_source() -> None:
    """No unmasked pan ever exists in the process, so none can leak into a log."""
    result = call_tool("get_transactions", {"limit": 50}, CALLER)

    for transaction in result["transactions"]:
        assert re.fullmatch(r"\*{4} \*{4} \*{4} \d{4}", transaction["card"])


def test_both_tools_are_registered() -> None:
    """The support agent is limited to exactly these two."""
    assert set(SUPPORT_TOOLS) == {"get_settlement_schedule", "get_transactions"}


def test_records_are_read_as_of_the_turns_day() -> None:
    """Yesterday, today and tomorrow keep landing on the same entries, whatever the real date. The
    status comes from the credit date against today, so money credited yesterday is "liquidado"
    and money crediting today is still "previsto": nothing stored can contradict the date."""
    window = {"date_from": "2026-10-13", "date_to": "2026-10-15"}

    entries = call_tool("get_settlement_schedule", window, CALLER, TODAY)["entries"]

    assert [(entry["credit_date"], entry["status"]) for entry in entries] == [
        ("2026-10-13", "liquidado"),
        ("2026-10-14", "previsto"),
        ("2026-10-15", "previsto"),
    ]
    assert entries[1]["sale_date"] == "2026-10-13"


def test_an_id_moves_with_its_date() -> None:
    latest = call_tool("get_transactions", {"limit": 1}, CALLER, TODAY)["transactions"][0]

    assert latest["date"] == "2026-10-13"
    assert latest["id"].startswith("TX-20261013-")


def test_amounts_reach_the_model_in_the_merchants_notation() -> None:
    declined = call_tool("get_transactions", {"status": "negada"}, CALLER)["transactions"][0]
    settled = call_tool(
        "get_settlement_schedule", {"date_from": "2026-09-01", "date_to": "2026-09-01"}, CALLER
    )["entries"][0]

    assert declined["amount"] == "R$ 1.240,00"
    assert (settled["gross"], settled["fees"], settled["net"]) == (
        "R$ 239,40",
        "R$ 8,14",
        "R$ 231,26",
    )


@pytest.mark.parametrize(
    ("amount", "written"),
    [(1240.0, "R$ 1.240,00"), (8.14, "R$ 8,14"), (0.1, "R$ 0,10"), (1234567.5, "R$ 1.234.567,50")],
)
def test_brl_groups_thousands_with_dots_and_decimals_with_a_comma(amount, written) -> None:
    assert brl(amount) == written


def test_yesterdays_sales_are_found_by_sale_date_and_credit_today() -> None:
    """S03: the money from yesterday's sales, not the money that landed yesterday."""
    window = {"date_from": "2026-10-13", "date_to": "2026-10-13"}

    by_sale = call_tool(
        "get_settlement_schedule", {**window, "date_field": "sale_date"}, CALLER, TODAY
    )
    by_credit = call_tool("get_settlement_schedule", window, CALLER, TODAY)

    assert by_sale["entries"] == [
        {
            "sale_date": "2026-10-13",
            "credit_date": "2026-10-14",
            "gross": "R$ 239,40",
            "fees": "R$ 8,14",
            "net": "R$ 231,26",
            "status": "previsto",
        }
    ]
    assert by_credit["entries"][0]["sale_date"] == "2026-10-10"
