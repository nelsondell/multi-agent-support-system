"""The support agent's two tools.

Both read the caller's own records and nothing else. `user_id` arrives through `InjectedState`,
so it is absent from the schema the model sees: there is no argument a prompt could set to reach
another merchant's data. Card numbers are already masked in the source file, so no unmasked pan
ever exists in this process.

The records are mock data written as of one day. Each turn reads them moved to its own date, so
"yesterday" and "tomorrow" always land on the same entries, and every amount leaves as the
merchant writes it, "R$ 1.240,00", so the model never has to format, or round, a raw float.
"""

import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

CUSTOMERS_PATH = Path("data/customers.json")

MAX_TRANSACTIONS = 50

RECORDED_ON = date(2026, 9, 1)
"""The day the mock records describe as today: cliente1988's terminal was last seen that morning
and the 31 August sales settle on it."""

MONEY_FIELDS = {"amount", "gross", "fees", "net"}

_DAY = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
"""An ISO date, alone or heading a timestamp."""
_DAY_IN_ID = re.compile(r"(?<=-)\d{8}(?=-)")
"""The date inside an id such as TX-20260831-0001."""


@lru_cache(maxsize=1)
def _customers() -> dict[str, Any]:
    """Load the account records once."""
    if not CUSTOMERS_PATH.is_file():
        raise FileNotFoundError(f"customer records not found at {CUSTOMERS_PATH}")
    return json.loads(CUSTOMERS_PATH.read_text(encoding="utf-8"))


def brl(amount: float) -> str:
    """An amount in the merchant's notation: 1240.0 becomes "R$ 1.240,00"."""
    grouped = f"{Decimal(str(amount)).quantize(Decimal('0.01')):,}"
    return "R$ " + grouped.translate(str.maketrans(",.", ".,"))


def shift_dates(value: Any, days: int) -> Any:
    """Every date in a record moved by `days`, including the ones embedded in ids."""
    if isinstance(value, dict):
        return {key: shift_dates(item, days) for key, item in value.items()}
    if isinstance(value, list):
        return [shift_dates(item, days) for item in value]
    if not isinstance(value, str):
        return value
    moved = _DAY.sub(
        lambda found: (date.fromisoformat(found[0]) + timedelta(days)).isoformat(), value
    )
    return _DAY_IN_ID.sub(
        lambda found: (datetime.strptime(found[0], "%Y%m%d") + timedelta(days)).strftime("%Y%m%d"),
        moved,
    )


def _record(user_id: str, today: date | None) -> dict[str, Any] | None:
    """The caller's record as of `today`, or None when the id is unknown.

    Without a date the record is read as written, which is what the offline tests rely on.
    """
    record = _customers().get(user_id)
    if record is None or today is None:
        return record
    return shift_dates(record, (today - RECORDED_ON).days)


def _with_money(entry: dict[str, Any]) -> dict[str, Any]:
    """The entry with every amount written as the merchant reads it."""
    return {key: brl(value) if key in MONEY_FIELDS else value for key, value in entry.items()}


@tool
def get_settlement_schedule(
    date_from: str,
    date_to: str,
    user_id: Annotated[str, InjectedState("user_id")],
    today: Annotated[date | None, InjectedState("today")] = None,
) -> dict[str, Any]:
    """Consulta a agenda de liquidação do cliente entre duas datas.

    Args:
        date_from: primeira data do intervalo, no formato AAAA-MM-DD.
        date_to: última data do intervalo, no formato AAAA-MM-DD.

    Returns:
        As parcelas previstas ou liquidadas no intervalo, com valor bruto, taxas, valor líquido,
        situação e a data de referência do dado.
    """
    record = _record(user_id, today)
    if record is None:
        return {"found": False, "entries": []}
    entries = [entry for entry in record["settlement"] if date_from <= entry["date"] <= date_to]
    ordered = sorted(entries, key=lambda entry: entry["date"])
    return {"found": True, "entries": [_with_money(entry) for entry in ordered]}


@tool
def get_transactions(
    user_id: Annotated[str, InjectedState("user_id")],
    status: str | None = None,
    limit: int = 10,
    today: Annotated[date | None, InjectedState("today")] = None,
) -> dict[str, Any]:
    """Lista as transações mais recentes do cliente.

    Args:
        status: filtra por situação da transação, por exemplo "aprovada", "negada" ou "estornada".
            Deixe vazio para trazer todas.
        limit: número máximo de transações retornadas, no máximo 50.

    Returns:
        As transações do cliente, da mais recente para a mais antiga, com número do cartão já
        mascarado na origem.
    """
    record = _record(user_id, today)
    if record is None:
        return {"found": False, "transactions": []}
    transactions = record["transactions"]
    if status:
        transactions = [item for item in transactions if item["status"] == status]
    transactions = sorted(transactions, key=lambda item: item["date"], reverse=True)
    latest = transactions[: min(limit, MAX_TRANSACTIONS)]
    return {"found": True, "transactions": [_with_money(item) for item in latest]}


SUPPORT_TOOLS = {
    get_settlement_schedule.name: get_settlement_schedule,
    get_transactions.name: get_transactions,
}


def call_tool(
    name: str, arguments: dict[str, Any], user_id: str, today: date | None = None
) -> dict[str, Any]:
    """Invoke a support tool with the caller's identity and the turn's date injected, never
    model-supplied ones.

    Arguments are filtered down to the fields the model is actually allowed to set, so an
    `user_id` smuggled into the model's arguments is dropped before it can reach the lookup.
    """
    selected = SUPPORT_TOOLS[name]
    allowed = set(selected.tool_call_schema.model_fields)
    safe_arguments = {key: value for key, value in arguments.items() if key in allowed}
    return selected.invoke({**safe_arguments, "user_id": user_id, "today": today})
