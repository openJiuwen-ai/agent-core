# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
import shutil
import tempfile
from typing import List

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.core.common.utils.singleton import Singleton
from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, BaseMessage
from openjiuwen.core.foundation.store import create_vector_store
from openjiuwen.core.foundation.store.base_embedding import Embedding
from openjiuwen.core.foundation.store.db.default_db_store import DefaultDbStore
from openjiuwen.core.foundation.store.kv.in_memory_kv_store import InMemoryKVStore
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.memory.config.config import AgentMemoryConfig, MemoryEngineConfig
from openjiuwen.core.memory.long_term_memory import AddMemResult, LongTermMemory
from openjiuwen.core.memory.manage.mem_model.memory_unit import (
    MemoryType,
    OperationType,
)

EMBEDDING_DIM = 32


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

class MockEmbedding(Embedding):
    """Deterministic embedding that returns fixed-size zero vectors."""

    def __init__(self):
        self.limiter = None  # type: ignore[assignment]

    @property
    def dimension(self) -> int:
        return EMBEDDING_DIM

    async def embed_query(self, text: str, **kwargs) -> List[float]:
        return [0.0] * EMBEDDING_DIM

    async def embed_documents(self, texts: List[str], **kwargs) -> List[List[float]]:
        return [[0.0] * EMBEDDING_DIM for _ in texts]


def _make_json_response(json_str: str) -> AssistantMessage:
    """Wrap a JSON string inside ```json``` block as LLM response content."""
    return AssistantMessage(content=f"```json\n{json_str}\n```")


def _analysis_response(
        has_key_information: bool = True,
        variables: list | None = None,
        summary: str = "",
) -> AssistantMessage:
    """Build LLM response for MemoryAnalyzer.analyze."""
    import json

    data = {
        "has_key_information": has_key_information,
        "variables": variables or [],
        "summary": summary,
    }
    return _make_json_response(json.dumps(data, ensure_ascii=False))


def _fragment_response(
        has_explict_instruct: bool = False,
        instruct_memories: list | None = None,
        user_profile: list | None = None,
        semantic_memory: list | None = None,
        episodic_memory: list | None = None,
) -> AssistantMessage:
    """Build LLM response for LongTermMemoryExtractor.extract_long_term_memory."""
    import json

    data = {
        "has_explict_instruct": has_explict_instruct,
        "instruct_memories": instruct_memories or [],
        "user_profile": user_profile or [],
        "semantic_memory": semantic_memory or [],
        "episodic_memory": episodic_memory or [],
    }
    return _make_json_response(json.dumps(data, ensure_ascii=False))


