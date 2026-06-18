# coding: utf-8
"""DeepAgent example for forked context compression processors.

This script builds a real DeepAgent configured with:

1. MessageOffloader
2. ForkedDialogueCompressor
3. ForkedCurrentRoundCompressor
4. ForkedRoundLevelCompressor

It also registers common working tools:

- ``fetch_webpage`` for webpage reading
- ``read_file`` and ``write_file`` through ``SysOperationRail``

By default it only builds and prints the configuration; it does not call the
model. Pass ``--run`` to execute one real DeepAgent call.

Run from repository root::

    uv run python examples/context_engine/forked_compression_deepagent_demo.py
    uv run python examples/context_engine/forked_compression_deepagent_demo.py --run

Model environment variables:

- ``API_KEY`` or ``LLM_API_KEY``
- ``API_BASE`` or ``LLM_BASE_URL``
- ``MODEL_NAME`` or ``LLM_MODEL``
- ``MODEL_PROVIDER`` or ``LLM_PROVIDER`` (default: ``OpenAI``)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from openjiuwen.core.context_engine.processor.compressor.forked.current import (  # noqa: E402
    ForkedCurrentRoundCompressorConfig,
)
from openjiuwen.core.context_engine.processor.compressor.forked.dialogue import (  # noqa: E402
    ForkedDialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.compressor.forked.round import (  # noqa: E402
    ForkedRoundLevelCompressorConfig,
)
from openjiuwen.core.context_engine.processor.offloader.message_offloader import (  # noqa: E402
    MessageOffloaderConfig,
)
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig  # noqa: E402
from openjiuwen.core.context_engine.schema.messages import OffloadMixin  # noqa: E402
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig  # noqa: E402
from openjiuwen.core.runner import Runner  # noqa: E402
from openjiuwen.core.single_agent.schema.agent_card import AgentCard  # noqa: E402
from openjiuwen.harness import Workspace, create_deep_agent  # noqa: E402
from openjiuwen.harness.rails.context_engineer.context_processor_rail import (  # noqa: E402
    ContextProcessorRail,
)
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail  # noqa: E402
from openjiuwen.harness.tools import WebFetchWebpageTool  # noqa: E402


DEFAULT_CONTEXT_WINDOW_TOKENS = 8000
DEFAULT_CONTEXT_LOG_DIR = _REPO_ROOT / ".forked_compression_demo_logs"
DEMO_AGENT_ID = "forked-compression-demo"
OFFLOAD_FILESYSTEM_MARKER_PATTERN = re.compile(
    r"\[\[OFFLOAD:\s*type=filesystem,\s*path=(?P<path>.*?)\]\]"
)


def _env(name: str, fallback: str = "") -> str:
    return os.getenv(name, fallback).strip()


def build_model(*, require_api_key: bool) -> Model:
    api_key = _env("API_KEY") or _env("LLM_API_KEY")
    api_base = _env("API_BASE") or _env("LLM_BASE_URL", "https://api.deepseek.com")
    model_name = _env("MODEL_NAME") or _env("LLM_MODEL", "deepseek-v4-flash")
    provider = _env("MODEL_PROVIDER") or _env("LLM_PROVIDER", "OpenAI")

    if require_api_key and not api_key:
        raise RuntimeError("Set API_KEY or LLM_API_KEY before running with --run.")
    if not api_key:
        api_key = "dry-run-placeholder-key"

    model_client = ModelClientConfig(
        client_provider=provider,
        api_key=api_key,
        api_base=api_base,
        verify_ssl=_env("LLM_VERIFY_SSL", "false").lower() in {"1", "true", "yes", "on"},
    )
    model_request = ModelRequestConfig(
        model=model_name,
        temperature=float(_env("LLM_TEMPERATURE", "0.0")),
    )
    return Model(model_client_config=model_client, model_config=model_request)


def build_context_processors(model: Model) -> list[tuple[str, Any]]:
    model_config = model.model_config
    model_client = model.model_client_config

    processors: list[tuple[str, Any]] = [
        (
            "MessageOffloader",
            MessageOffloaderConfig(
                add_message_threshold_ratio=0.05,
                enable_rule_compression=True,
                ttl_seconds=300,
                protected_tool_names=["reload_original_context_messages", "read_file:*SKILL.md"],
            ),
        ),
        # (
        #     "ForkedDialogueCompressor",
        #     ForkedDialogueCompressorConfig(
        #         trigger_context_ratio=0.10,
        #         model=model_config,
        #         model_client=model_client,
        #     ),
        # ),
    ]
    # processors.append(
    #     (
    #         "ForkedCurrentRoundCompressor",
    #         ForkedCurrentRoundCompressorConfig(
    #             trigger_context_ratio=0.10,
    #             keep_recent_messages=2,
    #             model=model_config,
    #             model_client=model_client,
    #         ),
    #     )
    # )
    # processors.append(
    #     (
    #         "ForkedRoundLevelCompressor",
    #         ForkedRoundLevelCompressorConfig(
    #             trigger_context_ratio=0.10,
    #             keep_recent_messages=4,
    #             model=model_config,
    #             model_client=model_client,
    #         ),
    #     )
    # )
    return processors


def build_agent(
    model: Model,
    workspace_dir: Path,
) -> Any:
    processors = build_context_processors(model)
    return create_deep_agent(
        model=model,
        card=AgentCard(
            id=DEMO_AGENT_ID,
            name=DEMO_AGENT_ID,
            description="DeepAgent demo for MessageOffloader and forked compressors.",
        ),
        system_prompt=(
            "你是 context compression demo agent。请正常回答用户问题；"
            "上下文处理器会在窗口压力达到阈值时自动处理上下文。"
            "你可以用 fetch_webpage 读取网页，用 read_file 读取文件，用 write_file 写文件。"
        ),
        tools=[WebFetchWebpageTool(language="cn", agent_id=DEMO_AGENT_ID)],
        enable_task_loop=False,
        max_iterations=4,
        workspace=Workspace(root_path=str(workspace_dir)),
        rails=[
            SysOperationRail(),
            ContextProcessorRail(
                preset=False,
                processors=processors,
            )
        ],
        context_engine_config=ContextEngineConfig(
            context_window_tokens=DEFAULT_CONTEXT_WINDOW_TOKENS,
            default_window_message_num=200,
        ),
        language="cn",
    )


def print_processors(agent: Any) -> None:
    processors = list(agent.react_config.context_processors)
    print("Registered context processors:")
    for index, (name, config) in enumerate(processors, start=1):
        model_name = getattr(getattr(config, "model", None), "model_name", None)
        print(f"{index}. {name} ({type(config).__name__}, model={model_name})")


def print_tools(agent: Any) -> None:
    tool_names = sorted(
        getattr(card, "name", str(card))
        for card in agent.ability_manager.list()
        if getattr(card, "name", None)
    )
    print("Registered tools:")
    for name in tool_names:
        print(f"- {name}")


def _message_payload(message: Any) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        payload = message.model_dump(mode="json")
    else:
        payload = {"content": getattr(message, "content", str(message))}
    payload["message_class"] = type(message).__name__
    if isinstance(message, OffloadMixin):
        payload["offload_handle"] = message.offload_handle
        payload["offload_type"] = message.offload_type
    payload["offload_files"] = _read_offload_files(getattr(message, "content", None))
    return payload


def _read_offload_files(content: Any) -> list[dict[str, Any]]:
    if not isinstance(content, str):
        return []

    files: list[dict[str, Any]] = []
    for match in OFFLOAD_FILESYSTEM_MARKER_PATTERN.finditer(content):
        offload_path = Path(match.group("path").strip())
        file_payload: dict[str, Any] = {
            "path": str(offload_path),
            "exists": offload_path.is_file(),
        }
        if offload_path.is_file():
            raw_content = offload_path.read_text(encoding="utf-8")
            file_payload["raw_content"] = raw_content
            try:
                file_payload["json"] = json.loads(raw_content)
            except json.JSONDecodeError:
                pass
        files.append(file_payload)
    return files


def dump_context_log(
    agent: Any,
    *,
    session_id: str,
    query: str,
    result: Any,
    log_dir: Path,
) -> Path:
    context = agent._get_context_or_error(session_id=session_id)
    messages = context.get_messages()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "query": query,
        "result": result,
        "context": {
            "message_count": len(messages),
            "messages": [_message_payload(message) for message in messages],
        },
    }
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{session_id}_context.json"
    log_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return log_path


async def maybe_run_agent(agent: Any, query: str, *, log_dir: Path) -> None:
    session_id = f"{DEMO_AGENT_ID}-{uuid.uuid4().hex[:8]}"
    result = await Runner.run_agent(agent, {"query": query}, session=session_id)
    log_path = dump_context_log(
        agent,
        session_id=session_id,
        query=query,
        result=result,
        log_dir=log_dir,
    )
    print("Session:", session_id)
    print("Result:", result)
    print("Context log:", log_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run",
        action="store_true",
        help="Actually invoke the DeepAgent and call the configured model.",
    )
    parser.add_argument(
        "--query",
        default=(
            "请简要说明这个 demo 的上下文处理器注册顺序，并生成一段较长回答用于触发上下文压力。"
        ),
        help="Query used only when --run is passed.",
    )
    parser.add_argument(
        "--context-log-dir",
        default=str(DEFAULT_CONTEXT_LOG_DIR),
        help="Directory used to write full post-run context JSON logs.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    model = build_model(require_api_key=args.run)
    await Runner.start()
    try:
        agent = build_agent(
            model,
            _REPO_ROOT,
        )
        await agent.ensure_initialized()
        print_processors(agent)
        print_tools(agent)
        log_dir = Path(args.context_log_dir)
        await maybe_run_agent(
            agent,
            r"你去检视一下这里的代码 D:\work\code\agent-core-mr\openjiuwen\core\context_engine\processor\offloader\rules 规则性压缩代码 不要改代码 ",
            log_dir=log_dir,
        )
        await maybe_run_agent(agent, "帮我去看一下最近的中东局势", log_dir=log_dir)
        await maybe_run_agent(agent, "111", log_dir=log_dir)
    finally:
        await Runner.stop()

if __name__ == "__main__":
    asyncio.run(main())
