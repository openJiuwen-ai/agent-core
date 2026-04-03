# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""MemoryRail 端到端系统测试（真实 LLM + Embedding API）。"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv

from openjiuwen.core.foundation.llm import (
    Model,
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperationCard,
)
from openjiuwen.harness import create_deep_agent, Workspace
from openjiuwen.harness.rails import MemoryRail


load_dotenv()


os.environ.setdefault("LLM_SSL_VERIFY", "false")
os.environ.setdefault("IS_SENSITIVE", "false")

logger = logging.getLogger(__name__)

LLM_API_BASE = os.getenv("API_BASE", "your_llm_api_url")
LLM_API_KEY = os.getenv("API_KEY", "your_llm_api_key")
LLM_MODEL_NAME = os.getenv("MODEL_NAME", "your_llm_model_name")
LLM_MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "OpenAI")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "60"))

EMBED_API_KEY = os.getenv("EMBED_API_KEY", "your_embed_api_key")
EMBED_API_BASE = os.getenv("EMBED_API_BASE", "your_embed_api_url")
EMBED_MODEL_NAME = os.getenv("EMBED_MODEL_NAME", "text-embedding-v3")


def _create_llm_model() -> Model:
    model_client_config = ModelClientConfig(
        client_provider=LLM_MODEL_PROVIDER,
        api_key=LLM_API_KEY,
        api_base=LLM_API_BASE,
        timeout=LLM_TIMEOUT,
        verify_ssl=False,
    )
    model_request_config = ModelRequestConfig(
        model=LLM_MODEL_NAME,
        temperature=0.2,
        top_p=0.9,
    )
    return Model(
        model_client_config=model_client_config,
        model_config=model_request_config,
    )


def _create_embedding_config() -> EmbeddingConfig:
    return EmbeddingConfig(
        model_name=EMBED_MODEL_NAME,
        base_url=EMBED_API_BASE,
        api_key=EMBED_API_KEY,
    )


def _require_api_config():
    missing = []
    if not LLM_API_KEY or LLM_API_KEY == "your_llm_api_key":
        missing.append("LLM_API_KEY")
    if not LLM_API_BASE or LLM_API_BASE == "your_llm_api_url":
        missing.append("LLM_API_BASE")
    if not EMBED_API_KEY or EMBED_API_KEY == "your_embed_api_key":
        missing.append("EMBED_API_KEY")
    if missing:
        pytest.skip(
            f"MemoryRail E2E requires {', '.join(missing)} in environment. "
            "Set them before running tests."
        )


@pytest.fixture
async def memory_test_env():
    await Runner.start()
    tmp_dir = tempfile.TemporaryDirectory(prefix="memory_rail_e2e_")
    work_dir = Workspace().root_path
    sys_operation_id = f"memory_rail_sysop_{uuid.uuid4().hex}"
    card = SysOperationCard(
        id=sys_operation_id,
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=work_dir),
    )
    add_result = Runner.resource_mgr.add_sys_operation(card)
    if add_result.is_err():
        raise RuntimeError(f"add_sys_operation failed: {add_result.msg()}")

    memory_dir = Path(work_dir) / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    yield {
        "tmp_dir": tmp_dir,
        "work_dir": work_dir,
        "sys_operation_id": sys_operation_id,
    }

    try:
        Runner.resource_mgr.remove_sys_operation(sys_operation_id=sys_operation_id)
    finally:
        tmp_dir.cleanup()
        await Runner.stop()


@pytest.mark.asyncio
@pytest.mark.skip("need llm and embedding")
async def test_01_memory_rail_basic_invoke(memory_test_env):
    """测试 MemoryRail 基本功能：注册工具、注入 prompt、初始化记忆管理器。"""
    _require_api_config()

    llm_model = _create_llm_model()
    embedding_config = _create_embedding_config()
    memory_rail = MemoryRail(embedding_config=embedding_config)

    agent = create_deep_agent(
        model=llm_model,
        rails=[memory_rail],
        enable_task_loop=False,
        max_iterations=10,
        system_prompt="你是一个智能助手，可以使用记忆工具来存储和检索信息。",
        auto_create_workspace=False
    )

    result = await Runner.run_agent(
        agent, {"query": "你好，请记住我的姓名是张三，今年25岁了，现在职业是软件开发工程师"},
    )

    result = await Runner.run_agent(
        agent, {"query": "你还记得我叫什么吗"},
    )

    assert isinstance(result, dict)
    assert result.get("result_type") == "answer"
    assert "output" in result
    assert bool(result["output"])


