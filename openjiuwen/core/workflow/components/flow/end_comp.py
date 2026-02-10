# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import re
import string
from typing import AsyncIterator, Union, AsyncGenerator, Any

from pydantic import BaseModel, ConfigDict, Field

from openjiuwen.core.common.constants.constant import END_NODE_STREAM
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.logging import workflow_logger, LogEventType
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.common.utils.dict_utils import extract_leaf_nodes, format_path
from openjiuwen.core.workflow.components.component import WorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session import END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY, \
    END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY, get_value_by_nested_path
from openjiuwen.core.session.node import Session
from openjiuwen.core.session.stream import OutputSchema


class EndConfig(BaseModel):
    response_template: str = Field(
        alias="responseTemplate",
        description="Response template for formatting the final output, format result {\"response\": \"...\"}",
        examples=["Hello {{name}}", "Error: {{error_msg}}"],
        min_length=1
    )
    model_config = ConfigDict(
        populate_by_name=True,
    )


class End(WorkflowComponent):
    def __init__(self, conf: Union[EndConfig, dict] = None):
        super().__init__()
        if conf:
            try:
                self._conf = EndConfig.model_validate(conf)
                self._template = TemplateProcessor(self._conf.response_template)
            except Exception as e:
                raise build_error(StatusCode.COMPONENT_END_PARAM_INVALID, reason=f"conf is invalid, {str(e)}", cause=e)
        else:
            self._conf = None
            self._template = None
        self._batch_template = None
        self._mix = False

    def set_mix(self):
        self._mix = True

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        workflow_logger.debug(
            "End component invoke started",
            event_type=LogEventType.WORKFLOW_COMPONENT_START,
            component_id=session.get_executable_id(),
            component_type_str="End",
            session_id=session.get_session_id(),
            metadata={"has_inputs": inputs is not None}
        )
        if self._template is not None:
            if inputs is None:
                inputs = {}
            return await self._render(inputs, session.get_env(END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY))
        else:
            if inputs is not None:
                output = {k: v for k, v in inputs.items() if v is not None} if isinstance(inputs, dict) else inputs
            else:
                output = None
            workflow_logger.debug(
                "End component invoke completed",
                event_type=LogEventType.WORKFLOW_COMPONENT_END,
                component_id=session.get_executable_id(),
                component_type_str="End",
                session_id=session.get_session_id(),
                metadata={"has_output": output is not None}
            )
            return {"output": output} if output is not None else output

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        workflow_logger.debug(
            "End component stream started",
            event_type=LogEventType.WORKFLOW_COMPONENT_START,
            component_id=session.get_executable_id(),
            component_type_str="End",
            session_id=session.get_session_id(),
            metadata={"has_inputs": inputs is not None, "has_template": self._template is not None}
        )
        if inputs is None:
            workflow_logger.debug(
                "End component received None inputs",
                event_type=LogEventType.WORKFLOW_COMPONENT_START,
                component_id=session.get_executable_id(),
                component_type_str="End",
                session_id=session.get_session_id(),
                metadata={"using_empty_dict": True}
            )
            inputs = {}
        try:
            if self._template is not None:
                workflow_logger.debug(
                    "End component rendering with template",
                    event_type=LogEventType.WORKFLOW_COMPONENT_START,
                    component_id=session.get_executable_id(),
                    component_type_str="End",
                    session_id=session.get_session_id(),
                    metadata={"input_keys": list(inputs.keys()) if isinstance(inputs, dict) else None}
                )
                generator = self._template.render_stream(inputs,
                                                         session.get_env(END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY))
                frame_count = 0
                async for frame in generator:
                    workflow_logger.debug(
                        "End component stream chunk",
                        event_type=LogEventType.WORKFLOW_STREAM_CHUNK,
                        component_id=session.get_executable_id(),
                        component_type_str="End",
                        session_id=session.get_session_id(),
                        metadata={"frame_index": frame.get("index"), "frame_count": frame_count}
                    )
                    frame_count += 1
                    yield OutputSchema(type=END_NODE_STREAM, index=frame.get("index"),
                                       payload=dict(response=frame.get("data")))
                workflow_logger.debug(
                    "End component stream completed",
                    event_type=LogEventType.WORKFLOW_COMPONENT_END,
                    component_id=session.get_executable_id(),
                    component_type_str="End",
                    session_id=session.get_session_id(),
                    metadata={"total_frames": frame_count}
                )
            else:
                if isinstance(inputs, dict):
                    for key, value in inputs.items():
                        yield dict(output={key: value})
                else:
                    yield dict(output=inputs)

        except Exception as e:
            workflow_logger.error(
                "End component stream error",
                event_type=LogEventType.WORKFLOW_COMPONENT_ERROR,
                component_id=session.get_executable_id(),
                component_type_str="End",
                session_id=session.get_session_id(),
                metadata={"error": str(e) if not UserConfig.is_sensitive() else None}
            )

    async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        workflow_logger.debug(
            "End component transform started",
            event_type=LogEventType.WORKFLOW_COMPONENT_START,
            component_id=session.get_executable_id(),
            component_type_str="End",
            session_id=session.get_session_id(),
            metadata={"has_template": self._template is not None}
        )
        if self._template is not None:
            generator = self._template.render_stream(inputs,
                                                     session.get_env(END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY))
            async for frame in generator:
                workflow_logger.debug(
                    "End component transform chunk",
                    event_type=LogEventType.WORKFLOW_STREAM_CHUNK,
                    component_id=session.get_executable_id(),
                    component_type_str="End",
                    session_id=session.get_session_id(),
                    metadata={"frame_index": frame.get("index")}
                )
                yield OutputSchema(type=END_NODE_STREAM, index=frame.get("index"),
                                   payload=dict(response=frame.get("data")))
        else:
            for (path, value) in extract_leaf_nodes(inputs):
                if isinstance(value, AsyncGenerator):
                    async for frame in value:
                        yield dict(output={format_path(path): frame})
                else:
                    yield dict(output={format_path(path): value})

    async def collect(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        workflow_logger.debug(
            "End component collect started",
            event_type=LogEventType.WORKFLOW_COMPONENT_START,
            component_id=session.get_executable_id(),
            component_type_str="End",
            session_id=session.get_session_id(),
            metadata={"has_template": self._template is not None}
        )
        if self._template is not None:
            return await self._render(inputs, session.get_env(END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY))
        else:
            chunks = []
            for (path, value) in extract_leaf_nodes(inputs):
                if isinstance(value, AsyncGenerator):
                    async for frame in value:
                        chunks.append({format_path(path): frame})
                else:
                    chunks.append({format_path(path): value})
            workflow_logger.debug(
                "End component collect completed",
                event_type=LogEventType.WORKFLOW_COMPONENT_END,
                component_id=session.get_executable_id(),
                component_type_str="End",
                session_id=session.get_session_id(),
                metadata={"chunk_count": len(chunks)}
            )
            return {
                "output": chunks
            }

    async def _render(self, inputs: Input, timeout: float = 0.2):
        if self._batch_template is None:
            processor = TemplateBatchProcessor(self._template, inputs)
            self._batch_template = processor
            if self._mix:
                async with self._batch_template.condition:
                    try:
                        await asyncio.wait_for(self._batch_template.condition.wait(),
                                               timeout if timeout and timeout > 0 else None)
                    except asyncio.TimeoutError as e:
                        workflow_logger.error(
                            "End component render timeout",
                            event_type=LogEventType.WORKFLOW_STREAM_ERROR,
                            component_id="end",
                            component_type_str="End",
                            metadata={"error": str(e)}
                        )
                        return None
                self._batch_template = None
                return None
        answer = await self._batch_template.render(inputs)
        async with self._batch_template.condition:
            self._batch_template.condition.notify_all()
        self._batch_template = None
        return {
            "response": answer,
        }


class TemplateProcessor:
    def __init__(self, template: str):
        self._template = template
        response_list = TemplateUtils.render_template_to_list(template)
        self._segments = response_list
        self._variables_positions: set[int] = set()
        self._current_position = 0
        for pos, res in enumerate(response_list):
            if res.startswith("{{") and res.endswith("}}"):
                self._variables_positions.add(pos)
                self._segments[pos] = res[2:-2]

        self._lock = asyncio.Lock()
        self._condition = asyncio.Condition()
        self._count = 0
        self._chunk_index = 0

    def current_position(self) -> int:
        return self._current_position

    def get_current_segment(self) -> str:
        return self._get_segment(self._current_position)

    def _need_render(self, inputs) -> bool:
        if not isinstance(inputs, dict):
            return False
        # Only check variable segments, not static text segments
        for pos, seg in enumerate(self._segments):
            if pos in self._variables_positions:
                if get_value_by_nested_path(seg, inputs) is not None:
                    return True
        return False

    def _get_segment(self, pos: int) -> str:
        if pos >= len(self._segments):
            return ""
        return self._segments[pos]

    def should_render(self) -> bool:
        return self._current_position in self._variables_positions

    def advance_position(self) -> int:
        self._current_position += 1
        return self._current_position

    def render(self, inputs: dict) -> str:
        return TemplateUtils.render_template(self._template, inputs)

    def reset(self):
        if self._current_position != 0:
            self._current_position = 0
        self._chunk_index = 0

    async def render_stream(self, inputs: dict, timeout: float = 0.2) -> AsyncGenerator:
        self._count += 1
        try:
            async for frame in self._render_stream(inputs, timeout):
                yield frame
        finally:
            self._count -= 1
            if self._count == 0:
                self.reset()

    async def _render_stream(self, inputs: dict, timeout: float) -> AsyncGenerator:
        # Even if _need_render returns False, static text segments should still be output
        # If all variables are None, at least output the static text segments
        has_any_value = self._need_render(inputs)
        should_wait = False
        while True:
            if should_wait:
                async with self._condition:
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=timeout if timeout > 0 else None)
                    except asyncio.TimeoutError as e:
                        workflow_logger.error(
                            "Template render stream timeout",
                            event_type=LogEventType.WORKFLOW_STREAM_ERROR,
                            component_type_str="TemplateProcessor",
                            metadata={"timeout_seconds": timeout, "error": str(e)}
                        )
                        self.advance_position()
                    except asyncio.CancelledError as e:
                        workflow_logger.error(
                            "Template render stream cancelled",
                            event_type=LogEventType.WORKFLOW_STREAM_ERROR,
                            component_type_str="TemplateProcessor",
                            metadata={"error": str(e)}
                        )
                        break
                should_wait = False
                workflow_logger.debug(
                    "Template segment finished",
                    event_type=LogEventType.WORKFLOW_STREAM_CHUNK,
                    component_type_str="TemplateProcessor",
                    metadata={"position": self._current_position}
                )
            async with self._lock:
                if self.is_finished():
                    break

                segment = self.get_current_segment()
                if not self.should_render():
                    # Static text segment, output directly
                    yield {"data": segment, "index": self._chunk_index}
                    self._chunk_index += 1
                    self.advance_position()
                    continue

                # Variable segment, render and output
                value = get_value_by_nested_path(segment, inputs)
                if value is None:
                    # In mixed mode (concurrent render_stream calls), should wait instead of skipping
                    if self._count > 1:
                        workflow_logger.debug(
                            "Template segment waiting for concurrent render",
                            event_type=LogEventType.WORKFLOW_STREAM_CHUNK,
                            component_type_str="TemplateProcessor",
                            metadata={"segment": segment, "concurrent_count": self._count}
                        )
                        should_wait = True
                        continue
                    # If only one call and no other values exist, skip None values
                    if not has_any_value:
                        workflow_logger.debug(
                            "Template segment skipped (None value)",
                            event_type=LogEventType.WORKFLOW_STREAM_CHUNK,
                            component_type_str="TemplateProcessor",
                            metadata={"segment": segment}
                        )
                        self.advance_position()
                        continue
                    workflow_logger.debug(
                        "Template segment waiting",
                        event_type=LogEventType.WORKFLOW_STREAM_CHUNK,
                        component_type_str="TemplateProcessor",
                        metadata={"segment": segment}
                    )
                    should_wait = True
                    continue

                if isinstance(value, AsyncGenerator):
                    workflow_logger.debug(
                        "Template segment is generator",
                        event_type=LogEventType.WORKFLOW_STREAM_CHUNK,
                        component_type_str="TemplateProcessor",
                        metadata={"segment": segment}
                    )
                    async for frame in value:
                        workflow_logger.debug(
                            "Template generator frame",
                            event_type=LogEventType.WORKFLOW_STREAM_CHUNK,
                            component_type_str="TemplateProcessor",
                            metadata={"chunk_index": self._chunk_index}
                        )
                        yield {"data": frame, "index": self._chunk_index}
                        self._chunk_index += 1
                else:
                    yield {"data": value, "index": self._chunk_index}
                    self._chunk_index += 1
                self.advance_position()
                async with self._condition:
                    self._condition.notify_all()
                workflow_logger.debug(
                    "Template segment completed",
                    event_type=LogEventType.WORKFLOW_STREAM_CHUNK,
                    component_type_str="TemplateProcessor",
                    metadata={"segment": segment, "position": self._current_position}
                )

    def is_finished(self) -> bool:
        return self._current_position >= len(self._segments)


