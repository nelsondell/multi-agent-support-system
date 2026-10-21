"""Identity, roles and LGPD.

A merchant is whoever a signed token names, operators act by role, and a merchant is held to a
rate. A merchant can erase a conversation and nothing of it is left; idle conversations are
purged after RETENTION_DAYS; and the audit log records every operator action and every erasure,
without a word of what was said, in a table the API itself cannot rewrite. The graph behind is
scripted, and every turn escalates a case.
"""

import sqlite3
from contextlib import AsyncExitStack
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.auth import RateLimiter
from app.contracts import Intent, TargetAgent
from app.graph.human_review import HandoffDraft, OperatorReply
from app.graph.memory import thread_id
from app.graph.router import PlanStep, RouterDecision
from app.graph.support import SupportAction, SupportPlan
from app.main import create_app
from app.rag.retrieve import HybridRetriever
from app.retention import purge
from app.settings import Settings
from app.storage import open_storage
from tests.identity import DEFAULTS, FORGER, IDENTITY, serve

CALLER = "cliente1988"
CPF = "123.456.789-00"
COMPLAINT = "Por que o estorno da minha venda ainda não apareceu?"
ESCALATES = {
    RouterDecision: RouterDecision(
        intent=Intent.INDIVIDUAL_CASE,
        confidence=0.9,
        rationale="t",
        steps=[PlanStep(agent=TargetAgent.SUPPORT, query_pt=COMPLAINT)],
    ),
    SupportPlan: SupportPlan(action=SupportAction.ESCALATE_INDIVIDUAL_CASE),
    HandoffDraft: HandoffDraft(draft="Estamos verificando."),
    OperatorReply: OperatorReply(answer="O estorno foi aprovado hoje."),
}


@pytest.fixture
def service(tmp_path, sample_chunks, fake_collection, build_test_graph):
    """The API over SQLite stores in `tmp_path`, with the suite's identity provider."""

    async def start(exits: AsyncExitStack) -> tuple[httpx.AsyncClient, object]:
        settings = Settings(state_dir=str(tmp_path / "state"))
        storage = await exits.enter_async_context(open_storage(settings))
        retriever = HybridRetriever(sample_chunks, fake_collection(), top_k=5)
        graph = build_test_graph(ESCALATES, checkpointer=storage.checkpointer, queue=storage.queue)
        api = create_app()
        serve(api, settings, storage, graph, retriever)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://t")
        return await exits.enter_async_context(client), api

    return start


