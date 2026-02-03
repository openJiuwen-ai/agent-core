# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
E2E Test Case for PGVectorStore usage in Workflow and Agent scenarios.
"""

import pytest

pgvector = pytest.importorskip("pgvector", reason="PGVector not installed")

from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)
import pytest
from sqlalchemy import (
    MetaData,
    Table,
    Column,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from pgvector.sqlalchemy import Vector

from openjiuwen.core.retrieval.knowledge_base import KnowledgeBase
from openjiuwen.core.retrieval.simple_knowledge_base import SimpleKnowledgeBase
from openjiuwen.core.retrieval.common.config import (
    KnowledgeBaseConfig,
    VectorStoreConfig,
)
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.vector_store.pg_store import PGVectorStore
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.indexing.processor.chunker.base import Chunker
from openjiuwen.core.retrieval.indexing.indexer.base import Indexer
from openjiuwen.core.retrieval.embedding.base import Embedding


# Mock components to isolate VectorStore testing
class MockEmbedding(Embedding):
    def __init__(self):
        # Base class Embedding is an ABC without __init__ or with empty __init__
        # It does NOT take config in __init__ based on base.py reading
        pass

    async def embed_documents(self, texts, batch_size=None, **kwargs):
        return [[0.1, 0.2] for _ in texts]

    async def embed_query(self, text, **kwargs):
        return [0.1, 0.2]

    @property
    def dimension(self):
        return 2


class MockParser(Parser):
    async def parse(self, file_path, **kwargs):
        return [Document(text="content from file", metadata={"source": file_path})]


class MockChunker(Chunker):
    def chunk_documents(self, documents):
        # Return documents as chunks directly for simplicity
        return documents


class MockIndexer(Indexer):
    def __init__(self, vector_store):
        self.vector_store = vector_store
        # Mock attributes for validation
        self.database_name = vector_store.config.database_name
        self.distance_metric = vector_store.config.distance_metric
        self.text_field = vector_store.text_field
        self.vector_field = vector_store.vector_field_config.vector_field
        self.sparse_vector_field = vector_store.sparse_vector_field
        self.metadata_field = vector_store.metadata_field
        self.doc_id_field = vector_store.doc_id_field

    async def build_index(self, chunks, **kwargs):
        # Simply add to vector store
        data = [
            {
                "id": c.id_,
                "content": c.text,
                "embedding": [0.1, 0.2],
                "metadata": c.metadata
            }
            for c in chunks
        ]
        await self.vector_store.add(data)
        return True

    async def update_index(self, *args, **kwargs):
        return True

    async def delete_index(self, *args, **kwargs):
        return True

    async def index_exists(self, *args, **kwargs):
        return True

    async def get_index_info(self, *args, **kwargs):
        return {}


@pytest.fixture
def mock_pg_session():
    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)
    mock_session.execute = AsyncMock()

    mock_transaction = MagicMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
    mock_transaction.__aexit__ = AsyncMock(return_value=None)
    mock_session.begin.return_value = mock_transaction
    return mock_session


@pytest.mark.asyncio
@patch("openjiuwen.core.retrieval.vector_store.pg_store.create_async_engine")
@patch("openjiuwen.core.retrieval.vector_store.pg_store.async_sessionmaker")
async def test_workflow_agent_kb_flow(mock_sessionmaker, mock_create_engine, mock_pg_session):
    """
    Scenario 1: Workflow Agent builds a Knowledge Base backed by PGVectorStore.
    Flow: User -> KB.add_documents -> Indexer -> PGVectorStore.add
          User -> KB.retrieve -> Retriever -> PGVectorStore.search
    """
    # Setup Mocks
    mock_sessionmaker.return_value = MagicMock(return_value=mock_pg_session)
    mock_create_engine.return_value = AsyncMock()

    # 1. Initialize Components
    kb_config = KnowledgeBaseConfig(kb_id="workflow_kb", index_type="vector")
    vs_config = VectorStoreConfig(collection_name="pg_collection", distance_metric="cosine")

    # PG Store

    pg_store = PGVectorStore(
        config=vs_config,
        pg_uri="postgresql+asyncpg://mock_user:mock_pass@localhost/mock_db"
    )
    # Inject real table object to avoid sqlalchemy coercion error with MagicMock
    pg_store.table_ref = Table(
        "pg_collection", MetaData(),
        Column("id", String, primary_key=True),
        Column("content", Text),
        Column("metadata", JSONB),
        Column("embedding", Vector(2))
    )
    # Mock real columns for validation if needed, or rely on loose mocks

    # Workflow Components
    embed_model = MockEmbedding()
    indexer = MockIndexer(pg_store)
    chunker = MockChunker()

    kb = SimpleKnowledgeBase(
        config=kb_config,
        vector_store=pg_store,
        embed_model=embed_model,
        index_manager=indexer,
        chunker=chunker,
        parser=MockParser()
    )

    # 2. Execute Workflow: Add Documents
    docs = [Document(text="This is a workflow document", metadata={"type": "report"})]
    await kb.add_documents(docs)

    # Verify: PGVectorStore.add called
    assert mock_pg_session.execute.called
    # Check if insert statement was executed
    # We can check call count or args if we dive deep into SQLAlchemy structure
    # For E2E, verifying execution happened is good first step.

    mock_pg_session.execute.reset_mock()

    # 3. Execute Workflow: Retrieval
    # Mock search result
    mock_row = MagicMock()
    mock_row.id = "1"
    mock_row.content = "This is a workflow document"
    mock_row.distance = 0.1
    mock_row.metadata = {"type": "report"}
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [mock_row]
    mock_pg_session.execute.return_value = mock_result

    results = await kb.retrieve("workflow query")

    # Verify: PGVectorStore.search called
    assert mock_pg_session.execute.called
    assert len(results) == 1
    assert results[0].text == "This is a workflow document"


# Mock Agent classes for Scene 2
class MockRetrievalTool:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb
        self.name = "retrieval_tool"

    async def run(self, query: str):
        results = await self.kb.retrieve(query)
        return "\n".join([r.text for r in results])


class MockLLMAgent:
    def __init__(self, tools):
        self.tools = {t.name: t for t in tools}

    async def act(self, instruction: str):
        # Simulate LLM deciding to call tool
        if "search" in instruction or "find" in instruction:
            tool_name = "retrieval_tool"
            # Extract query (simplified)
            query = instruction.replace("search for ", "").replace("find ", "")
            result = await self.tools[tool_name].run(query)
            return f"Found info: {result}"
        return "I don't know."


@pytest.mark.asyncio
@patch("openjiuwen.core.retrieval.vector_store.pg_store.create_async_engine")
@patch("openjiuwen.core.retrieval.vector_store.pg_store.async_sessionmaker")
async def test_llm_agent_retrieval(mock_sessionmaker, mock_create_engine, mock_pg_session):
    """
    Scenario 2: LLM Agent uses a Retrieval Tool backed by PGVectorStore.
    Flow: User -> Agent.act -> Tool.run -> KB.retrieve -> PGVectorStore.search
    """
    # Setup Mocks
    mock_sessionmaker.return_value = MagicMock(return_value=mock_pg_session)
    mock_create_engine.return_value = AsyncMock()

    # 1. Setup Backend (PG Store + KB)
    vs_config = VectorStoreConfig(collection_name="agent_collection", distance_metric="euclidean")
    pg_store = PGVectorStore(
        config=vs_config,
        pg_uri="postgresql+asyncpg://mock:mock@localhost/db"
    )
    pg_store.table_ref = Table(
        "agent_collection", MetaData(),
        Column("id", String, primary_key=True),
        Column("content", Text),
        Column("metadata", JSONB),
        Column("embedding", Vector(2))
    )

    kb_config = KnowledgeBaseConfig(kb_id="agent_kb", index_type="vector")
    kb = SimpleKnowledgeBase(
        config=kb_config,
        vector_store=pg_store,
        embed_model=MockEmbedding(),
        index_manager=MockIndexer(pg_store),  # Needed for validation
        chunker=MockChunker()
    )

    # 2. Setup Agent
    tool = MockRetrievalTool(kb)
    agent = MockLLMAgent(tools=[tool])

    # 3. Simulate Agent Execution
    # Mock DB return for search
    mock_row = MagicMock()
    mock_row.id = "doc_1"
    mock_row.content = "Secret Agent Info"
    mock_row.distance = 0.05
    mock_row.metadata = {}

    mock_result = MagicMock()
    mock_result.fetchall.return_value = [mock_row]
    mock_pg_session.execute.return_value = mock_result

    response = await agent.act("search for secret info")

    # Verify
    assert "Found info: Secret Agent Info" in response
    assert mock_pg_session.execute.called
