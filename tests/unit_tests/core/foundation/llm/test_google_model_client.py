"""Offline protocol checks using the real Google SDK and openJiuwen Model."""

import json
import unittest
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import httpx
from google import genai

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.llm.model_clients.google_model_client import (
    GoogleModelClient,
    _contents,
    register_google_client,
)
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, SystemMessage, ToolMessage, UserMessage
from openjiuwen.core.foundation.tool import ToolInfo

MODEL = "gemini-3.8-flash"
HOST = "https://generativelanguage.googleapis.com"
TOOL = {
    "type": "function",
    "function": {
        "name": "measure",
        "description": "Read experiment result",
        "parameters": {"type": "object", "properties": {"trial": {"type": "integer"}}, "required": ["trial"]},
    },
}
SIGNED = {"functionCall": {"name": "measure", "args": {"trial": 1}}, "thoughtSignature": "AP9zaWduZWQ="}


def response(parts, finish="STOP", usage=True):
    result: dict[str, Any] = {
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish}],
        "modelVersion": MODEL,
        "responseId": "native-response",
    }
    if usage:
        result["usageMetadata"] = {
            "promptTokenCount": 10,
            "candidatesTokenCount": 4,
            "thoughtsTokenCount": 3,
            "totalTokenCount": 17,
            "cachedContentTokenCount": 2,
        }
    return result


class GoogleClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        register_google_client()
        register_google_client()
        self.model = Model(
            ModelClientConfig(
                client_provider="Google",
                api_key="fixture-key",
                api_base=HOST,
                timeout=20,
                custom_headers={"x-fixture": "default"},
            ),
            ModelRequestConfig(model=MODEL, max_tokens=512, temperature=0, top_p=0.9, stop="END"),
        )
        self.requests = []
        self.clients = []
        self.client = cast(GoogleModelClient, self.model._client)

    def transport(self, replies):
        def handle(request):
            self.requests.append(request)
            reply = replies.pop(0)
            return reply if isinstance(reply, httpx.Response) else httpx.Response(200, json=reply)

        real_client = genai.Client

        def sdk(**kwargs):
            options = kwargs["http_options"]
            options.async_client_args = {"transport": httpx.MockTransport(handle)}
            client = real_client(**kwargs)
            self.clients.append(client)
            return client

        return patch("openjiuwen.core.foundation.llm.model_clients.google_model_client.genai.Client", side_effect=sdk)

    def body(self, index=0):
        return json.loads(self.requests[index].content)

    async def test_native_request_config_headers_usage_and_parser(self):
        parser = unittest.mock.Mock(parse=AsyncMock(return_value={"parsed": True}))
        with self.transport([response([{"text": "Ablation supports the hypothesis."}])]):
            result = await self.model.invoke(
                [SystemMessage(content="Write a scientific abstract."), UserMessage(content="Report the ablation.")],
                output_parser=parser,
                timeout=2,
                custom_headers={"x-fixture": "override"},
                request_purpose="compaction",
                context_operation_id="internal",
            )
        request = self.requests[0]
        self.assertEqual(str(request.url), HOST + "/v1beta/models/" + MODEL + ":generateContent")
        self.assertEqual(request.headers["x-goog-api-key"], "fixture-key")
        self.assertEqual(request.headers["x-fixture"], "override")
        self.assertEqual(request.extensions["timeout"]["read"], 2)
        self.assertEqual(self.body()["systemInstruction"]["parts"], [{"text": "Write a scientific abstract."}])
        self.assertEqual(
            self.body()["generationConfig"],
            {"temperature": 0, "topP": 0.9, "maxOutputTokens": 512, "stopSequences": ["END"]},
        )
        self.assertEqual(
            (
                result.usage_metadata.input_tokens,
                result.usage_metadata.output_tokens,
                result.usage_metadata.total_tokens,
                result.usage_metadata.reasoning_tokens,
            ),
            (10, 7, 17, 3),
        )
        self.assertEqual(result.usage_metadata.cache_read_tokens, 2)
        self.assertEqual(result.parser_content, {"parsed": True})
        self.assertEqual(result.finish_reason, "stop")
        self.assertTrue(self.clients[0]._api_client._async_httpx_client.is_closed)
        self.assertTrue(self.clients[0]._api_client._httpx_client.is_closed)

    async def test_two_request_tool_loop_replays_signed_parts_after_serialization(self):
        parts = [
            {"text": "Check experiment.", "thought": True},
            SIGNED,
            {"functionCall": {"name": "measure", "args": {"trial": 2}, "id": "provider-call"}},
        ]
        with self.transport([response(parts), response([{"text": "Trial 2 improved by 0.2."}])]):
            first = await self.model.invoke("Compare two trials.", tools=[TOOL], tool_choice="required")
            # Checkpoint round trip: the signature includes non-UTF8 bytes.
            saved = AssistantMessage.model_validate(json.loads(first.model_dump_json()))
            history = [
                UserMessage(content="Compare two trials."),
                saved,
                ToolMessage(tool_call_id=saved.tool_calls[0].id, content="0.4"),
                ToolMessage(tool_call_id=saved.tool_calls[1].id, content="0.6"),
            ]
            second = await self.model.invoke(history, tools=[TOOL])
        self.assertEqual(first.finish_reason, "tool_calls")
        self.assertNotEqual(first.tool_calls[0].id, first.tool_calls[1].id)
        self.assertEqual(second.content, "Trial 2 improved by 0.2.")
        self.assertEqual(self.body(1)["contents"][1]["parts"], parts)
        results = self.body(1)["contents"][2]["parts"]
        self.assertEqual(results[0]["functionResponse"], {"name": "measure", "response": {"result": "0.4"}})
        self.assertEqual(results[1]["functionResponse"]["id"], "provider-call")
        self.assertEqual(self.body()["toolConfig"]["functionCallingConfig"]["mode"], "ANY")
        self.assertEqual(
            self.body()["tools"][0]["functionDeclarations"][0]["parameters_json_schema"], TOOL["function"]["parameters"]
        )
        self.assertEqual(len(self.requests), 2)  # The SDK must not execute the tools.

    async def test_stream_merge_preserves_late_signature_and_parallel_calls(self):
        events = [
            response([{"text": "Analyzing", "thought": True}], finish=None, usage=False),
            response([{"text": "Result: "}], finish=None, usage=False),
            response([SIGNED, {"functionCall": {"name": "measure", "args": {"trial": 2}}}], finish=None, usage=False),
            response([{"text": "", "thoughtSignature": "bGF0ZQ=="}]),
        ]
        wire = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
        reply = httpx.Response(200, headers={"content-type": "text/event-stream"}, content=wire)
        with self.transport([reply, response([{"text": "Research complete."}])]):
            chunks = [chunk async for chunk in self.model.stream("Run ablation.", tools=[TOOL])]
            merged = chunks[0]
            for chunk in chunks[1:]:
                merged = merged + chunk
            saved = AssistantMessage.model_validate(json.loads(merged.model_dump_json()))
            await self.model.invoke(
                [
                    UserMessage(content="Run ablation."),
                    saved,
                    *[ToolMessage(tool_call_id=call.id, content="0.6") for call in saved.tool_calls],
                ],
                tools=[TOOL],
            )
        self.assertEqual(self.requests[0].url.path, "/v1beta/models/" + MODEL + ":streamGenerateContent")
        self.assertEqual(self.requests[0].url.params["alt"], "sse")
        self.assertEqual(merged.content, "Result: ")
        self.assertEqual(merged.reasoning_content, "Analyzing")
        self.assertEqual(len(merged.tool_calls), 2)
        self.assertEqual(merged.finish_reason, "tool_calls")
        self.assertEqual(merged.usage_metadata.total_tokens, 17)
        self.assertEqual(
            self.body(1)["contents"][1]["parts"],
            [part for event in events for part in event["candidates"][0]["content"]["parts"]],
        )

    async def test_overrides_toolinfo_and_finish_reason(self):
        info = ToolInfo(name="measure", description="Read result", parameters=TOOL["function"]["parameters"])
        with self.transport([response([{"text": "Truncated"}], finish="MAX_TOKENS")]):
            result = await self.model.invoke(
                "Report.",
                tools=[info],
                model="gemini-fixture",
                max_tokens=42,
                temperature=0.2,
                top_p=0.7,
                stop="STOP",
                tool_choice="none",
            )
        config = self.body()["generationConfig"]
        self.assertEqual(config["maxOutputTokens"], 42)
        self.assertEqual(config["temperature"], 0.2)
        self.assertEqual(config["stopSequences"], ["STOP"])
        self.assertEqual(result.finish_reason, "length")
        self.assertEqual(result.provider_metadata, {"finish_reason": "MAX_TOKENS"})
        self.assertEqual(self.body()["toolConfig"]["functionCallingConfig"]["mode"], "NONE")

    async def test_no_usage_and_blocked_prompt_are_not_success(self):
        blocked = {"promptFeedback": {"blockReason": "SAFETY"}}
        with self.transport([response([{"text": "Text"}], usage=False), blocked]):
            first = await self.model.invoke("Report.")
            second = await self.model.invoke("Blocked fixture.")
        self.assertIsNone(first.usage_metadata)
        self.assertEqual(second.finish_reason, "SAFETY")
        self.assertEqual(second.content, "")

    async def test_provider_error_is_not_retried_and_clients_close(self):
        error = httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "fixture"}})
        with self.transport([error]):
            with self.assertRaises(BaseError) as raised:
                await self.model.invoke("Report.")
        self.assertIsInstance(raised.exception.__cause__, genai.errors.APIError)
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(self.clients[0]._api_client._async_httpx_client.is_closed)

    async def test_cancelled_stream_closes_transport(self):
        event = response([{"text": "First"}], finish=None, usage=False)
        reply = httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content="data: " + json.dumps(event) + "\n\n"
        )
        with self.transport([reply]):
            stream = self.client.stream("Report.")
            self.assertEqual((await anext(stream)).content, "First")
            await stream.aclose()
        self.assertTrue(self.clients[0]._api_client._async_httpx_client.is_closed)

    async def test_native_research_agent_executes_tool_and_records_usage(self):
        from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
        from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
        from openjiuwen.core.single_agent.rail.base import AgentRail
        from openjiuwen.core.single_agent.schema.agent_card import AgentCard

        class UsageRail(AgentRail):
            def __init__(self, sink):
                super().__init__()
                self.sink = sink

            async def after_model_call(self, ctx):
                self.sink({"usage": ctx.inputs.response.usage_metadata.model_dump()})

        measured = []
        usage: list[dict[str, Any]] = []

        def measure(trial):
            measured.append(trial)
            return {"accuracy": 0.6}

        agent = ReActAgent(AgentCard(id="google-research-fixture", name="Research", description="Ablation smoke"))
        agent.configure(
            ReActAgentConfig(
                model_name=MODEL,
                model_provider="Google",
                api_key="fixture-key",
                api_base=HOST,
                max_iterations=3,
                model_client_config=self.model.model_client_config,
                model_config_obj=self.model.model_config,
            )
        )
        tool = LocalFunction(
            ToolCard(
                id="measure",
                name="measure",
                description="Read experiment result",
                input_params=TOOL["function"]["parameters"],
            ),
            measure,
        )
        agent.ability_manager.add_ability(tool.card, tool)
        self.addCleanup(agent.ability_manager.remove_ability, tool.card.name)
        await agent.register_rail(UsageRail(usage.append))
        with self.transport([response([SIGNED]), response([{"text": "The observed accuracy is 0.6."}])]):
            result = await agent.invoke(
                {
                    "query": "Measure trial 1 and report the observed result.",
                    "conversation_id": "google-research-fixture",
                }
            )
        self.assertEqual(result["result_type"], "answer")
        self.assertEqual(result["output"], "The observed accuracy is 0.6.")
        self.assertEqual(measured, [1])
        self.assertEqual([entry["usage"]["total_tokens"] for entry in usage], [17, 17])
        replay = next(content for content in self.body(1)["contents"] if content["role"] == "model")
        self.assertEqual(replay["parts"], [SIGNED])

    def test_dict_history_plain_tool_calls_and_text_blocks(self):
        contents, system = _contents(
            [
                {"role": "system", "content": ["Write carefully."]},
                {"role": "user", "content": [{"type": "text", "text": "Report."}]},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "call", "type": "function", "function": {"name": "measure", "arguments": '{"trial": 1}'}}
                    ],
                },
                {"role": "tool", "tool_call_id": "call", "content": "0.6"},
            ]
        )
        self.assertEqual(system[0].text, "Write carefully.")
        self.assertEqual(contents[1].parts[0].function_call.args, {"trial": 1})
        self.assertEqual(contents[2].parts[0].function_response.name, "measure")
        with self.assertRaisesRegex(BaseError, "matching preceding"):
            _contents([ToolMessage(tool_call_id="missing", content="0.6")])
        with self.assertRaisesRegex(BaseError, "only text"):
            _contents([{"role": "user", "content": [{"type": "image_url"}]}])


if __name__ == "__main__":
    unittest.main()