def chat(http, headers: dict, conversation_id: str = "c1"):
    payload = {"message": COMPLAINT, "conversation_id": conversation_id}
    return http.post("/chat", json=payload, headers=headers)


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="missing"),
        pytest.param({"Authorization": "Bearer not-a-jwt"}, id="malformed"),
        pytest.param(FORGER.merchant(CALLER), id="forged"),
        pytest.param(
            {"Authorization": "Bearer " + IDENTITY.token(CALLER, DEFAULTS.merchant_audience, -60)},
            id="expired",
        ),
        pytest.param(IDENTITY.operator(), id="wrong-audience"),
        pytest.param(IDENTITY.merchant(CALLER, iss="someone-else"), id="wrong-issuer"),
    ],
)
async def test_chat_needs_a_valid_merchant_token(service, headers):
    async with AsyncExitStack() as exits:
        http, _ = await service(exits)
        response = await chat(http, headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_the_merchant_is_the_token_subject_and_nobody_else_sees_the_conversation(service):
    async with AsyncExitStack() as exits:
        http, _ = await service(exits)
        await chat(http, IDENTITY.merchant(CALLER))
        own = await http.get("/conversations/c1", headers=IDENTITY.merchant(CALLER))
        other = await http.get("/conversations/c1", headers=IDENTITY.merchant("cliente7771"))

    assert own.status_code == 200 and own.json()["status"] == "pending_human"
    assert other.status_code == 404


@pytest.mark.parametrize(
    ("headers", "route", "expected"),
    [
        (IDENTITY.operator(role="operator"), "/handoffs", 200),
        (IDENTITY.operator(role="supervisor"), "/handoffs", 200),
        (IDENTITY.merchant(CALLER), "/handoffs", 401),
        (
            {"Authorization": "Bearer " + IDENTITY.token("ana", DEFAULTS.operator_audience)},
            "/handoffs",
            403,
        ),
        (IDENTITY.operator(role="operator"), "/audit", 403),
        (IDENTITY.operator(role="supervisor"), "/audit", 200),
        (IDENTITY.merchant(CALLER), "/audit", 401),
    ],
    ids=[
        "operator-cases",
        "supervisor-cases",
        "merchant-cases",
        "no-role-cases",
        "operator-audit",
        "supervisor-audit",
        "merchant-audit",
    ],
)
async def test_roles(service, headers, route, expected):
    async with AsyncExitStack() as exits:
        http, _ = await service(exits)
        response = await http.get(route, headers=headers)

    assert response.status_code == expected


async def test_a_merchant_past_the_rate_limit_gets_429_with_retry_after(service):
    async with AsyncExitStack() as exits:
        http, api = await service(exits)
        api.state.limiter = RateLimiter(per_minute=2)
        statuses = [(await chat(http, IDENTITY.merchant(CALLER), f"c{n}")) for n in range(3)]
        other = await chat(http, IDENTITY.merchant("cliente2042"))

    assert [response.status_code for response in statuses] == [200, 200, 429]
    assert 1 <= int(statuses[2].headers["retry-after"]) <= 60
    assert other.status_code == 200, "the limit is per merchant"


# --- LGPD and the audit log ------------------------------------------------------------------

OPERATOR, SUPERVISOR = IDENTITY.operator(), IDENTITY.operator("bia", "supervisor")


def rows(path, sql: str, *params) -> list:
    with sqlite3.connect(path) as connection:
        return connection.execute(sql, params).fetchall()


async def test_erasing_a_conversation_leaves_nothing_of_it(service, tmp_path):
    state = tmp_path / "state"
    thread = thread_id(CALLER, "c1")
    async with AsyncExitStack() as exits:
        http, _ = await service(exits)
        protocol = (await chat(http, IDENTITY.merchant(CALLER))).json()["escalation"]["protocol"]
        note = {"message": f"meu CPF é {CPF}", "conversation_id": "c1"}
        await http.post("/chat", json=note, headers=IDENTITY.merchant(CALLER))
        stranger = await http.delete("/me/conversations/c1", headers=IDENTITY.merchant("x9"))
        erased = await http.delete("/me/conversations/c1", headers=IDENTITY.merchant(CALLER))
        again = await http.delete("/me/conversations/c1", headers=IDENTITY.merchant(CALLER))
        history = await http.get("/conversations/c1", headers=IDENTITY.merchant(CALLER))
        case = await http.get(f"/handoffs/{protocol}", headers=OPERATOR)
        audit = (await http.get("/audit", headers=SUPERVISOR)).json()

    assert stranger.status_code == 404, "another merchant cannot erase it, or learn it exists"
    assert erased.status_code == 204 and again.status_code == 404
    assert history.status_code == 404 and case.status_code == 404
    checkpoints = state / "checkpoints.sqlite"
    for table in ("checkpoints", "writes"):
        assert rows(checkpoints, f"SELECT 1 FROM {table} WHERE thread_id = ?", thread) == []
    assert rows(state / "handoffs.sqlite", "SELECT 1 FROM handoffs") == []
    assert {"action": "conversation.erased", "actor": CALLER, "target": thread}.items() <= {
        **audit[0]
    }.items()
    assert not any(CPF in str(entry) or "estorno" in str(entry) for entry in audit)


async def test_every_operator_action_is_in_the_audit_log(service):
    async with AsyncExitStack() as exits:
        http, _ = await service(exits)
        protocol = (await chat(http, IDENTITY.merchant(CALLER))).json()["escalation"]["protocol"]
        await http.get("/handoffs", headers=OPERATOR)
        await http.get(f"/handoffs/{protocol}", headers=OPERATOR)
        reply = {"action": "reply", "answer": "O estorno foi aprovado hoje."}
        await http.post(f"/handoffs/{protocol}/resume", json=reply, headers=OPERATOR)
        audit = (await http.get("/audit", headers=SUPERVISOR)).json()

    assert [(entry["action"], entry["target"]) for entry in audit] == [
        ("case.reply", protocol),
        ("case.read", protocol),
        ("cases.listed", "*"),
    ]
    assert {(entry["actor"], entry["role"]) for entry in audit} == {("ana", "operator")}


@pytest.mark.parametrize("statement", ["UPDATE audit SET actor = 'x'", "DELETE FROM audit"])
async def test_the_audit_log_cannot_be_rewritten(tmp_path, statement):
    settings = Settings(state_dir=str(tmp_path / "state"))
    async with open_storage(settings) as storage:
        await storage.audit.append("ana", "operator", "case.read", "ESC-1")

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        rows(tmp_path / "state" / "handoffs.sqlite", statement)
    assert len(rows(tmp_path / "state" / "handoffs.sqlite", "SELECT 1 FROM audit")) == 1


async def test_retention_purges_only_conversations_idle_for_longer(service, tmp_path):
    settings = Settings(state_dir=str(tmp_path / "state"), retention_days=90)
    async with AsyncExitStack() as exits:
        http, _ = await service(exits)
        for conversation in ("c1", "c2"):
            await chat(http, IDENTITY.merchant(CALLER), conversation)
        kept = await purge(settings)
        purged = await purge(settings, now=datetime.now(UTC) + timedelta(days=91))
        after = await http.get("/conversations/c1", headers=IDENTITY.merchant(CALLER))
        audit = (await http.get("/audit", headers=SUPERVISOR)).json()

    assert (kept, purged) == (0, 2)
    assert after.status_code == 404
    assert rows(tmp_path / "state" / "handoffs.sqlite", "SELECT 1 FROM handoffs") == []
    assert audit[0]["action"] == "retention.purged" and audit[0]["detail"].startswith("2 idle")
