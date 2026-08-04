# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the Codex package's exact rollout inference observability."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

pytest.importorskip("opentelemetry.sdk")

from openjiuwen.agent_teams.observability.codex.rollout_trace import (
    CodexRolloutTraceReader,
)


@pytest.mark.level0
@pytest.mark.parametrize(
    ("code", "expected_name", "expected_display_name"),
    [
        (
            "await tools.mcp__openjiuwen_team__view_task({action:'get'});",
            "openjiuwen_team.view_task",
            "codex.exec",
        ),
        (
            (
                'const name = "mcp__openjiuwen_team__claim_task"; '
                'if (typeof tools[name] === "function") { '
                'await tools[name]({task_id:"task-1",status:"claimed"}); }'
            ),
            "openjiuwen_team.claim_task",
            "codex.exec",
        ),
        (
            'await tools["mcp__openjiuwen_team__send_message"]({to:"team-leader"});',
            "openjiuwen_team.send_message",
            "codex.exec",
        ),
        ("await tools.apply_patch(patch);", "apply_patch", "codex.exec"),
        (
            'await tools.exec_command({cmd:"cat result.md", workdir:"/tmp"});',
            "shell.cat",
            "codex.exec",
        ),
        (
            "text(ALL_TOOLS.filter(x => /write_file/.test(x.name)));",
            "codex.tool_discovery",
            "codex.tool_discovery",
        ),
        (
            "text('no nested tool');",
            "codex.internal.exec",
            "codex.internal.exec",
        ),
    ],
)
def test_hidden_exec_tool_name_is_decoded(
    code: str,
    expected_name: str,
    expected_display_name: str,
):
    from openjiuwen.agent_teams.observability.codex.bridge import (
        _decode_rollout_tool,
        _sdk_tool_display_name,
    )

    decoded = _decode_rollout_tool(
        {
            "type": "custom_tool_call",
            "call_id": "call-1",
            "name": "exec",
            "input": code,
        },
    )

    assert decoded["tool_name"] == expected_name
    assert decoded["display_name"] == expected_display_name
    assert (
        _sdk_tool_display_name(
            item_type="dynamicToolCall",
            tool_args={"code": code},
        )
        == expected_display_name
    )


@pytest.mark.level0
def test_rollout_reader_resolves_bundle_local_payloads(tmp_path: Path):
    bundle = tmp_path / "trace-trace-id-thread-id"
    payloads = bundle / "payloads"
    payloads.mkdir(parents=True)
    request = {
        "instructions": "team role",
        "input": [{"type": "message", "role": "user", "content": "inspect"}],
    }
    (payloads / "request.json").write_text(
        json.dumps(request),
        encoding="utf-8",
    )
    event = {
        "schema_version": 1,
        "seq": 3,
        "wall_time_unix_ms": 1_750_000_000_000,
        "thread_id": "thread-1",
        "codex_turn_id": "turn-1",
        "payload": {
            "type": "inference_started",
            "inference_call_id": "inference-1",
            "request_payload": {
                "raw_payload_id": "request-1",
                "kind": {"type": "inference_request"},
                "path": "payloads/request.json",
            },
        },
    }
    (bundle / "trace.jsonl").write_text(
        f"{json.dumps(event)}\n",
        encoding="utf-8",
    )
    received: list[dict] = []
    reader = CodexRolloutTraceReader(root=tmp_path, callback=received.append)

    count = reader._poll_once()

    assert count == 1
    assert len(received) == 1
    assert received[0]["resolved_payloads"]["request_payload"] == request


@pytest.mark.level0
def test_rollout_reader_removes_only_abandoned_owned_roots(
    tmp_path: Path,
    monkeypatch,
):
    from openjiuwen.agent_teams.observability.codex import rollout_trace

    dead = tmp_path / "openjiuwen-codex-rollout-dead"
    live = tmp_path / "openjiuwen-codex-rollout-live"
    dead.mkdir()
    live.mkdir()
    (dead / ".openjiuwen-owner.json").write_text(
        json.dumps({"pid": 111}),
        encoding="utf-8",
    )
    (live / ".openjiuwen-owner.json").write_text(
        json.dumps({"pid": 222}),
        encoding="utf-8",
    )
    old = time.time() - 120
    os.utime(dead, (old, old))
    os.utime(live, (old, old))
    monkeypatch.setattr(
        rollout_trace,
        "_pid_is_running",
        lambda pid: pid == 222,
    )

    removed = rollout_trace._cleanup_stale_roots(
        base_dir=tmp_path,
        now=time.time(),
    )

    assert removed == 1
    assert not dead.exists()
    assert live.exists()


