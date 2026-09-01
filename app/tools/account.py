"""The support agent's two tools.

Both read the caller's own records and nothing else. `user_id` arrives through `InjectedState`,
so it is absent from the schema the model sees: there is no argument a prompt could set to reach
another merchant's data. Card numbers are already masked in the source file, so no unmasked pan
ever exists in this process.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

CUSTOMERS_PATH = Path("data/customers.json")

MAX_TRANSACTIONS = 50


@lru_cache(maxsize=1)
def _customers() -> dict[str, Any]:
    """Load the account records once."""
    if not CUSTOMERS_PATH.is_file():
        raise FileNotFoundError(f"customer records not found at {CUSTOMERS_PATH}")
    return json.loads(CUSTOMERS_PATH.read_text(encoding="utf-8"))


def _record(user_id: str) -> dict[str, Any] | None:
    """The caller's record, or None when the id is unknown."""
    return _customers().get(user_id)


@tool
def get_settlement_schedule(
    date_from: str,
    date_to: str,
    user_id: Annotated[str, InjectedState("user_id")],
) -> dict[str, Any]:
    """Consulta a agenda de liquidação do cliente entre duas datas.

    Args:
        date_from: primeira data do intervalo, no formato AAAA-MM-DD.
        date_to: última data do intervalo, no formato AAAA-MM-DD.

    Returns:
        As parcelas previstas ou liquidadas no intervalo, com valor bruto, taxas, valor líquido,
        situação e a data de referência do dado.
    """
    record = _record(user_id)
    if record is None:
        return {"found": False, "entries": []}
    entries = [entry for entry in record["settlement"] if date_from <= entry["date"] <= date_to]
    return {"found": True, "entries": sorted(entries, key=lambda entry: entry["date"])}


@tool
def get_transactions(
    user_id: Annotated[str, InjectedState("user_id")],
    status: str | None = None,
    limit: int = 10,
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
    record = _record(user_id)
    if record is None:
        return {"found": False, "transactions": []}
    transactions = record["transactions"]
    if status:
        transactions = [item for item in transactions if item["status"] == status]
    transactions = sorted(transactions, key=lambda item: item["date"], reverse=True)
    return {"found": True, "transactions": transactions[: min(limit, MAX_TRANSACTIONS)]}


SUPPORT_TOOLS = {
    get_settlement_schedule.name: get_settlement_schedule,
    get_transactions.name: get_transactions,
}


def call_tool(name: str, arguments: dict[str, Any], user_id: str) -> dict[str, Any]:
    """Invoke a support tool with the caller's identity injected, never a model-supplied one.

    Arguments are filtered down to the fields the model is actually allowed to set, so an
    `user_id` smuggled into the model's arguments is dropped before it can reach the lookup.
    """
    selected = SUPPORT_TOOLS[name]
    allowed = set(selected.tool_call_schema.model_fields)
    safe_arguments = {key: value for key, value in arguments.items() if key in allowed}
    return selected.invoke({**safe_arguments, "user_id": user_id})