@pytest.mark.asyncio
@pytest.mark.skip("need llm and embedding")
async def test_02_write_memory_tool(memory_test_env):
    """测试 write_memory 工具：写入 USER.md 和会话日志。"""
    _require_api_config()

    llm_model = _create_llm_model()
    embedding_config = _create_embedding_config()
    memory_rail = MemoryRail(embedding_config=embedding_config)

    agent = create_deep_agent(
        model=llm_model,
        embedding_config=embedding_config,
        rails=[memory_rail],
        enable_task_loop=False,
        max_iterations=15,
        system_prompt=(
            "你是一个智能助手，可以使用记忆工具来存储和检索信息。"
            "当用户要求你记住某些信息时，请使用 write_memory 工具。"
        ),
        auto_create_workspace=False
    )

    result = await Runner.run_agent(
        agent, {"query": "请记住：我的项目截止日期是2026年5月1日，项目名称是智能助手开发。"},
    )
    assert isinstance(result, dict)
    assert result.get("result_type") == "answer"


@pytest.mark.asyncio
@pytest.mark.skip("need llm and embedding")
async def test_03_memory_search_tool(memory_test_env):
    """测试 memory_search 工具：搜索历史记忆。"""
    _require_api_config()

    llm_model = _create_llm_model()
    embedding_config = _create_embedding_config()
    memory_rail = MemoryRail(embedding_config=embedding_config)

    agent = create_deep_agent(
        model=llm_model,
        embedding_config=embedding_config,
        rails=[memory_rail],
        enable_task_loop=False,
        max_iterations=15,
        system_prompt=(
            "你是一个智能助手，可以使用记忆工具来存储和检索信息。"
            "当用户询问之前的信息时，请先使用 memory_search 工具搜索。"
        ),
        auto_create_workspace=False
    )

    await Runner.run_agent(
        agent, {"query": "请记住：我最喜欢的编程语言是Python，我最喜欢的框架是FastAPI。"},
    )

    await asyncio.sleep(5)

    search_result = await Runner.run_agent(
        agent, {"query": "我最喜欢的编程语言是什么？"},
    )
    assert isinstance(search_result, dict)
    assert search_result.get("result_type") == "answer"
    assert "Python" in search_result.get("output", "")


@pytest.mark.asyncio
@pytest.mark.skip("need llm and embedding")
async def test_04_read_memory_tool(memory_test_env):
    """测试 read_memory 工具：读取 USER.md 文件。"""
    _require_api_config()

    llm_model = _create_llm_model()
    embedding_config = _create_embedding_config()
    memory_rail = MemoryRail(embedding_config=embedding_config)

    agent = create_deep_agent(
        model=llm_model,
        embedding_config=embedding_config,
        rails=[memory_rail],
        enable_task_loop=False,
        max_iterations=15,
        system_prompt=(
            "你是一个智能助手，可以使用记忆工具来存储和检索信息。"
            "当需要查看用户资料时，请使用 read_memory 工具读取 USER.md。"
        ),
        auto_create_workspace=False
    )

    await Runner.run_agent(
        agent, {"query": "请记住我的姓名是李四，我的邮箱是lisi@example.com。"},
    )

    result = await Runner.run_agent(
        agent, {"query": "请帮我确认一下我的邮箱地址是什么？"},
    )
    assert isinstance(result, dict)
    assert result.get("result_type") == "answer"
    assert "lisi@example.com" in result.get("output", "")


@pytest.mark.asyncio
@pytest.mark.skip("need llm and embedding")
async def test_05_edit_memory_tool(memory_test_env):
    """测试 edit_memory 工具：编辑已存在的字段。"""
    _require_api_config()

    llm_model = _create_llm_model()
    embedding_config = _create_embedding_config()
    memory_rail = MemoryRail(embedding_config=embedding_config)

    agent = create_deep_agent(
        model=llm_model,
        embedding_config=embedding_config,
        rails=[memory_rail],
        enable_task_loop=False,
        max_iterations=15,
        system_prompt=(
            "你是一个智能助手，可以使用记忆工具来存储和检索信息。"
            "当用户更新信息时，请使用 edit_memory 工具更新已存在的字段。"
        ),
        auto_create_workspace=False
    )

    await Runner.run_agent(
        agent, {"query": "请记住我的手机号是13800138000。"},
    )

    result = await Runner.run_agent(
        agent, {"query": "我的手机号变了，请更新为13900139000。"},
    )
    assert isinstance(result, dict)
    assert result.get("result_type") == "answer"

    result = await Runner.run_agent(
        agent, {"query": "我的手机号是多少？"},
    )
    assert "13900139000" in result.get("output", "")