@pytest.mark.asyncio
@pytest.mark.level0
async def test_rollout_reader_drains_and_removes_root_on_close(tmp_path: Path):
    root = tmp_path / "reader"
    bundle = root / "trace-trace-id-thread-id"
    bundle.mkdir(parents=True)
    event = {
        "seq": 1,
        "payload": {"type": "codex_turn_started"},
    }
    (bundle / "trace.jsonl").write_text(
        f"{json.dumps(event)}\n",
        encoding="utf-8",
    )
    received: list[dict] = []
    reader = CodexRolloutTraceReader(root=root, callback=received.append)

    await reader.aclose()

    assert len(received) == 1
    assert not root.exists()


@pytest.mark.level0
def test_rollout_inference_emits_exact_content_reasoning_and_tool_parent(
    monkeypatch,
):
    exporter_module = pytest.importorskip(
        "opentelemetry.sdk.trace.export.in_memory_span_exporter",
    )
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    from openjiuwen.agent_teams.observability import ObservabilityConfig
    from openjiuwen.agent_teams.observability.codex import CodexSpanBridge
    from openjiuwen.agent_teams.observability import setup
    from openjiuwen.agent_teams.observability.semconv import (
        LANGFUSE_OBSERVATION_INPUT,
        LANGFUSE_OBSERVATION_OUTPUT,
    )

    exporter = exporter_module.InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("codex-rollout-test")
    team_span = tracer.start_span("team.test")
    config = ObservabilityConfig(
        enabled=True,
        service_name="codex-rollout-test",
        sample_rate=1.0,
    )
    runtime = (tracer, config, team_span)
    monkeypatch.setattr(
        CodexSpanBridge,
        "_observability_runtime",
        staticmethod(lambda: runtime),
    )
    monkeypatch.setattr(setup, "get_tracer", lambda _: tracer)

    bridge = CodexSpanBridge(
        member_name="codex-test",
        member_agent_id="team_codex-test",
        team_name="team",
        session_id="session",
    )
    bridge.enable_rollout_trace()
    bridge.start_turn(
        prompt="inspect task",
        thread_id="thread-1",
        developer_instructions="team role",
        model="gpt-test",
    )
    bridge.record_rollout_event(
        {
            "seq": 1,
            "wall_time_unix_ms": 1_750_000_000_000,
            "thread_id": "thread-1",
            "codex_turn_id": "turn-1",
            "payload": {
                "type": "codex_turn_started",
                "thread_id": "thread-1",
                "codex_turn_id": "turn-1",
            },
        },
    )
    bridge.start_tool(
        call_id="call-1",
        tool_name="openjiuwen_team.view_task",
        tool_args={"task_id": "task-1"},
        item_type="mcpToolCall",
        server_name="openjiuwen-team",
    )
    bridge.record_rollout_event(
        {
            "seq": 2,
            "wall_time_unix_ms": 1_750_000_000_100,
            "thread_id": "thread-1",
            "codex_turn_id": "turn-1",
            "payload": {
                "type": "inference_started",
                "inference_call_id": "inference-1",
                "thread_id": "thread-1",
                "codex_turn_id": "turn-1",
                "model": "gpt-rollout",
                "provider_name": "openai",
            },
            "resolved_payloads": {
                "request_payload": {
                    "instructions": "team role",
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "inspect task"}],
                        },
                    ],
                },
            },
        },
    )
    bridge.record_rollout_event(
        {
            "seq": 3,
            "wall_time_unix_ms": 1_750_000_000_350,
            "thread_id": "thread-1",
            "codex_turn_id": "turn-1",
            "payload": {
                "type": "inference_completed",
                "inference_call_id": "inference-1",
                "response_id": "response-1",
                "upstream_request_id": "request-1",
            },
            "resolved_payloads": {
                "response_payload": {
                    "response_id": "response-1",
                    "upstream_request_id": "request-1",
                    "token_usage": {
                        "input_tokens": 10,
                        "cached_input_tokens": 2,
                        "output_tokens": 5,
                        "reasoning_output_tokens": 3,
                        "total_tokens": 15,
                    },
                    "output_items": [
                        {
                            "type": "reasoning",
                            "summary": [
                                {"type": "summary_text", "text": "check task first"},
                            ],
                            "content": [
                                {"type": "text", "text": "raw reasoning"},
                            ],
                        },
                        {
                            "type": "function_call",
                            "id": "item-1",
                            "call_id": "call-1",
                            "name": "openjiuwen_team.view_task",
                            "arguments": '{"task_id":"task-1"}',
                        },
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": "I will inspect it."},
                            ],
                        },
                    ],
                },
            },
        },
    )
    bridge.finish_tool(
        call_id="call-1",
        tool_name="openjiuwen_team.view_task",
        tool_args={"task_id": "task-1"},
        tool_result={"status": "pending"},
        item_type="mcpToolCall",
        server_name="openjiuwen-team",
    )
    bridge.finish_turn(status="completed")
    team_span.end()

    spans = list(exporter.get_finished_spans())
    llm_spans = [span for span in spans if span.name == "llm.call"]
    assert len(llm_spans) == 1
    llm_span = llm_spans[0]
    assert llm_span.start_time == 1_750_000_000_100_000_000
    assert llm_span.end_time == 1_750_000_000_350_000_000
    assert llm_span.attributes["codex.inference.call_id"] == "inference-1"
    assert llm_span.attributes["codex.model.call.paired"] is True
    assert llm_span.attributes["gen_ai.request.model"] == "gpt-rollout"
    assert "inspect task" in llm_span.attributes[LANGFUSE_OBSERVATION_INPUT]
    assert "I will inspect it." in llm_span.attributes[LANGFUSE_OBSERVATION_OUTPUT]

    reasoning_span = next(span for span in spans if span.name == "llm.reasoning")
    assert reasoning_span.parent.span_id == llm_span.context.span_id
    assert "raw reasoning" in reasoning_span.attributes[LANGFUSE_OBSERVATION_OUTPUT]
    turn_span = next(span for span in spans if span.name == "agent.codex-test.codex_turn.1")
    tool_span = next(span for span in spans if span.name == "tool.view_task")
    assert len([span for span in spans if span.name.startswith("tool.")]) == 1
    assert tool_span.parent.span_id == turn_span.context.span_id
    assert tool_span.attributes["codex.tool.parent_inference_call_id"] == "inference-1"
    assert tool_span.attributes["codex.tool.parent_exact"] is True
    assert tool_span.attributes["codex.tool.source"] == "sdk"
    assert tool_span.attributes["codex.tool.boundary_exact"] is True
    assert not [span for span in spans if span.name == "codex.sdk.summary"]