class TemplateBatchProcessor:
    def __init__(self, template: TemplateProcessor, inputs: dict):
        self._template = template
        self._inputs = inputs if inputs is not None else {}
        self.condition = asyncio.Condition()

    async def render(self, inputs: dict) -> str:
        if inputs is None:
            inputs = self._inputs
        else:
            inputs = self._inputs | inputs
        generator = self._template.render_stream(inputs)
        answer = []
        async for frame in generator:
            workflow_logger.debug(
                "Template batch render frame",
                event_type=LogEventType.WORKFLOW_STREAM_CHUNK,
                component_type_str="TemplateBatchProcessor",
                metadata={"frame_index": frame.get("index")}
            )
            answer.append(str(frame.get("data")))
        return "".join(answer)


class TemplateUtils:

    @staticmethod
    def render_template(template: str, inputs: dict) -> str:

        if not isinstance(template, str):
            raise TypeError("template must be a string")
        if not isinstance(inputs, dict):
            raise TypeError("inputs must be a dict")

        template = template.replace("{{", "$").replace("}}", "")
        t = string.Template(template)
        return t.safe_substitute(**inputs)

    @staticmethod
    def render_template_to_list(template: str) -> list[str | Any]:
        return list(filter(None, re.split(r'(\{\{[^}]+\}\})', template)))