def _semantic_validation_response(correct: bool = True) -> AssistantMessage:
    """Build LLM response for _semantic_validation."""
    text = "CORRECT" if correct else "WRONG"
    return AssistantMessage(content=text)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset LongTermMemory singleton between tests."""
    Singleton._instances.pop(LongTermMemory, None)
    yield
    Singleton._instances.pop(LongTermMemory, None)


@pytest.fixture(autouse=True)
def _mock_callback_trigger():
    """Disable callback framework triggering during tests."""
    with patch(
            "openjiuwen.core.runner.callback.decorator._do_trigger",
            new_callable=AsyncMock,
    ):
        yield


@pytest.fixture
def temp_dir():
    """Create a temp directory for ChromaDB / SQLite; cleaned up after test."""
    d = tempfile.mkdtemp(prefix="mem_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def mock_llm():
    """Mock Model whose invoke returns controlled responses."""
    llm = MagicMock()
    llm.invoke = AsyncMock(
        side_effect=[
            _analysis_response(
                has_key_information=True,
                variables=[{"variable_key": "姓名", "variable_value": "Tom"}],
                summary="用户介绍了自己",
            ),
            _fragment_response(user_profile=["用户名叫Tom"]),
        ]
    )
    return llm


@pytest_asyncio.fixture
async def engine(temp_dir, mock_llm):
    """Create a fully initialized LongTermMemory with real stores."""
    mem = LongTermMemory()

    # Real stores
    kv_store = InMemoryKVStore()
    vector_store = create_vector_store("chroma", persist_directory=temp_dir)
    sqlite_path = os.path.join(temp_dir, "test.db")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{sqlite_path}", pool_pre_ping=True, echo=False
    )
    db_store = DefaultDbStore(engine)
    embedding = MockEmbedding()

    await mem.register_store(
        kv_store=kv_store,
        vector_store=vector_store,
        db_store=db_store,
        embedding_model=embedding,
    )

    # Mock LLM creation so no real API calls happen
    with patch.object(
            LongTermMemory, "_get_llm_from_config", return_value=mock_llm
    ):
        config = MemoryEngineConfig(
            default_model_cfg=ModelRequestConfig(model="mock-model", temperature=0.2),
            default_model_client_cfg=ModelClientConfig(
                client_id="test",
                client_provider="OpenAI",
                api_key="mock-key",
                api_base="http://localhost",
            ),
        )
        mem.set_config(config)

    return mem


@pytest.fixture
def default_config():
    return AgentMemoryConfig()


@pytest.fixture
def user_assistant_messages():
    return [
        BaseMessage(role="user", content="我是Tom"),
        BaseMessage(role="assistant", content="你好Tom"),
    ]


def _assert_empty_result(result: AddMemResult):
    """Assert AddMemResult has no memory data."""
    assert isinstance(result, AddMemResult)
    for field_name in ("variables", "user_profile", "semantic_memory",
                       "episodic_memory", "summary"):
        val = getattr(result, field_name)
        assert val == [] or val is list, f"{field_name} should be empty, got {val!r}"


# ---------------------------------------------------------------------------
# 1. add_messages return value tests
# ---------------------------------------------------------------------------

class TestAddMessagesReturnValue:
    """Verify add_messages returns correct AddMemResult in all branches."""

    # -- Normal flow --

    @pytest.mark.asyncio
    async def test_normal_flow_returns_add_mem_result(
            self, engine, mock_llm, default_config, user_assistant_messages
    ):
        """Normal dialogue returns AddMemResult with extracted memories."""
        result = await engine.add_messages(
            messages=user_assistant_messages,
            agent_config=default_config,
            user_id="u1",
            scope_id="scope1",
        )

        assert isinstance(result, AddMemResult)
        assert len(result.variables) >= 1
        assert result.variables[0].variable_name == "姓名"
        assert result.variables[0].variable_mem == "Tom"
        assert len(result.user_profile) >= 1
        assert result.summary
        assert result.summary[0].summary == "用户介绍了自己"

    @pytest.mark.asyncio
    async def test_multi_round_independent_results(
            self, engine, mock_llm, default_config
    ):
        """Each add_messages call returns independent results for that round."""
        # Round 1: name
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(
                has_key_information=True,
                variables=[{"variable_key": "姓名", "variable_value": "Tom"}],
                summary="用户介绍了自己",
            ),
            _fragment_response(user_profile=["用户名叫Tom"]),
        ])
        msg1 = [
            BaseMessage(role="user", content="我是Tom"),
            BaseMessage(role="assistant", content="你好"),
        ]
        res1 = await engine.add_messages(
            messages=msg1, agent_config=default_config,
            user_id="u1", scope_id="s1",
        )

        # Round 2: occupation
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(
                has_key_information=True,
                variables=[{"variable_key": "职业", "variable_value": "工程师"}],
                summary="用户介绍了职业",
            ),
            _fragment_response(user_profile=["用户是工程师"]),
        ])
        msg2 = [
            BaseMessage(role="user", content="我是工程师"),
            BaseMessage(role="assistant", content="好的"),
        ]
        res2 = await engine.add_messages(
            messages=msg2, agent_config=default_config,
            user_id="u1", scope_id="s1",
        )

        assert len(res1.variables) >= 1
        assert res1.variables[0].variable_name == "姓名"
        assert len(res2.variables) >= 1
        assert res2.variables[0].variable_name == "职业"

    @pytest.mark.asyncio
    async def test_assistant_only_returns_empty(self, engine, mock_llm, default_config):
        """Messages with no human role result in empty AddMemResult."""
        messages = [BaseMessage(role="assistant", content="hello")]

        result = await engine.add_messages(
            messages=messages,
            agent_config=default_config,
            user_id="u1",
            scope_id="s1",
        )

        _assert_empty_result(result)

    # -- Exception branches --

    @pytest.mark.asyncio
    async def test_gen_mem_false_returns_empty(
            self, engine, mock_llm, default_config, user_assistant_messages
    ):
        """gen_mem=False skips memory generation and returns empty AddMemResult."""
        result = await engine.add_messages(
            messages=user_assistant_messages,
            agent_config=default_config,
            user_id="u1",
            scope_id="s1",
            gen_mem=False,
        )

        _assert_empty_result(result)
        mock_llm.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_scope_id_raises_error(
            self, engine, mock_llm, default_config, user_assistant_messages
    ):
        """Empty scope_id fails validation, raises BaseError."""
        with pytest.raises(BaseError) as exc_info:
            await engine.add_messages(
                messages=user_assistant_messages,
                agent_config=default_config,
                user_id="u1",
                scope_id="",
            )

        assert exc_info.value.code == StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR.code
        mock_llm.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_scope_id_with_slash_raises_error(
            self, engine, mock_llm, default_config, user_assistant_messages
    ):
        """scope_id containing '/' fails validation, raises BaseError."""
        with pytest.raises(BaseError) as exc_info:
            await engine.add_messages(
                messages=user_assistant_messages,
                agent_config=default_config,
                user_id="u1",
                scope_id="invalid/scope",
            )

        assert exc_info.value.code == StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR.code
        mock_llm.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_scope_id_too_long_raises_error(
            self, engine, mock_llm, default_config, user_assistant_messages
    ):
        """scope_id exceeding 128 chars fails validation, raises BaseError."""
        with pytest.raises(BaseError) as exc_info:
            await engine.add_messages(
                messages=user_assistant_messages,
                agent_config=default_config,
                user_id="u1",
                scope_id="a" * 129,
            )

        assert exc_info.value.code == StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR.code
        mock_llm.invoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_llm_not_initialized_raises_error(
            self, engine, mock_llm, default_config, user_assistant_messages
    ):
        """When _get_scope_llm returns None, raises BaseError."""
        with patch.object(engine, "_get_scope_llm", new_callable=AsyncMock, return_value=None):
            with pytest.raises(BaseError) as exc_info:
                await engine.add_messages(
                    messages=user_assistant_messages,
                    agent_config=default_config,
                    user_id="u1",
                    scope_id="s1",
                )

        assert exc_info.value.code == StatusCode.MEMORY_ADD_MEMORY_EXECUTION_ERROR.code
        mock_llm.invoke.assert_not_called()

    # -- AgentMemoryConfig effects --

    @pytest.mark.asyncio
    async def test_disable_long_term_mem_no_fragments(
            self, engine, mock_llm, user_assistant_messages
    ):
        """enable_long_term_mem=False: gen_all_memory returns no fragment memories."""
        cfg = AgentMemoryConfig(enable_long_term_mem=False)
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(
                has_key_information=False,
                variables=[{"variable_key": "姓名", "variable_value": "Tom"}],
                summary="",
            ),
        ])

        result = await engine.add_messages(
            messages=user_assistant_messages,
            agent_config=cfg,
            user_id="u1",
            scope_id="s1",
        )

        assert len(result.variables) >= 1
        assert result.user_profile == [] or result.user_profile is list
        assert result.semantic_memory == [] or result.semantic_memory is list

    @pytest.mark.asyncio
    async def test_disable_summary_memory(
            self, engine, mock_llm, user_assistant_messages
    ):
        """enable_summary_memory=False: no summary in result."""
        cfg = AgentMemoryConfig(enable_summary_memory=False)
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(
                has_key_information=True,
                variables=[{"variable_key": "姓名", "variable_value": "Tom"}],
                summary="should be ignored",
            ),
            _fragment_response(user_profile=["用户名叫Tom"]),
        ])

        result = await engine.add_messages(
            messages=user_assistant_messages,
            agent_config=cfg,
            user_id="u1",
            scope_id="s1",
        )

        assert result.summary == [] or result.summary is list

    @pytest.mark.asyncio
    async def test_disable_user_profile(
            self, engine, mock_llm, user_assistant_messages
    ):
        """enable_user_profile=False: no user_profile in result."""
        cfg = AgentMemoryConfig(enable_user_profile=False)
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(
                has_key_information=True,
                variables=[{"variable_key": "姓名", "variable_value": "Tom"}],
                summary="summary",
            ),
            _fragment_response(
                user_profile=["用户名叫Tom"],
                semantic_memory=["语义记忆"],
            ),
        ])

        result = await engine.add_messages(
            messages=user_assistant_messages,
            agent_config=cfg,
            user_id="u1",
            scope_id="s1",
        )

        assert result.user_profile == [] or result.user_profile is list
        # semantic_memory should still work
        assert len(result.semantic_memory) >= 1

    @pytest.mark.asyncio
    async def test_all_memory_types_disabled(
            self, engine, mock_llm, user_assistant_messages
    ):
        """All enable flags False: only variables may have values."""
        cfg = AgentMemoryConfig(
            enable_long_term_mem=False,
            enable_summary_memory=False,
        )
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(
                has_key_information=False,
                variables=[{"variable_key": "姓名", "variable_value": "Tom"}],
                summary="",
            ),
        ])

        result = await engine.add_messages(
            messages=user_assistant_messages,
            agent_config=cfg,
            user_id="u1",
            scope_id="s1",
        )

        assert len(result.variables) >= 1
        assert result.user_profile == [] or result.user_profile is list
        assert result.semantic_memory == [] or result.semantic_memory is list
        assert result.episodic_memory == [] or result.episodic_memory is list
        assert result.summary == [] or result.summary is list

    # -- Return value data content --

    @pytest.mark.asyncio
    async def test_variable_fields_content(
            self, engine, mock_llm, default_config, user_assistant_messages
    ):
        """Verify VariableUnit field values in return result."""
        result = await engine.add_messages(
            messages=user_assistant_messages,
            agent_config=default_config,
            user_id="u1",
            scope_id="s1",
        )

        v = result.variables[0]
        assert v.variable_name == "姓名"
        assert v.variable_mem == "Tom"
        assert v.mem_type == MemoryType.VARIABLE

    @pytest.mark.asyncio
    async def test_fragment_memory_fields_content(
            self, engine, mock_llm, default_config, user_assistant_messages
    ):
        """Verify FragmentMemoryUnit field values in return result."""
        result = await engine.add_messages(
            messages=user_assistant_messages,
            agent_config=default_config,
            user_id="u1",
            scope_id="s1",
        )

        assert len(result.user_profile) >= 1
        f = result.user_profile[0]
        assert f.content
        assert f.mem_type == MemoryType.USER_PROFILE
        assert f.operation_type == OperationType.ADD
        assert f.timestamp != ""

    @pytest.mark.asyncio
    async def test_summary_fields_content(
            self, engine, mock_llm, default_config, user_assistant_messages
    ):
        """Verify SummaryUnit field values in return result."""
        result = await engine.add_messages(
            messages=user_assistant_messages,
            agent_config=default_config,
            user_id="u1",
            scope_id="s1",
        )

        assert len(result.summary) >= 1
        s = result.summary[0]
        assert s.summary
        assert s.mem_type == MemoryType.SUMMARY
        assert s.message_mem_id != ""


# ---------------------------------------------------------------------------
# 2. End-to-end instructive memory tests (through add_messages)
# ---------------------------------------------------------------------------

class TestInstructiveMemoryE2E:
    """Verify UPDATE/DELETE instructive memory behavior through add_messages."""

    @pytest.mark.asyncio
    async def test_update_instruction_e2e(
            self, engine, mock_llm, default_config
    ):
        """UPDATE instruction: memory is updated end-to-end."""
        # Step 1: add initial memory
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(
                has_key_information=True,
                variables=[{"variable_key": "职业", "variable_value": "数据分析师"}],
                summary="用户介绍了职业",
            ),
            _fragment_response(user_profile=["用户是数据分析师"]),
        ])
        await engine.add_messages(
            messages=[
                BaseMessage(role="user", content="我是一名数据分析师"),
                BaseMessage(role="assistant", content="好的"),
            ],
            agent_config=default_config,
            user_id="u1",
            scope_id="s1",
        )

        # Step 2: send UPDATE instruction
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(has_key_information=True, summary="用户更新职业"),
            _fragment_response(
                has_explict_instruct=True,
                instruct_memories=[{
                    "mem_content": "用户是软件工程师",
                    "mem_type": "user_profile",
                    "mem_instruct": "update",
                    "old_mem": "数据分析师",
                }],
            ),
            _semantic_validation_response(correct=True),
        ])

        result = await engine.add_messages(
            messages=[
                BaseMessage(role="user", content="把我的职业改为软件工程师"),
                BaseMessage(role="assistant", content="已更新"),
            ],
            agent_config=default_config,
            user_id="u1",
            scope_id="s1",
        )

        assert len(result.user_profile) >= 1
        update_mem = [m for m in result.user_profile if m.operation_type == OperationType.UPDATE]
        assert len(update_mem) >= 1
        assert "软件工程师" in update_mem[0].content

    @pytest.mark.asyncio
    async def test_delete_instruction_e2e(
            self, engine, mock_llm, default_config
    ):
        """DELETE instruction: memory is deleted end-to-end."""
        # Step 1: add initial memory
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(
                has_key_information=True,
                variables=[{"variable_key": "姓名", "variable_value": "Tom"}],
                summary="用户介绍了姓名",
            ),
            _fragment_response(user_profile=["用户名叫Tom"]),
        ])
        await engine.add_messages(
            messages=[
                BaseMessage(role="user", content="我是Tom"),
                BaseMessage(role="assistant", content="你好"),
            ],
            agent_config=default_config,
            user_id="u1",
            scope_id="s1",
        )

        # Step 2: send DELETE instruction
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(has_key_information=True, summary="用户要删除姓名"),
            _fragment_response(
                has_explict_instruct=True,
                instruct_memories=[{
                    "mem_content": "",
                    "mem_type": "user_profile",
                    "mem_instruct": "delete",
                    "old_mem": "Tom",
                }],
            ),
            _semantic_validation_response(correct=True),
        ])

        result = await engine.add_messages(
            messages=[
                BaseMessage(role="user", content="删除我的姓名信息"),
                BaseMessage(role="assistant", content="已删除"),
            ],
            agent_config=default_config,
            user_id="u1",
            scope_id="s1",
        )

        delete_mem = [m for m in result.user_profile if m.operation_type == OperationType.DELETE]
        assert len(delete_mem) >= 1


# ---------------------------------------------------------------------------
# 3. Exception and boundary tests
# ---------------------------------------------------------------------------

class TestExceptionAndBoundary:
    """Verify add_messages behavior in error and edge cases."""

    @pytest.mark.asyncio
    async def test_empty_message_list_returns_empty(
            self, engine, mock_llm, default_config
    ):
        """Empty message list: _check_messages finds no human message."""
        result = await engine.add_messages(
            messages=[],
            agent_config=default_config,
            user_id="u1",
            scope_id="s1",
        )

        _assert_empty_result(result)

    @pytest.mark.asyncio
    async def test_gen_all_memory_returns_empty_dict(
            self, engine, mock_llm, default_config, user_assistant_messages
    ):
        """LLM returns no key information -> result only has variables from analysis."""
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(has_key_information=False, summary=""),
        ])

        result = await engine.add_messages(
            messages=user_assistant_messages,
            agent_config=default_config,
            user_id="u1",
            scope_id="s1",
        )

        assert isinstance(result, AddMemResult)

    @pytest.mark.asyncio
    async def test_long_content_does_not_crash(
            self, engine, mock_llm, default_config
    ):
        """Very long message content is handled without exception."""
        long_content = "x" * 10000
        messages = [
            BaseMessage(role="user", content=long_content),
            BaseMessage(role="assistant", content="ok"),
        ]
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(
                has_key_information=True,
                variables=[{"variable_key": "info", "variable_value": "long data"}],
                summary="长内容",
            ),
            _fragment_response(user_profile=["用户发送了长内容"]),
        ])

        result = await engine.add_messages(
            messages=messages,
            agent_config=default_config,
            user_id="u1",
            scope_id="s1",
        )

        assert isinstance(result, AddMemResult)
        assert len(result.variables) >= 1

    @pytest.mark.asyncio
    async def test_config_passed_to_gen_all_memory(
            self, engine, mock_llm, user_assistant_messages
    ):
        """AgentMemoryConfig is forwarded through the real flow."""
        cfg = AgentMemoryConfig(enable_long_term_mem=False)
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(has_key_information=False),
        ])

        with patch.object(
                engine.generator, "gen_all_memory",
                new_callable=AsyncMock,
                return_value={},
        ) as mock_gen:
            await engine.add_messages(
                messages=user_assistant_messages,
                agent_config=cfg,
                user_id="u1",
                scope_id="s1",
            )

            call_kwargs = mock_gen.call_args.kwargs
            assert call_kwargs["config"] is cfg

    @pytest.mark.asyncio
    async def test_instruct_memories_empty_list(
            self, engine, mock_llm, default_config, user_assistant_messages
    ):
        """has_explict_instruct=True but instruct_memories=[] -> no crash."""
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(has_key_information=True, summary="test"),
            _fragment_response(has_explict_instruct=True, instruct_memories=[]),
        ])

        result = await engine.add_messages(
            messages=user_assistant_messages,
            agent_config=default_config,
            user_id="u1",
            scope_id="s1",
        )

        assert isinstance(result, AddMemResult)

    @pytest.mark.asyncio
    async def test_update_semantic_validation_fails(
            self, engine, mock_llm, default_config
    ):
        """UPDATE instruction when semantic validation returns WRONG -> no update."""
        # Step 1: add initial memory
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(
                has_key_information=True,
                variables=[{"variable_key": "职业", "variable_value": "分析师"}],
                summary="用户介绍了职业",
            ),
            _fragment_response(user_profile=["用户是分析师"]),
        ])
        await engine.add_messages(
            messages=[
                BaseMessage(role="user", content="我是一名分析师"),
                BaseMessage(role="assistant", content="好的"),
            ],
            agent_config=default_config,
            user_id="u1",
            scope_id="s1",
        )

        # Step 2: UPDATE with failed validation
        mock_llm.invoke = AsyncMock(side_effect=[
            _analysis_response(has_key_information=True, summary="更新职业"),
            _fragment_response(
                has_explict_instruct=True,
                instruct_memories=[{
                    "mem_content": "用户是工程师",
                    "mem_type": "user_profile",
                    "mem_instruct": "update",
                    "old_mem": "不存在的记忆",
                }],
            ),
            _semantic_validation_response(correct=False),
        ])

        result = await engine.add_messages(
            messages=[
                BaseMessage(role="user", content="把职业改为工程师"),
                BaseMessage(role="assistant", content="已更新"),
            ],
            agent_config=default_config,
            user_id="u1",
            scope_id="s1",
        )

        # Validation failed -> no UPDATE FragmentMemoryUnit returned
        update_mem = [m for m in result.user_profile if m.operation_type == OperationType.UPDATE]
        assert len(update_mem) == 0