@pytest.mark.level0
def test_rollout_exec_wrapper_keeps_distinct_display_and_logical_names(
    monkeypatch,
):
    exporter_module = pytest.importorskip(
        "opentelemetry.sdk.trace.export.in_memory_span_exporter",
    )
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    from openjiuwen.agent_teams.observability import ObservabilityConfig
    from openjiuwen.agent_teams.observability.codex import CodexSpanBridge
    from openjiuwen.agent_teams.observability import setup
    from openjiuwen.agent_teams.observability.semconv import (
        LANGFUSE_OBSERVATION_OUTPUT,
    )

    exporter = exporter_module.InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("codex-hidden-tool-test")
    team_span = tracer.start_span("team.test")
    config = ObservabilityConfig(
        enabled=True,
        service_name="codex-hidden-tool-test",
        sample_rate=1.0,
    )
    monkeypatch.setattr(
        CodexSpanBridge,
        "_observability_runtime",
        staticmethod(lambda: (tracer, config, team_span)),
    )
    monkeypatch.setattr(setup, "get_tracer", lambda _: tracer)

    bridge = CodexSpanBridge(
        member_name="codex-test",
        member_agent_id="team_codex-test",
        team_name="team",
        session_id="session",
    )
    bridge.enable_rollout_trace()
    bridge.start_turn(
        prompt="inspect task",
        thread_id="thread-1",
        developer_instructions="team role",
        model="gpt-test",
    )
    bridge.record_rollout_event(
        {
            "seq": 1,
            "wall_time_unix_ms": 1_750_000_000_000,
            "thread_id": "thread-1",
            "codex_turn_id": "turn-1",
            "payload": {
                "type": "codex_turn_started",
                "thread_id": "thread-1",
                "codex_turn_id": "turn-1",
            },
        },
    )
    bridge.record_rollout_event(
        {
            "seq": 2,
            "wall_time_unix_ms": 1_750_000_000_100,
            "thread_id": "thread-1",
            "codex_turn_id": "turn-1",
            "payload": {
                "type": "inference_started",
                "inference_call_id": "inference-1",
                "model": "gpt-rollout",
            },
            "resolved_payloads": {
                "request_payload": {
                    "input": [{"type": "message", "role": "user", "content": "inspect"}],
                },
            },
        },
    )
    bridge.record_rollout_event(
        {
            "seq": 3,
            "wall_time_unix_ms": 1_750_000_000_350,
            "thread_id": "thread-1",
            "codex_turn_id": "turn-1",
            "payload": {
                "type": "inference_completed",
                "inference_call_id": "inference-1",
            },
            "resolved_payloads": {
                "response_payload": {
                    "output_items": [
                        {
                            "type": "custom_tool_call",
                            "call_id": "call-view-task",
                            "name": "exec",
                            "input": (
                                "const task = await "
                                "tools.mcp__openjiuwen_team__view_task("
                                '{action:"get",task_id:"task-1"});'
                            ),
                        },
                    ],
                },
            },
        },
    )
    bridge.record_rollout_event(
        {
            "seq": 4,
            "wall_time_unix_ms": 1_750_000_000_500,
            "thread_id": "thread-1",
            "codex_turn_id": "turn-1",
            "payload": {
                "type": "inference_started",
                "inference_call_id": "inference-2",
                "model": "gpt-rollout",
            },
            "resolved_payloads": {
                "request_payload": {
                    "input": [
                        {
                            "type": "custom_tool_call_output",
                            "call_id": "call-view-task",
                            "output": [{"status": "pending"}],
                        },
                    ],
                },
            },
        },
    )
    bridge.record_rollout_event(
        {
            "seq": 5,
            "wall_time_unix_ms": 1_750_000_000_700,
            "thread_id": "thread-1",
            "codex_turn_id": "turn-1",
            "payload": {
                "type": "inference_completed",
                "inference_call_id": "inference-2",
            },
            "resolved_payloads": {
                "response_payload": {
                    "output_items": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "done"}],
                        },
                    ],
                },
            },
        },
    )
    bridge.finish_turn(status="completed")
    team_span.end()

    spans = list(exporter.get_finished_spans())
    tool_span = next(span for span in spans if span.name == "tool.codex.exec")
    first_llm = next(
        span
        for span in spans
        if span.name == "llm.call" and span.attributes["codex.inference.call_id"] == "inference-1"
    )
    turn_span = next(span for span in spans if span.name == "agent.codex-test.codex_turn.1")
    assert tool_span.parent.span_id == turn_span.context.span_id
    assert first_llm.parent.span_id == turn_span.context.span_id
    assert tool_span.attributes["codex.tool.parent_inference_call_id"] == "inference-1"
    assert tool_span.start_time == 1_750_000_000_350_000_000
    assert tool_span.end_time == 1_750_000_000_500_000_000
    assert tool_span.attributes["codex.tool.source"] == "rollout"
    assert tool_span.attributes["codex.tool.boundary_exact"] is False
    assert "pending" in tool_span.attributes[LANGFUSE_OBSERVATION_OUTPUT]
    assert tool_span.attributes["gen_ai.tool.name"] == "codex.exec"
    assert tool_span.attributes["codex.tool.logical_name"] == (
        "openjiuwen_team.view_task"
    )


