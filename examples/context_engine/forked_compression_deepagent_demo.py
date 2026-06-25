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
import importlib.util
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

_FIXTURES_PATH = _REPO_ROOT / "examples" / "context_engine" / "rule_compression_fixtures.py"
_FIXTURES_SPEC = importlib.util.spec_from_file_location("rule_compression_fixtures", _FIXTURES_PATH)
if _FIXTURES_SPEC is None or _FIXTURES_SPEC.loader is None:
    raise RuntimeError(f"Unable to load rule compression fixtures from {_FIXTURES_PATH}")
_FIXTURES = importlib.util.module_from_spec(_FIXTURES_SPEC)
sys.modules.setdefault(_FIXTURES_SPEC.name, _FIXTURES)
_FIXTURES_SPEC.loader.exec_module(_FIXTURES)
SCENARIOS = _FIXTURES.SCENARIOS

from openjiuwen.core.context_engine import ContextEngine  # noqa: E402
from openjiuwen.core.context_engine.base import ContextWindow  # noqa: E402
from openjiuwen.core.context_engine.processor.compressor.forked.current import (  # noqa: E402
    ForkedCurrentRoundCompressorConfig,
)
from openjiuwen.core.context_engine.processor.compressor.forked.dialogue import (  # noqa: E402
    ForkedDialogueCompressorConfig,
)
from openjiuwen.core.context_engine.processor.compressor.forked.executor import (  # noqa: E402
    ForkedCompressionExecutor,
    ForkedCompressionResult,
)
from openjiuwen.core.context_engine.processor.compressor.forked.round import (  # noqa: E402
    ForkedRoundLevelCompressorConfig,
)
from openjiuwen.core.context_engine.processor.offloader.message_offloader import (  # noqa: E402
    MessageOffloaderConfig,
)
from openjiuwen.core.context_engine.schema.config import ContextEngineConfig  # noqa: E402
from openjiuwen.core.context_engine.schema.messages import OffloadMixin  # noqa: E402
from openjiuwen.core.foundation.llm import (  # noqa: E402
    AssistantMessage,
    Model,
    ModelClientConfig,
    ModelRequestConfig,
    ToolCall,
    ToolMessage,
    UserMessage,
)
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
FORKED_PROBE_USER_GOAL = "验证 forked compression query 触发流程"
FORKED_PROBE_DURABLE_FACT = "ALPHA-42"
_REAL_PROBE_PROCESSOR_NAMES = [
    "ForkedDialogueCompressor",
    "ForkedCurrentRoundCompressor",
    "ForkedRoundLevelCompressor",
]
_REAL_PROBE_CACHE_WARMUP_SECONDS = 3.0
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
        raise RuntimeError("Set API_KEY or LLM_API_KEY before running with --run or real-model probes.")
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
                ttl_seconds=300,
                protected_tool_names=["reload_original_context_messages", "read_file:*SKILL.md"],
            ),
        ),
        (
            "ForkedDialogueCompressor",
            ForkedDialogueCompressorConfig(
                trigger_context_ratio=0.10,
                model=model_config,
                model_client=model_client,
            ),
        ),
        (
            "ForkedCurrentRoundCompressor",
            ForkedCurrentRoundCompressorConfig(
                trigger_context_ratio=0.10,
                keep_recent_messages=2,
                model=model_config,
                model_client=model_client,
            ),
        ),
        (
            "ForkedRoundLevelCompressor",
            ForkedRoundLevelCompressorConfig(
                trigger_context_ratio=0.10,
                keep_recent_messages=4,
                model=model_config,
                model_client=model_client,
            ),
        ),
    ]
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


class _ProbeForkedExecutor:
    def __init__(self, processor_name: str) -> None:
        self.processor_name = processor_name
        self.calls = 0

    async def invoke(self, request: Any) -> ForkedCompressionResult:
        self.calls += 1
        summary = "\n".join(
            [
                "<state_snapshot>",
                f"processor: {self.processor_name}",
                f"user_goal: {FORKED_PROBE_USER_GOAL}",
                f"durable_fact: {FORKED_PROBE_DURABLE_FACT}",
                "quality_note: Preserve the user goal, durable fact, active task, and latest raw tail.",
                "</state_snapshot>",
            ]
        )
        return ForkedCompressionResult(AssistantMessage(content=summary))


class _ProbeKVCacheModel:
    def __init__(self) -> None:
        self.release_calls: list[dict[str, Any]] = []

    async def release(self, **kwargs: Any) -> bool:
        self.release_calls.append(dict(kwargs))
        return True


