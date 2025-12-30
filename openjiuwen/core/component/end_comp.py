# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import re
import string
from typing import AsyncIterator, TypedDict, Union, AsyncGenerator, Any

from openjiuwen.core.common.constants.constant import END_NODE_STREAM
from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.common.utlis.dict_utils import extract_leaf_nodes, format_path
from openjiuwen.core.component.base import WorkflowComponent
from openjiuwen.core.context_engine.base import Context
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.runtime.base import ComponentExecutable
from openjiuwen.core.runtime.constants import END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY, \
    END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY
from openjiuwen.core.runtime.runtime import Runtime
from openjiuwen.core.runtime.utils import get_value_by_nested_path
from openjiuwen.core.stream.base import OutputSchema

RESPONSE_TEMPLATE = "responseTemplate"


class EndConfig(TypedDict):
    responseTemplate: str


class End(ComponentExecutable, WorkflowComponent):
    def __init__(self, conf: Union[EndConfig, dict] = None):
        super().__init__()
        self.conf = conf
        self.template = None
        self._batch_template = None
        self._mix = False
        if conf is not None and conf.get(RESPONSE_TEMPLATE) is not None:
            template = conf.get(RESPONSE_TEMPLATE)
            if not isinstance(template, str):
                raise JiuWenBaseException(StatusCode.WORKFLOW_END_CREATE_VALUE.code,
                                          message=StatusCode.WORKFLOW_END_CREATE_VALUE.errmsg.format(
                                              reason="`responseTemplate` type error, is not str"))
            if template != "":
                self.template = TemplateProcessor(template)

    def set_mix(self):
        self._mix = True

    async def invoke(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        if self.template is not None:
            if inputs is None:
                inputs = {}
            return await self._render(inputs, runtime.get_env(END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY))
        else:
            if inputs is not None:
                output = {k: v for k, v in inputs.items() if v is not None} if isinstance(inputs, dict) else inputs
            else:
                output = None
            logger.debug(f"end component invoke method output: {output}")
            return {"output": output}

    async def stream(self, inputs: Input, runtime: Runtime, context: Context) -> AsyncIterator[Output]:
        logger.debug(f"end component stream method inputs: {inputs}")
        if inputs is None:
            logger.debug("end component stream method received None inputs, using empty dict")
            inputs = {}
        try:
            if self.template is not None:
                logger.debug(f"end component has template, inputs: {inputs}")
                generator = self.template.render_stream(inputs,
                                                        runtime.get_env(END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY))
                frame_count = 0
                async for frame in generator:
                    logger.debug(f"rendering stream frame: {frame}")
                    frame_count += 1
                    yield OutputSchema(type=END_NODE_STREAM, index=frame.get("index"),
                                       payload=dict(answer=frame.get("data")))
                logger.debug(f"end component stream method yielded {frame_count} frames")
            else:
                if isinstance(inputs, dict):
                    for key, value in inputs.items():
                        yield dict(output={key: value})
                else:
                    yield dict(output=inputs)

        except Exception as e:
            if UserConfig.is_sensitive():
                logger.info("stream output error")
            else:
                logger.error("stream output error: {}".format(e), exc_info=True)

    async def transform(self, inputs: Input, runtime: Runtime, context: Context) -> AsyncIterator[Output]:
        logger.debug(f"end component transform method inputs: {inputs}")
        if self.template is not None:
            generator = self.template.render_stream(inputs,
                                                    runtime.get_env(END_COMP_TEMPLATE_RENDER_POSITION_TIMEOUT_KEY))
            async for frame in generator:
                logger.debug(f"rendering transform frame: {frame}")
                yield OutputSchema(type=END_NODE_STREAM, index=frame.get("index"),
                                   payload=dict(answer=frame.get("data")))
        else:
            for (path, value) in extract_leaf_nodes(inputs):
                if isinstance(value, AsyncGenerator):
                    async for frame in value:
                        yield dict(output={format_path(path): frame})
                else:
                    yield dict(output={format_path(path): value})

    async def collect(self, inputs: Input, runtime: Runtime, context: Context) -> Output:
        logger.debug(f"end component collect method inputs: {inputs}")
        if self.template is not None:
            return await self._render(inputs, runtime.get_env(END_COMP_TEMPLATE_BATCH_READER_TIMEOUT_KEY))
        else:
            chunks = []
            for (path, value) in extract_leaf_nodes(inputs):
                if isinstance(value, AsyncGenerator):
                    async for frame in value:
                        chunks.append({format_path(path): frame})
                else:
                    chunks.append({format_path(path): value})
            logger.debug(f"collect chunks: {chunks}")
            return {
                "collect_output": chunks
            }

    async def _render(self, inputs: Input, timeout: float = 0.2):
        if self._batch_template is None:
            processor = TemplateBatchProcessor(self.template, inputs)
            self._batch_template = processor
            if self._mix:
                async with self._batch_template.condition:
                    try:
                        await asyncio.wait_for(self._batch_template.condition.wait(),
                                               timeout if timeout and timeout > 0 else None)
                    except asyncio.TimeoutError as e:
                        logger.error(f"render template stream timeout, {e}")
                        return None
                self._batch_template = None
                return None
        answer = await self._batch_template.render(inputs)
        async with self._batch_template.condition:
            self._batch_template.condition.notify_all()
        self._batch_template = None
        return {
            "responseContent": answer,
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
                        logger.error(f"render template stream timeout {timeout}s, {e}")
                        self.advance_position()
                    except asyncio.CancelledError as e:
                        logger.error(f"render template stream cancelled {e}")
                        break
                should_wait = False
                logger.debug("previous segment has been finished")
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
                        logger.debug(
                            f"current segment [{segment}] should wait for other method "
                            f"(concurrent render_stream)"
                        )
                        should_wait = True
                        continue
                    # If only one call and no other values exist, skip None values
                    if not has_any_value:
                        logger.debug(f"current segment [{segment}] is None and no other values exist, skipping")
                        self.advance_position()
                        continue
                    logger.debug(f"current segment [{segment}] should wait for other method")
                    should_wait = True
                    continue

                if isinstance(value, AsyncGenerator):
                    logger.debug(f"current segment generator [{segment}] is generator")
                    async for frame in value:
                        logger.debug(f"rendering generator frame: {frame}")
                        yield {"data": frame, "index": self._chunk_index}
                        self._chunk_index += 1
                else:
                    yield {"data": value, "index": self._chunk_index}
                    self._chunk_index += 1
                self.advance_position()
                async with self._condition:
                    self._condition.notify_all()
                logger.debug(f"current segment [{segment}] has been finished")

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
            logger.debug(f"rendering collect frame: {frame}")
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