@pytest.mark.asyncio
@pytest.mark.level0
async def test_rollout_wait_requires_turn_end_and_each_enabled_source():
    from openjiuwen.agent_teams.observability.codex import CodexSpanBridge

    bridge = CodexSpanBridge(
        member_name="codex-test",
        member_agent_id="team_codex-test",
        team_name="team",
        session_id="session",
    )
    bridge._turn_span = object()
    bridge.enable_rollout_trace()
    bridge.enable_native_model_spans()
    old = time.monotonic() - 10
    bridge._last_rollout_event_at = old
    bridge._rollout_turn_ended_at = old
    bridge._last_native_span_at = 0.0
    started = time.monotonic()

    await bridge.wait_for_native_observations(timeout_s=0.05)

    assert time.monotonic() - started >= 0.045


@pytest.mark.level0
def test_codex_spans_do_not_bypass_redaction(monkeypatch):
    exporter_module = pytest.importorskip(
        "opentelemetry.sdk.trace.export.in_memory_span_exporter",
    )
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    from openjiuwen.agent_teams.observability import ObservabilityConfig
    from openjiuwen.agent_teams.observability import setup
    from openjiuwen.agent_teams.observability.codex import CodexSpanBridge

    secret = "codex-super-secret-value"
    exporter = exporter_module.InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("codex-redaction-test")
    team_span = tracer.start_span("team.test")
    config = ObservabilityConfig(
        enabled=True,
        service_name="codex-redaction-test",
        sample_rate=1.0,
        redact_prompts=True,
        redact_completions=True,
    )
    runtime = (tracer, config, team_span)
    monkeypatch.setattr(
        CodexSpanBridge,
        "_observability_runtime",
        staticmethod(lambda: runtime),
    )
    monkeypatch.setattr(setup, "get_tracer", lambda _: tracer)

    bridge = CodexSpanBridge(
        member_name="codex-test",
        member_agent_id="team_codex-test",
        team_name="team",
        session_id="session",
    )
    bridge.enable_rollout_trace()
    bridge.start_turn(
        prompt=secret,
        thread_id="thread-1",
        developer_instructions=secret,
        model="gpt-test",
    )
    bridge.start_tool(
        call_id="call-1",
        tool_name="openjiuwen_team.view_task",
        tool_args={"secret": secret},
        item_type="mcpToolCall",
    )
    bridge.record_error({"secret": secret})
    bridge.record_rollout_event(
        {
            "seq": 1,
            "wall_time_unix_ms": 1_750_000_000_100,
            "thread_id": "thread-1",
            "codex_turn_id": "turn-1",
            "payload": {
                "type": "inference_started",
                "inference_call_id": "inference-1",
                "model": "gpt-test",
            },
            "resolved_payloads": {
                "request_payload": {
                    "input": [{"type": "message", "content": secret}],
                },
            },
        },
    )
    bridge.record_rollout_event(
        {
            "seq": 2,
            "wall_time_unix_ms": 1_750_000_000_200,
            "thread_id": "thread-1",
            "codex_turn_id": "turn-1",
            "payload": {
                "type": "inference_completed",
                "inference_call_id": "inference-1",
            },
            "resolved_payloads": {
                "response_payload": {
                    "output_items": [
                        {
                            "type": "function_call",
                            "call_id": "call-1",
                            "name": "openjiuwen_team.view_task",
                            "arguments": json.dumps({"secret": secret}),
                        },
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": secret}],
                        },
                    ],
                },
            },
        },
    )
    bridge.finish_tool(
        call_id="call-1",
        tool_name="openjiuwen_team.view_task",
        tool_args={"secret": secret},
        tool_result={"secret": secret},
        item_type="mcpToolCall",
        error={"secret": secret},
    )
    bridge.finish_turn(status="failed", error={"secret": secret})

    summary_bridge = CodexSpanBridge(
        member_name="codex-summary",
        member_agent_id="team_codex-summary",
        team_name="team",
        session_id="session",
    )
    summary_bridge.start_turn(
        prompt=secret,
        thread_id="thread-2",
        developer_instructions=secret,
        model="gpt-test",
    )
    summary_bridge.append_output(secret)
    summary_bridge.finish_turn(status="completed")
    team_span.end()

    spans = list(exporter.get_finished_spans())
    exported = json.dumps(
        [
            {
                "attributes": dict(span.attributes),
                "events": [
                    {
                        "name": event.name,
                        "attributes": dict(event.attributes),
                    }
                    for event in span.events
                ],
                "status": span.status.description,
            }
            for span in spans
        ],
        ensure_ascii=False,
        default=str,
    )
    assert secret not in exported
    assert "sha256:" in exported