def _forked_probe_processors(model: Model, processor_name: str) -> list[tuple[str, Any]]:
    model_config = model.model_config
    model_client = model.model_client_config
    if processor_name == "ForkedDialogueCompressor":
        return [
            (
                processor_name,
                ForkedDialogueCompressorConfig(
                    trigger_context_ratio=0.05,
                    model=model_config,
                    model_client=model_client,
                ),
            )
        ]
    if processor_name == "ForkedCurrentRoundCompressor":
        return [
            (
                processor_name,
                ForkedCurrentRoundCompressorConfig(
                    trigger_context_ratio=0.05,
                    keep_recent_messages=1,
                    model=model_config,
                    model_client=model_client,
                ),
            )
        ]
    if processor_name == "ForkedRoundLevelCompressor":
        return [
            (
                processor_name,
                ForkedRoundLevelCompressorConfig(
                    trigger_context_ratio=0.05,
                    keep_recent_messages=1,
                    model=model_config,
                    model_client=model_client,
                ),
            )
        ]
    raise ValueError(f"Unsupported forked probe processor: {processor_name}")


def _build_forked_probe_messages(processor_name: str) -> list[Any]:
    durable_header = (
        f"用户目标: {FORKED_PROBE_USER_GOAL}\n"
        f"必须保留的持久事实: {FORKED_PROBE_DURABLE_FACT}\n"
    )
    large_history = (
        durable_header
        + "历史执行记录: 已检查 processor 注册、构造长上下文、准备验证压缩质量。"
        + "重复填充用于制造上下文压力。"
    ) * 80

    if processor_name == "ForkedDialogueCompressor":
        return [
            UserMessage(content=f"请记住: {durable_header}"),
            AssistantMessage(content=large_history),
            UserMessage(content="当前 query: 请继续验证 dialogue forked 压缩是否触发。"),
        ]

    if processor_name == "ForkedCurrentRoundCompressor":
        return [
            UserMessage(content=f"请记住: {durable_header}"),
            AssistantMessage(content="已进入当前任务验证。"),
            UserMessage(content="当前 query: 请继续验证 current-round forked 压缩是否触发。"),
            AssistantMessage(content=large_history),
            AssistantMessage(content="最新尾部原文: current-round tail should remain raw."),
        ]

    if processor_name == "ForkedRoundLevelCompressor":
        return [
            UserMessage(content=f"请记住: {durable_header}"),
            AssistantMessage(content=large_history),
            UserMessage(content="当前 query: 请继续验证 round-level forked 压缩是否触发。"),
            AssistantMessage(content="最新尾部原文: round-level tail should remain raw."),
        ]

    raise ValueError(f"Unsupported forked probe processor: {processor_name}")


def _window_token_count(context: Any, window: Any) -> int:
    processor = context._processors[0]
    return processor._count_context_window_tokens(window, context)


