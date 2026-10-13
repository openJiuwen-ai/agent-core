# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Generated OpenTelemetry GenAI semantic-convention attribute names.

Source: open-telemetry/semantic-conventions-genai
Revision: fee465db333bdd6a7d2faa320edab5cf3101a4f4

This module contains only upstream standard definitions. Replace it as one
unit when the pinned GenAI semantic-conventions registry is upgraded; project
extensions belong in ``semconv.py``.
"""

from __future__ import annotations

from typing import Final


GEN_AI_SEMCONV_REVISION: Final = "fee465db333bdd6a7d2faa320edab5cf3101a4f4"
GEN_AI_SEMCONV_SCHEMA_URL: Final = "https://opentelemetry.io/schemas/gen-ai-dev/1.42.0-dev"
GEN_AI_CORE_SEMCONV_SCHEMA_URL: Final = "https://opentelemetry.io/schemas/1.44.0"
GEN_AI_SEMCONV_ATTRIBUTE_COUNT: Final = 72

GEN_AI_AGENT_DESCRIPTION: Final = "gen_ai.agent.description"
GEN_AI_AGENT_ID: Final = "gen_ai.agent.id"
GEN_AI_AGENT_NAME: Final = "gen_ai.agent.name"
GEN_AI_AGENT_VERSION: Final = "gen_ai.agent.version"
GEN_AI_CONVERSATION_COMPACTED: Final = "gen_ai.conversation.compacted"
GEN_AI_CONVERSATION_ID: Final = "gen_ai.conversation.id"
GEN_AI_DATA_SOURCE_ID: Final = "gen_ai.data_source.id"
GEN_AI_EMBEDDINGS_DIMENSION_COUNT: Final = "gen_ai.embeddings.dimension.count"
GEN_AI_EVALUATION_EXPLANATION: Final = "gen_ai.evaluation.explanation"
GEN_AI_EVALUATION_NAME: Final = "gen_ai.evaluation.name"
GEN_AI_EVALUATION_SCORE_LABEL: Final = "gen_ai.evaluation.score.label"
GEN_AI_EVALUATION_SCORE_VALUE: Final = "gen_ai.evaluation.score.value"
GEN_AI_INPUT_MESSAGES: Final = "gen_ai.input.messages"
GEN_AI_MEMORY_QUERY_TEXT: Final = "gen_ai.memory.query.text"
GEN_AI_MEMORY_RECORD_COUNT: Final = "gen_ai.memory.record.count"
GEN_AI_MEMORY_RECORD_ID: Final = "gen_ai.memory.record.id"
GEN_AI_MEMORY_RECORDS: Final = "gen_ai.memory.records"
GEN_AI_MEMORY_STORE_ID: Final = "gen_ai.memory.store.id"
GEN_AI_OPERATION_NAME: Final = "gen_ai.operation.name"
GEN_AI_OUTPUT_MESSAGES: Final = "gen_ai.output.messages"
GEN_AI_OUTPUT_TYPE: Final = "gen_ai.output.type"
GEN_AI_PROMPT_NAME: Final = "gen_ai.prompt.name"
GEN_AI_PROMPT_VARIABLE: Final = "gen_ai.prompt.variable"
GEN_AI_PROMPT_VERSION: Final = "gen_ai.prompt.version"
GEN_AI_PROVIDER_NAME: Final = "gen_ai.provider.name"
GEN_AI_REQUEST_CHOICE_COUNT: Final = "gen_ai.request.choice.count"
GEN_AI_REQUEST_ENCODING_FORMATS: Final = "gen_ai.request.encoding_formats"
GEN_AI_REQUEST_FREQUENCY_PENALTY: Final = "gen_ai.request.frequency_penalty"
GEN_AI_REQUEST_MAX_TOKENS: Final = "gen_ai.request.max_tokens"
GEN_AI_REQUEST_MODEL: Final = "gen_ai.request.model"
GEN_AI_REQUEST_PRESENCE_PENALTY: Final = "gen_ai.request.presence_penalty"
GEN_AI_REQUEST_PREVIOUS_RESPONSE_ID: Final = "gen_ai.request.previous_response.id"
GEN_AI_REQUEST_REASONING_LEVEL: Final = "gen_ai.request.reasoning.level"
GEN_AI_REQUEST_SEED: Final = "gen_ai.request.seed"
GEN_AI_REQUEST_STOP_SEQUENCES: Final = "gen_ai.request.stop_sequences"
GEN_AI_REQUEST_STREAM: Final = "gen_ai.request.stream"
GEN_AI_REQUEST_STREAM_CURSOR: Final = "gen_ai.request.stream_cursor"
GEN_AI_REQUEST_TEMPERATURE: Final = "gen_ai.request.temperature"
GEN_AI_REQUEST_TOP_K: Final = "gen_ai.request.top_k"
GEN_AI_REQUEST_TOP_P: Final = "gen_ai.request.top_p"
GEN_AI_RESPONSE_FINISH_REASONS: Final = "gen_ai.response.finish_reasons"
GEN_AI_RESPONSE_ID: Final = "gen_ai.response.id"
GEN_AI_RESPONSE_MODEL: Final = "gen_ai.response.model"
GEN_AI_RESPONSE_STATUS: Final = "gen_ai.response.status"
GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK: Final = "gen_ai.response.time_to_first_chunk"
GEN_AI_RETRIEVAL_DOCUMENTS: Final = "gen_ai.retrieval.documents"
GEN_AI_RETRIEVAL_QUERY_TEXT: Final = "gen_ai.retrieval.query.text"
GEN_AI_RETRIEVAL_TOP_K: Final = "gen_ai.retrieval.top_k"
GEN_AI_SYSTEM_INSTRUCTIONS: Final = "gen_ai.system_instructions"
GEN_AI_TOKEN_TYPE: Final = "gen_ai.token.type"
GEN_AI_TOOL_CALL_ARGUMENTS: Final = "gen_ai.tool.call.arguments"
GEN_AI_TOOL_CALL_ID: Final = "gen_ai.tool.call.id"
GEN_AI_TOOL_CALL_RESULT: Final = "gen_ai.tool.call.result"
GEN_AI_TOOL_DEFINITIONS: Final = "gen_ai.tool.definitions"
GEN_AI_TOOL_DESCRIPTION: Final = "gen_ai.tool.description"
GEN_AI_TOOL_NAME: Final = "gen_ai.tool.name"
GEN_AI_TOOL_TYPE: Final = "gen_ai.tool.type"
GEN_AI_USAGE_AUDIO_CACHE_READ_INPUT_TOKENS: Final = "gen_ai.usage.audio.cache_read.input_tokens"
GEN_AI_USAGE_AUDIO_INPUT_TOKENS: Final = "gen_ai.usage.audio.input_tokens"
GEN_AI_USAGE_AUDIO_OUTPUT_TOKENS: Final = "gen_ai.usage.audio.output_tokens"
GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS: Final = "gen_ai.usage.cache_read.input_tokens"
GEN_AI_USAGE_CACHE_WRITE_INPUT_TOKENS: Final = "gen_ai.usage.cache_write.input_tokens"
GEN_AI_USAGE_IMAGE_CACHE_READ_INPUT_TOKENS: Final = "gen_ai.usage.image.cache_read.input_tokens"
GEN_AI_USAGE_IMAGE_INPUT_TOKENS: Final = "gen_ai.usage.image.input_tokens"
GEN_AI_USAGE_IMAGE_OUTPUT_TOKENS: Final = "gen_ai.usage.image.output_tokens"
GEN_AI_USAGE_INPUT_TOKENS: Final = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS: Final = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_REASONING_OUTPUT_TOKENS: Final = "gen_ai.usage.reasoning.output_tokens"
GEN_AI_USAGE_TEXT_CACHE_READ_INPUT_TOKENS: Final = "gen_ai.usage.text.cache_read.input_tokens"
GEN_AI_USAGE_TEXT_INPUT_TOKENS: Final = "gen_ai.usage.text.input_tokens"
GEN_AI_USAGE_TEXT_OUTPUT_TOKENS: Final = "gen_ai.usage.text.output_tokens"
GEN_AI_WORKFLOW_NAME: Final = "gen_ai.workflow.name"

__all__ = (
    "GEN_AI_SEMCONV_REVISION",
    "GEN_AI_SEMCONV_SCHEMA_URL",
    "GEN_AI_CORE_SEMCONV_SCHEMA_URL",
    "GEN_AI_SEMCONV_ATTRIBUTE_COUNT",
    "GEN_AI_AGENT_DESCRIPTION",
    "GEN_AI_AGENT_ID",
    "GEN_AI_AGENT_NAME",
    "GEN_AI_AGENT_VERSION",
    "GEN_AI_CONVERSATION_COMPACTED",
    "GEN_AI_CONVERSATION_ID",
    "GEN_AI_DATA_SOURCE_ID",
    "GEN_AI_EMBEDDINGS_DIMENSION_COUNT",
    "GEN_AI_EVALUATION_EXPLANATION",
    "GEN_AI_EVALUATION_NAME",
    "GEN_AI_EVALUATION_SCORE_LABEL",
    "GEN_AI_EVALUATION_SCORE_VALUE",
    "GEN_AI_INPUT_MESSAGES",
    "GEN_AI_MEMORY_QUERY_TEXT",
    "GEN_AI_MEMORY_RECORD_COUNT",
    "GEN_AI_MEMORY_RECORD_ID",
    "GEN_AI_MEMORY_RECORDS",
    "GEN_AI_MEMORY_STORE_ID",
    "GEN_AI_OPERATION_NAME",
    "GEN_AI_OUTPUT_MESSAGES",
    "GEN_AI_OUTPUT_TYPE",
    "GEN_AI_PROMPT_NAME",
    "GEN_AI_PROMPT_VARIABLE",
    "GEN_AI_PROMPT_VERSION",
    "GEN_AI_PROVIDER_NAME",
    "GEN_AI_REQUEST_CHOICE_COUNT",
    "GEN_AI_REQUEST_ENCODING_FORMATS",
    "GEN_AI_REQUEST_FREQUENCY_PENALTY",
    "GEN_AI_REQUEST_MAX_TOKENS",
    "GEN_AI_REQUEST_MODEL",
    "GEN_AI_REQUEST_PRESENCE_PENALTY",
    "GEN_AI_REQUEST_PREVIOUS_RESPONSE_ID",
    "GEN_AI_REQUEST_REASONING_LEVEL",
    "GEN_AI_REQUEST_SEED",
    "GEN_AI_REQUEST_STOP_SEQUENCES",
    "GEN_AI_REQUEST_STREAM",
    "GEN_AI_REQUEST_STREAM_CURSOR",
    "GEN_AI_REQUEST_TEMPERATURE",
    "GEN_AI_REQUEST_TOP_K",
    "GEN_AI_REQUEST_TOP_P",
    "GEN_AI_RESPONSE_FINISH_REASONS",
    "GEN_AI_RESPONSE_ID",
    "GEN_AI_RESPONSE_MODEL",
    "GEN_AI_RESPONSE_STATUS",
    "GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK",
    "GEN_AI_RETRIEVAL_DOCUMENTS",
    "GEN_AI_RETRIEVAL_QUERY_TEXT",
    "GEN_AI_RETRIEVAL_TOP_K",
    "GEN_AI_SYSTEM_INSTRUCTIONS",
    "GEN_AI_TOKEN_TYPE",
    "GEN_AI_TOOL_CALL_ARGUMENTS",
    "GEN_AI_TOOL_CALL_ID",
    "GEN_AI_TOOL_CALL_RESULT",
    "GEN_AI_TOOL_DEFINITIONS",
    "GEN_AI_TOOL_DESCRIPTION",
    "GEN_AI_TOOL_NAME",
    "GEN_AI_TOOL_TYPE",
    "GEN_AI_USAGE_AUDIO_CACHE_READ_INPUT_TOKENS",
    "GEN_AI_USAGE_AUDIO_INPUT_TOKENS",
    "GEN_AI_USAGE_AUDIO_OUTPUT_TOKENS",
    "GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS",
    "GEN_AI_USAGE_CACHE_WRITE_INPUT_TOKENS",
    "GEN_AI_USAGE_IMAGE_CACHE_READ_INPUT_TOKENS",
    "GEN_AI_USAGE_IMAGE_INPUT_TOKENS",
    "GEN_AI_USAGE_IMAGE_OUTPUT_TOKENS",
    "GEN_AI_USAGE_INPUT_TOKENS",
    "GEN_AI_USAGE_OUTPUT_TOKENS",
    "GEN_AI_USAGE_REASONING_OUTPUT_TOKENS",
    "GEN_AI_USAGE_TEXT_CACHE_READ_INPUT_TOKENS",
    "GEN_AI_USAGE_TEXT_INPUT_TOKENS",
    "GEN_AI_USAGE_TEXT_OUTPUT_TOKENS",
    "GEN_AI_WORKFLOW_NAME",
)
