"""The CI backend: a valid, deterministic instance of every schema the graph asks for."""

import pytest

from app.graph.build import build_graph
from app.graph.knowledge import KnowledgeAnswer
from app.graph.memory import ConversationSummary
from app.graph.router import RouterDecision
from app.graph.state import GraphState
from app.graph.support import SupportAnswer, SupportPlan
from app.main import to_response
from app.providers.fake import FakeProvider
from app.providers.llm import build_provider
from app.rag.retrieve import HybridRetriever
from app.settings import Settings

# Every `schema=` passed to `provider.structured` under app/graph/.
GRAPH_SCHEMAS = [RouterDecision, KnowledgeAnswer, SupportPlan, SupportAnswer, ConversationSummary]


@pytest.mark.parametrize("schema", GRAPH_SCHEMAS, ids=lambda schema: schema.__name__)
async def test_returns_a_valid_instance_of_every_graph_schema(schema):
    result = await FakeProvider().structured(system="", user="", schema=schema)

    assert isinstance(result, schema)
    assert schema.model_validate(result.model_dump()) == result


@pytest.mark.parametrize("schema", GRAPH_SCHEMAS, ids=lambda schema: schema.__name__)
async def test_answers_the_same_way_every_time(schema):
    provider = FakeProvider()

    first = await provider.structured(system="a", user="b", schema=schema)
    second = await provider.structured(system="c", user="d", schema=schema)

    assert first == second


def test_the_fake_backend_needs_no_key():
    provider = build_provider(Settings(llm_backend="fake", openai_api_key=None))

    assert isinstance(provider, FakeProvider)


async def test_drives_a_knowledge_question_to_a_sourced_answer(sample_chunks, fake_collection):
    above_threshold = {chunk.id: 0.9 for chunk in sample_chunks}
    retriever = HybridRetriever(sample_chunks, fake_collection(above_threshold), top_k=5)
    graph = build_graph(FakeProvider(), Settings(llm_backend="fake"), retriever)

    final = await graph.ainvoke(GraphState(message="What is the Get Smart?", user_id="cliente1988"))
    response = to_response(final, "c1")

    assert response.conversation_id == "c1"
    assert response.escalated is False
    assert response.target_agent is not None
    assert response.sources