async def _run_one_forked_compression_probe(
    *,
    processor_name: str,
    log_dir: Path,
    workspace_dir: Path,
    compression_model: Any | None = None,
    use_probe_executor: bool = True,
) -> dict[str, Any]:
    model_for_config = compression_model or build_model(require_api_key=False)
    engine = ContextEngine(
        ContextEngineConfig(
            context_window_tokens=500,
            default_window_message_num=200,
            enable_kv_cache_release=True,
        ),
        workspace=Workspace(root_path=str(workspace_dir)),
    )
    context = await engine.create_context(
        f"forked_probe_{processor_name}",
        history_messages=_build_forked_probe_messages(processor_name),
        processors=_forked_probe_processors(model_for_config, processor_name),
    )
    processor = context._processors[0]
    executor = None
    if use_probe_executor:
        executor = _ProbeForkedExecutor(processor_name)
        processor._forked_executor = executor
    elif compression_model is not None:
        processor._forked_executor = ForkedCompressionExecutor(compression_model)
    model = _ProbeKVCacheModel()
    baseline_window = ContextWindow(
        system_messages=[],
        context_messages=list(context.get_messages()),
        tools=[],
    )
    before_tokens = _window_token_count(context, baseline_window)
    if context._kv_cache_manager is not None:
        await context._kv_cache_manager.release(baseline_window, model=model)

    updated_window = await context.get_context_window(model=model)
    after_tokens = _window_token_count(context, updated_window)
    messages = updated_window.get_messages()
    serialized_messages = [
        message.model_dump(mode="json") if hasattr(message, "model_dump") else str(message)
        for message in messages
    ]
    joined_content = "\n".join(str(getattr(message, "content", "")) for message in messages)
    release_call = model.release_calls[-1] if model.release_calls else {}
    states = context._processor_state_recorder.history()
    completed_states = [
        state for state in states
        if state.get("processor") == processor_name and state.get("status") == "completed"
    ]
    compression_usage = completed_states[-1].get("compression_usage") if completed_states else None
    cache_tokens = int((compression_usage or {}).get("cache_tokens") or 0)

    result = {
        "processor": processor_name,
        "constructed_query": next(
            str(getattr(message, "content", ""))
            for message in reversed(_build_forked_probe_messages(processor_name))
            if isinstance(message, UserMessage)
        ),
        "triggered": (executor.calls > 0 if executor is not None else bool(completed_states))
        and bool(completed_states),
        "before_message_count": len(baseline_window.get_messages()),
        "after_message_count": len(messages),
        "before_tokens": before_tokens,
        "after_tokens": after_tokens,
        "saved_tokens": before_tokens - after_tokens,
        "quality": {
            "contains_user_goal": FORKED_PROBE_USER_GOAL in joined_content,
            "contains_durable_fact": FORKED_PROBE_DURABLE_FACT in joined_content,
            "contains_state_snapshot_tags": "<state_snapshot>" in joined_content,
        },
        "compression_usage": compression_usage,
        "compression_request_cache": {
            "hit": cache_tokens > 0,
            "cache_tokens": cache_tokens,
        },
        "kv_cache_release": {
            "called": bool(model.release_calls),
            "messages_released_index": release_call.get("messages_released_index"),
            "session_id": release_call.get("session_id"),
        },
        "messages": serialized_messages,
        "processor_states": states,
    }
    log_dir.mkdir(parents=True, exist_ok=True)
    probe_path = log_dir / f"forked_probe_{processor_name}.json"
    probe_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    result["probe_log"] = str(probe_path)
    return result


async def _warm_real_forked_compression_cache(
    *,
    processor_name: str,
    log_dir: Path,
    workspace_dir: Path,
    compression_model: Any,
) -> dict[str, Any]:
    return await _run_one_forked_compression_probe(
        processor_name=processor_name,
        log_dir=log_dir,
        workspace_dir=workspace_dir,
        compression_model=compression_model,
        use_probe_executor=False,
    )


async def run_forked_compression_probe(
    *,
    log_dir: Path,
    workspace_dir: Path,
) -> dict[str, Any]:
    processor_names = [
        "ForkedDialogueCompressor",
        "ForkedCurrentRoundCompressor",
        "ForkedRoundLevelCompressor",
    ]
    results = [
        await _run_one_forked_compression_probe(
            processor_name=processor_name,
            log_dir=log_dir,
            workspace_dir=workspace_dir,
        )
        for processor_name in processor_names
    ]
    summary = {
        "session_id": f"{DEMO_AGENT_ID}-forked-probe-{uuid.uuid4().hex[:8]}",
        "processor_count": len(results),
        "processors": results,
    }
    log_dir.mkdir(parents=True, exist_ok=True)
    summary_path = log_dir / f"{summary['session_id']}_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    summary["summary_log"] = str(summary_path)
    return summary


async def run_real_forked_compression_probe(
    *,
    log_dir: Path,
    workspace_dir: Path,
) -> dict[str, Any]:
    model = build_model(require_api_key=True)
    results: list[dict[str, Any]] = []
    warmups: list[dict[str, Any]] = []
    for processor_name in _REAL_PROBE_PROCESSOR_NAMES:
        warmups.append(
            await _warm_real_forked_compression_cache(
                processor_name=processor_name,
                log_dir=log_dir,
                workspace_dir=workspace_dir,
                compression_model=model,
            )
        )
        if _REAL_PROBE_CACHE_WARMUP_SECONDS > 0:
            await asyncio.sleep(_REAL_PROBE_CACHE_WARMUP_SECONDS)
        results.append(
            await _run_one_forked_compression_probe(
                processor_name=processor_name,
                log_dir=log_dir,
                workspace_dir=workspace_dir,
                compression_model=model,
                use_probe_executor=False,
            )
        )

    summary = {
        "session_id": f"{DEMO_AGENT_ID}-real-forked-probe-{uuid.uuid4().hex[:8]}",
        "processor_count": len(results),
        "cache_warmup_count": len(warmups),
        "processors": results,
        "warmups": warmups,
    }
    log_dir.mkdir(parents=True, exist_ok=True)
    summary_path = log_dir / f"{summary['session_id']}_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    summary["summary_log"] = str(summary_path)
    return summary


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


