from __future__ import annotations

import json
import os
from pathlib import Path
from datetime import datetime, timezone
import uuid
from typing import Any

from pydantic import BaseModel, Field

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent import AbilityManager, ReActAgent
from openjiuwen.core.context_engine.processor.forked.compressor.support.forked_agent import (
    ForkedAgent,
)


class SessionMemoryAgentConfig(BaseModel):
    max_iterations: int = Field(default=3, ge=1, le=3)
    enable_debug_dump: bool = Field(default=False)
    debug_dump_dir: str | None = Field(default=None)


class SessionMemoryAbilityManager(AbilityManager):
    """Expose stable model tools while enforcing host-side execution policy."""

    def __init__(self, owner_id: str | None = None) -> None:
        super().__init__(owner_id=owner_id)
        self._model_tools: list[Any] = []
        self._allowed_notes_path: Path | None = None
        self.rejected_tool_calls: list[dict[str, Any]] = []

    def set_model_tools(self, tools: list[Any] | None) -> None:
        self._model_tools = list(tools or [])

    def set_allowed_notes_path(self, path: str | Path | None) -> None:
        self._allowed_notes_path = Path(path).expanduser().resolve() if path else None

    async def list_tool_info(
        self,
        names: list[str] | None = None,
        mcp_server_name: str | None = None,
    ) -> list[Any]:
        _ = names, mcp_server_name
        return list(self._model_tools)

    def debug_snapshot(self) -> dict[str, Any]:
        return {
            "rejected_tool_calls": list(self.rejected_tool_calls),
            "model_tools": [self._to_jsonable(item) for item in self._model_tools],
            "allowed_notes_path": str(self._allowed_notes_path) if self._allowed_notes_path else None,
        }

    @staticmethod
    def _to_jsonable(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)

    async def execute(
        self,
        ctx,
        tool_call,
        session,
        parallel_tool_calls: bool = True,
        tag=None,
    ):
        tool_calls = self._normalize_tool_calls(tool_call)
        allowed = []
        rejected = []
        for call in tool_calls:
            if call.name != "edit_file":
                rejected.append(call)
                self.rejected_tool_calls.append(
                    {
                        "name": call.name,
                        "tool_call_id": call.id,
                        "reason": "only edit_file is executable",
                    }
                )
                continue
            if not self._is_allowed_edit_path(call.arguments):
                rejected.append(call)
                self.rejected_tool_calls.append(
                    {
                        "name": call.name,
                        "tool_call_id": call.id,
                        "reason": "target path is not the pending notes file",
                    }
                )
                continue
            allowed.append(call)

        rejected_results = {}
        for call in rejected:
            rejected_results[id(call)] = (
                None,
                ToolMessage(
                    content=(
                        "Tool call rejected by SessionMemory policy. "
                        "Only edit_file on the pending notes file is allowed."
                    ),
                    tool_call_id=call.id or "",
                ),
            )

        allowed_results = []
        if allowed:
            allowed_results = await super().execute(
                ctx=ctx,
                tool_call=allowed,
                session=session,
                parallel_tool_calls=parallel_tool_calls,
                tag=tag,
            )
        allowed_results_by_identity = {id(call): result for call, result in zip(allowed, allowed_results)}
        return [rejected_results.get(id(call)) or allowed_results_by_identity[id(call)] for call in tool_calls]

    def _is_allowed_edit_path(self, arguments: Any) -> bool:
        if self._allowed_notes_path is None:
            return False
        try:
            parsed = arguments if isinstance(arguments, dict) else json.loads(arguments or "{}")
        except (TypeError, ValueError):
            return False
        requested = parsed.get("file_path")
        if not isinstance(requested, str) or not requested:
            return False
        return Path(requested).expanduser().resolve() == self._allowed_notes_path


class SessionMemoryAgent(ForkedAgent):
    """Forked compression executor that updates session memory through ReActAgent."""

    def __init__(
        self,
        model: Any,
        *,
        model_config: Any,
        model_client_config: Any,
        prompt_template: list[dict[str, str]] | None = None,
        config: SessionMemoryAgentConfig | None = None,
        workspace_root: str | Path | None = None,
    ) -> None:
        self._session_memory_config = config or SessionMemoryAgentConfig()
        self._ability_manager: SessionMemoryAbilityManager | None = None
        self._workspace_root = Path(workspace_root).expanduser().resolve() if workspace_root else None
        self._edit_tool = None
        super().__init__(
            model,
            model_config=model_config,
            model_client_config=model_client_config,
            agent_name="session_memory_update_agent",
            agent_description="Updates the session memory Markdown file.",
            prompt_template=prompt_template,
            max_iterations=self._session_memory_config.max_iterations,
        )

    @property
    def ability_manager(self) -> SessionMemoryAbilityManager | None:
        return self._ability_manager

    def _configure_agent(self, agent: ReActAgent) -> None:
        manager = SessionMemoryAbilityManager(owner_id=agent.card.id)
        manager.set_context_engine(agent.context_engine)
        if self._workspace_root is not None:
            from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperation, SysOperationCard
            from openjiuwen.harness.tools.filesystem import EditFileTool

            operation_card = SysOperationCard(
                id=f"{agent.card.id}_sysop",
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(
                    sandbox_root=[str(self._workspace_root)],
                    restrict_to_sandbox=True,
                ),
            )
            operation = SysOperation(operation_card)
            tool = EditFileTool(operation, agent_id=agent.card.id)
            manager.add_ability(tool.card, tool)
            self._edit_tool = tool
        agent.ability_manager = manager
        self._ability_manager = manager

    def configure_request(
        self,
        *,
        tools: list[Any] | None,
        allowed_notes_path: str | Path,
    ) -> None:
        agent = self._ensure_agent()
        _ = agent
        if self._ability_manager is None:
            raise RuntimeError("SessionMemory ability manager is not initialized")
        self._ability_manager.set_model_tools(tools)
        self._ability_manager.set_allowed_notes_path(allowed_notes_path)

    async def invoke(self, request):
        error = None
        result = None
        try:
            result = await super().invoke(request)
            return result
        except Exception as exc:
            error = repr(exc)
            raise
        finally:
            self._dump_debug_request(request, result, error)

    def _dump_debug_request(self, request, result, error: str | None) -> None:
        config = self._session_memory_config
        if not config.enable_debug_dump:
            return
        directory = Path(
            config.debug_dump_dir
            or os.getenv("OPENJIUWEN_SESSION_MEMORY_DEBUG_DUMP_DIR")
            or (Path.cwd() / "context" / "session_memory_debug_logs")
        )
        try:
            directory.mkdir(parents=True, exist_ok=True)
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request": {
                    "prompt": request.prompt,
                    "system_messages": [self._message_to_jsonable(item) for item in request.system_messages],
                    "context_messages": [self._message_to_jsonable(item) for item in request.context_messages],
                    "tools": [self._message_to_jsonable(item) for item in request.tools or []],
                    "exclude_recent_messages": request.exclude_recent_messages,
                },
                "result": self._message_to_jsonable(result.response) if result is not None else None,
                "error": error,
                "agent": {
                    "max_iterations": self._session_memory_config.max_iterations,
                    "agent_session_namespace": self._agent_namespace,
                },
                "ability": self._ability_manager.debug_snapshot() if self._ability_manager else {},
            }
            path = directory / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex}.json"
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        except Exception:
            # Debug tracing must never break the compression path.
            return

    @staticmethod
    def _message_to_jsonable(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(value)


__all__ = [
    "SessionMemoryAgent",
    "SessionMemoryAgentConfig",
    "SessionMemoryAbilityManager",
]