@pytest.mark.asyncio
@pytest.mark.skip("need llm and embedding")
async def test_06_memory_get_tool(memory_test_env):
    """测试 memory_get 工具：读取指定行范围。"""
    _require_api_config()

    llm_model = _create_llm_model()
    embedding_config = _create_embedding_config()
    memory_rail = MemoryRail(embedding_config=embedding_config)

    agent = create_deep_agent(
        model=llm_model,
        embedding_config=embedding_config,
        rails=[memory_rail],
        enable_task_loop=False,
        max_iterations=15,
        system_prompt=(
            "你是一个智能助手，可以使用记忆工具来存储和检索信息。"
            "当需要读取特定行时，请使用 memory_get 工具。"
        ),
        auto_create_workspace=False
    )

    await Runner.run_agent(
        agent, {"query": "请记住以下信息：第一条记录是关于项目A的启动时间2026年1月。第二条记录是关于项目B的预算100万。第三条记录是关于项目C的负责人王五。"},
    )
    await asyncio.sleep(5)
    result = await Runner.run_agent(
        agent, {"query": "项目B的预算是多少？"},
    )
    assert isinstance(result, dict)
    assert result.get("result_type") == "answer"
    assert "100" in result.get("output", "")


@pytest.mark.asyncio
@pytest.mark.skip("need llm and embedding")
async def test_07_write_memory_append_mode(memory_test_env):
    """测试 write_memory 工具的追加模式。"""
    _require_api_config()

    llm_model = _create_llm_model()
    embedding_config = _create_embedding_config()
    memory_rail = MemoryRail(embedding_config=embedding_config)

    agent = create_deep_agent(
        model=llm_model,
        embedding_config=embedding_config,
        rails=[memory_rail],
        enable_task_loop=False,
        max_iterations=15,
        system_prompt=(
            "你是一个智能助手，可以使用记忆工具来存储和检索信息。"
            "当用户要求追加信息时，请使用 write_memory 工具的追加模式。"
        ),
        auto_create_workspace=False
    )

    await Runner.run_agent(
        agent, {"query": "请记住：今天完成了用户登录功能的开发。"},
    )

    await Runner.run_agent(
        agent, {"query": "请追加记录：今天还完成了用户注册功能的开发。"},
    )

    result = await Runner.run_agent(
        agent, {"query": "今天完成了哪些功能？"},
    )
    assert isinstance(result, dict)
    assert result.get("result_type") == "answer"
    output = result.get("output", "")
    assert "登录" in output and "注册" in output


@pytest.mark.asyncio
@pytest.mark.skip("need llm and embedding")
async def test_08_update_user_profile(memory_test_env):
    """测试更新 USER.md 用户画像。"""
    _require_api_config()

    llm_model = _create_llm_model()
    embedding_config = _create_embedding_config()
    memory_rail = MemoryRail(embedding_config=embedding_config)

    agent = create_deep_agent(
        model=llm_model,
        embedding_config=embedding_config,
        rails=[memory_rail],
        enable_task_loop=False,
        max_iterations=15,
        system_prompt=(
            "你是一个智能助手，可以使用记忆工具来存储和检索信息。"
            "当用户透露个人信息时，请更新 USER.md 文件。"
        ),
        auto_create_workspace=False
    )

    await Runner.run_agent(
        agent, {"query": "我是王明，今年30岁，是一名产品经理。"},
    )

    result = await Runner.run_agent(
        agent, {"query": "我是谁？我的职业是什么？"},
    )
    assert isinstance(result, dict)
    assert result.get("result_type") == "answer"
    output = result.get("output", "")
    assert "王明" in output or "产品经理" in output


@pytest.mark.asyncio
@pytest.mark.skip("need llm and embedding")
async def test_09_write_memory_md_file(memory_test_env):
    """测试写入 MEMORY.md 长期记忆文件。"""
    _require_api_config()

    llm_model = _create_llm_model()
    embedding_config = _create_embedding_config()
    memory_rail = MemoryRail(embedding_config=embedding_config)

    agent = create_deep_agent(
        model=llm_model,
        embedding_config=embedding_config,
        rails=[memory_rail],
        enable_task_loop=False,
        max_iterations=15,
        system_prompt=(
            "你是一个智能助手，可以使用记忆工具来存储和检索信息。"
            "当用户要求记录长期知识时，请写入 MEMORY.md 文件。"
        ),
        auto_create_workspace=False
    )

    await Runner.run_agent(
        agent, {"query": "请记录一条长期知识：我们公司的服务器IP是192.168.1.100，SSH端口是2222。"},
    )

    result = await Runner.run_agent(
        agent, {"query": "我们公司的服务器IP是多少？"},
    )
    assert isinstance(result, dict)
    assert result.get("result_type") == "answer"
    assert "192.168.1.100" in result.get("output", "")