def dump_context_log_from_context(
    context: Any,
    *,
    session_id: str,
    query: str,
    result: Any,
    log_dir: Path,
) -> Path:
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


async def run_rule_compression_probe(
    *,
    log_dir: Path,
    workspace_dir: Path,
) -> dict[str, Any]:
    engine = ContextEngine(
        ContextEngineConfig(
            context_window_tokens=DEFAULT_CONTEXT_WINDOW_TOKENS,
            default_window_message_num=200,
        ),
        workspace=Workspace(root_path=str(workspace_dir)),
    )
    session_id = f"{DEMO_AGENT_ID}-rule-probe-{uuid.uuid4().hex[:8]}"
    context = await engine.create_context(
        "rule_compression_probe",
        processors=build_context_processors(build_model(require_api_key=False)),
    )

    scenarios: list[dict[str, Any]] = []
    for index, scenario in enumerate(SCENARIOS, start=1):
        tool_call_id = f"probe-{index}-{scenario.name}"
        original = scenario.build_content()
        await context.add_messages(
            [
                UserMessage(content=scenario.query),
                AssistantMessage(
                    content=f"calling {scenario.tool_name}",
                    tool_calls=[
                        ToolCall(
                            id=tool_call_id,
                            type="function",
                            name=scenario.tool_name,
                            arguments=json.dumps({"scenario": scenario.name}, ensure_ascii=False),
                        )
                    ],
                ),
                ToolMessage(content=original, tool_call_id=tool_call_id),
            ]
        )
        message = context.get_messages()[-1]
        metadata = getattr(message, "metadata", None) or {}
        offload_files = _read_offload_files(getattr(message, "content", None))
        scenarios.append(
            {
                "name": scenario.name,
                "expected_type": scenario.content_type.value,
                "rule_compression_type": metadata.get("rule_compression_type"),
                "modified": bool(metadata.get("rule_compressed_at")),
                "original_chars": len(original),
                "content_chars": len(message.content) if isinstance(message.content, str) else 0,
                "offload_type": getattr(message, "offload_type", None),
                "offload_file_count": len(offload_files),
                "offload_files": offload_files,
                "details": metadata.get("rule_compression_details") or {},
            }
        )

    summary = {
        "session_id": session_id,
        "scenario_count": len(scenarios),
        "scenarios": scenarios,
    }
    log_path = dump_context_log_from_context(
        context,
        session_id=session_id,
        query="rule_compression_probe",
        result=summary,
        log_dir=log_dir,
    )
    summary["context_log"] = str(log_path)
    summary_path = log_dir / f"{session_id}_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    summary["summary_log"] = str(summary_path)
    return summary


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
    parser.add_argument(
        "--probe-rule-compression",
        action="store_true",
        help="Run deterministic oversized tool-result probes for every rule compressor without calling a model.",
    )
    parser.add_argument(
        "--probe-forked-compression",
        action="store_true",
        help="Run deterministic probes for each forked compressor and KV-cache release without calling a model.",
    )
    parser.add_argument(
        "--probe-forked-compression-real",
        action="store_true",
        help=(
            "Run each forked compressor with real model calls, warm the provider prompt cache, "
            "and report compression quality plus compression-request cache hits."
        ),
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    log_dir = Path(args.context_log_dir)
    if args.probe_rule_compression:
        summary = await run_rule_compression_probe(log_dir=log_dir, workspace_dir=_REPO_ROOT)
        print("Rule compression probe summary:", json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return
    if args.probe_forked_compression:
        summary = await run_forked_compression_probe(log_dir=log_dir, workspace_dir=_REPO_ROOT)
        print("Forked compression probe summary:", json.dumps(summary, ensure_ascii=False, indent=2, default=str))
        return
    if args.probe_forked_compression_real:
        summary = await run_real_forked_compression_probe(log_dir=log_dir, workspace_dir=_REPO_ROOT)
        print(
            "Real forked compression probe summary:",
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        )
        return

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
        if args.run:
            await maybe_run_agent(agent, args.query, log_dir=log_dir)
    finally:
        await Runner.stop()

if __name__ == "__main__":
    asyncio.run(main())
