"""Identity, roles and LGPD: a merchant is whoever a signed token names, operators act by role, a
merchant is held to a rate, and an erased or idle conversation leaves nothing behind but an audit
entry the API cannot rewrite. Every scripted turn escalates a case."""

import json
import sqlite3
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta

import pytest

from app.auth import TokenVerifier
from app.contracts import Intent, TargetAgent
from app.graph.human_review import HandoffDraft
from app.graph.memory import thread_id
from app.graph.router import PlanStep, RouterDecision
from app.graph.support import SupportAction, SupportPlan
from app.retention import purge
from app.settings import Settings
from app.storage import open_storage
from tests.identity import DEFAULTS, FORGER, IDENTITY

CALLER, CPF = "cliente1988", "123.456.789-00"
COMPLAINT = "Por que o estorno da minha venda ainda não apareceu?"
SUPERVISOR = IDENTITY.operator("bia", "supervisor")
STEP = PlanStep(agent=TargetAgent.SUPPORT, query_pt=COMPLAINT)
ESCALATES = {
    RouterDecision: RouterDecision(
        intent=Intent.INDIVIDUAL_CASE, confidence=0.9, rationale="t", steps=[STEP]
    ),
    SupportPlan: SupportPlan(action=SupportAction.ESCALATE_INDIVIDUAL_CASE),
    HandoffDraft: HandoffDraft(draft="Estamos verificando."),
}


class Escalates:
    async def structured(self, *, system: str, user: str, schema: type):
        return ESCALATES[schema]


def chat(http, headers: dict, conversation_id: str = "c1", message: str = COMPLAINT):
    payload = {"message": message, "conversation_id": conversation_id}
    return http.post("/chat", json=payload, headers=headers)


def rows(path, sql: str, *params) -> list:
    with sqlite3.connect(path) as connection:
        return connection.execute(sql, params).fetchall()


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing"),
        pytest.param(bearer("not-a-jwt"), id="malformed"),
        pytest.param(FORGER.merchant(CALLER), id="forged"),
        pytest.param(bearer(IDENTITY.token(CALLER, DEFAULTS.merchant_audience, -60)), id="expired"),
        pytest.param(IDENTITY.operator(), id="wrong-audience"),
        pytest.param(IDENTITY.merchant(CALLER, iss="someone-else"), id="wrong-issuer"),
    ],
)
async def test_chat_needs_a_valid_merchant_token(api, headers):
    async with AsyncExitStack() as exits:
        response = await chat(await api(exits, Escalates()), headers)

    assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"


NO_ROLE = bearer(IDENTITY.token("ana", DEFAULTS.operator_audience))


@pytest.mark.parametrize(
    ("headers", "route", "expected"),
    [
        (IDENTITY.operator(), "/handoffs", 200),
        (SUPERVISOR, "/handoffs", 200),
        (IDENTITY.merchant(CALLER), "/handoffs", 401),
        (NO_ROLE, "/handoffs", 403),
        (IDENTITY.operator(), "/audit", 403),
        (SUPERVISOR, "/audit", 200),
        (IDENTITY.merchant(CALLER), "/audit", 401),
    ],
)
async def test_roles(api, headers, route, expected):
    async with AsyncExitStack() as exits:
        response = await (await api(exits, Escalates())).get(route, headers=headers)

    assert response.status_code == expected


async def test_a_merchant_past_the_rate_limit_gets_429_with_retry_after(api):
    async with AsyncExitStack() as exits:
        http = await api(exits, Escalates(), rate_limit_per_minute=2)
        statuses = [await chat(http, IDENTITY.merchant(CALLER), f"c{n}") for n in range(3)]
        other = await chat(http, IDENTITY.merchant("cliente2042"))

    assert [response.status_code for response in statuses] == [200, 200, 429]
    assert 1 <= int(statuses[2].headers["retry-after"]) <= 60
    assert other.status_code == 200, "the limit is per merchant"


async def test_only_its_merchant_sees_or_erases_a_conversation_and_erasing_leaves_nothing(
    api, tmp_path
):
    state, thread, own = tmp_path / "state", thread_id(CALLER, "c1"), IDENTITY.merchant(CALLER)
    async with AsyncExitStack() as exits:
        http = await api(exits, Escalates())
        await chat(http, own)
        await chat(http, own, message=f"meu CPF é {CPF}")
        peek = await http.get("/conversations/c1", headers=IDENTITY.merchant("x9"))
        stranger = await http.delete("/me/conversations/c1", headers=IDENTITY.merchant("x9"))
        erased = await http.delete("/me/conversations/c1", headers=own)
        history = await http.get("/conversations/c1", headers=own)
        audit = (await http.get("/audit", headers=SUPERVISOR)).json()

    assert peek.status_code == stranger.status_code == 404, "nobody else learns it exists"
    assert erased.status_code == 204 and history.status_code == 404
    for table in ("checkpoints", "writes"):
        found = rows(
            state / "checkpoints.sqlite", f"SELECT 1 FROM {table} WHERE thread_id = ?", thread
        )
        assert found == []
    assert rows(state / "handoffs.sqlite", "SELECT 1 FROM handoffs") == []
    assert (audit[0]["action"], audit[0]["actor"], audit[0]["target"]) == (
        "conversation.erased",
        CALLER,
        thread,
    )
    assert not any(CPF in str(entry) or "estorno" in str(entry) for entry in audit)


@pytest.mark.parametrize("statement", ["UPDATE audit SET actor = 'x'", "DELETE FROM audit"])
async def test_the_audit_log_cannot_be_rewritten(tmp_path, statement):
    async with open_storage(Settings(state_dir=str(tmp_path))) as storage:
        await storage.audit.append("ana", "operator", "case.read", "ESC-1")

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        rows(tmp_path / "handoffs.sqlite", statement)


async def test_retention_purges_only_conversations_idle_for_longer(api, tmp_path):
    settings = Settings(state_dir=str(tmp_path / "state"), retention_days=90)
    async with AsyncExitStack() as exits:
        http = await api(exits, Escalates())
        for conversation in ("c1", "c2"):
            await chat(http, IDENTITY.merchant(CALLER), conversation)
        kept = await purge(settings)
        purged = await purge(settings, now=datetime.now(UTC) + timedelta(days=91))
        audit = (await http.get("/audit", headers=SUPERVISOR)).json()

    assert (kept, purged) == (0, 2)
    assert rows(tmp_path / "state" / "handoffs.sqlite", "SELECT 1 FROM handoffs") == []
    assert audit[0]["action"] == "retention.purged" and audit[0]["detail"].startswith("2 idle")


def test_the_verifier_loads_the_providers_keys_and_fails_fast_without_them(tmp_path):
    jwks = tmp_path / "jwks.json"
    jwks.write_text(json.dumps(IDENTITY.jwks))
    verifier = TokenVerifier.from_settings(Settings(auth_jwks_url=str(jwks)))
    audience = DEFAULTS.merchant_audience

    assert verifier.claims(IDENTITY.token(CALLER, audience), audience)["sub"] == CALLER
    with pytest.raises(ValueError, match="make dev-keys"):
        TokenVerifier.from_settings(Settings(auth_jwks_url=None))
