#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import pytest

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner.drunner.dmessage_queue.message import DmqResponseMessage, DmqRequestMessage
from openjiuwen.core.runner.drunner.dmessage_queue.message_serializer import serialize_message, deserialize_message
from openjiuwen.core.session.interaction.interaction import InteractionOutput
from openjiuwen.core.session.stream.base import CustomSchema, OutputSchema, TraceSchema
from openjiuwen.core.workflow import WorkflowOutput, WorkflowExecutionState


class TestMessageSerializer:

    @staticmethod
    def test_output_schema():
        payload = OutputSchema(
            type=INTERACTION,
            index=0,
            payload=InteractionOutput(id="l.2", value="Please enter any key"),
        )
        msg = DmqResponseMessage(payload=payload)

        b = serialize_message(msg)
        logger.info(f"b:{b}")
        msg2 = deserialize_message(b)
        logger.info(f"msg2:{msg2}")
        assert isinstance(msg2.payload, OutputSchema)
        assert isinstance(msg2.payload.payload, InteractionOutput)

    @staticmethod
    def test_list_of_output_schema():
        payload = [
            # Interrupt
            OutputSchema(type=INTERACTION, index=0,
                         payload=InteractionOutput(id="questioner", value="信息")),
            # Regular dict
            OutputSchema(
                type="answer",
                index=0,
                payload={"output": "123", "result_type": "answer"}
            ),
            OutputSchema(
                type="workflow_final",
                index=0,
                payload={
                    "error": True,
                    "message": "aaa",
                    "status": "failed"}),
            # workflowoutput
            OutputSchema(
                type="workflow_final",
                index=0,
                payload=WorkflowOutput(
                    result={'responseContent': '上海', 'output': {}},
                    state=WorkflowExecutionState.COMPLETED
                ))
        ]
        msg = DmqResponseMessage(payload=payload)

        data = serialize_message(msg)
        msg2 = deserialize_message(data)

        # Add these assertions after deserializing the message
        assert isinstance(msg2.payload, list)
        assert len(msg2.payload) == 4

        # First item - OutputSchema with InteractionOutput (interrupt)
        assert isinstance(msg2.payload[0], OutputSchema)
        assert msg2.payload[0].type == INTERACTION
        assert isinstance(msg2.payload[0].payload, InteractionOutput)
        assert msg2.payload[0].payload.id == "questioner"
        assert msg2.payload[0].payload.value == "信息"

        # Second item - OutputSchema with plain dict payload
        assert isinstance(msg2.payload[1], OutputSchema)
        assert msg2.payload[1].type == "answer"
        assert isinstance(msg2.payload[1].payload, dict)
        assert msg2.payload[1].payload["output"] == "123"
        assert msg2.payload[1].payload["result_type"] == "answer"

        # Third item - OutputSchema with error dict
        assert isinstance(msg2.payload[2], OutputSchema)
        assert msg2.payload[2].type == "workflow_final"
        assert isinstance(msg2.payload[2].payload, dict)
        assert msg2.payload[2].payload["error"] == True
        assert msg2.payload[2].payload["message"] == "aaa"
        assert msg2.payload[2].payload["status"] == "failed"

        # Fourth item - OutputSchema with WorkflowOutput
        assert isinstance(msg2.payload[3], OutputSchema)
        assert msg2.payload[3].type == "workflow_final"
        assert isinstance(msg2.payload[3].payload, WorkflowOutput)
        assert msg2.payload[3].payload.result == {'responseContent': '上海', 'output': {}}
        assert msg2.payload[3].payload.state == WorkflowExecutionState.COMPLETED

    @staticmethod
    def test_customschema_with_workflow_output():
        payload = CustomSchema(
            output=[["aaa"]],
            result_type='answer',
            aaa='{"aaa":"123"}',
            work=WorkflowOutput(
                result={'responseContent': '上海', 'output': {}},
                state=WorkflowExecutionState.COMPLETED
            ),
        )
        msg = DmqResponseMessage(payload=payload)

        data = serialize_message(msg)
        msg2 = deserialize_message(data)

        assert isinstance(msg2.payload, CustomSchema)
        assert isinstance(msg2.payload.output, list)
        assert isinstance(msg2.payload.output[0], list)
        assert isinstance(msg2.payload.work, WorkflowOutput)
        assert msg2.payload.work.result == {'responseContent': '上海', 'output': {}}
        assert msg2.payload.work.state == WorkflowExecutionState.COMPLETED

    @staticmethod
    def test_plain_dict_in_request():
        payload = {"query": "你好"}
        msg = DmqRequestMessage(payload=payload)

        data = serialize_message(msg)
        msg2 = deserialize_message(data)

        assert isinstance(msg2, DmqRequestMessage)
        assert isinstance(msg2.payload, dict)
        assert msg2.payload["query"] == "你好"

    @staticmethod
    def test_dict_response():
        """Test plain dict in DmqResponseMessage"""
        msg = DmqResponseMessage(payload={'output': '你好！', 'result_type': 'answer'})

        data = serialize_message(msg)
        msg2 = deserialize_message(data)

        assert isinstance(msg2.payload, dict)
        assert msg2.payload['output'] == '你好！'

    @staticmethod
    def test_dict_with_embedded_basemodel():
        payload = {
            "output": WorkflowOutput(
                result={'responseContent': '上海', 'output': {}},
                state=WorkflowExecutionState.COMPLETED
            ),
            "result_type": "answer"
        }
        msg = DmqResponseMessage(payload=payload)

        data = serialize_message(msg)
        msg2 = deserialize_message(data)

        assert isinstance(msg2.payload, dict)
        assert isinstance(msg2.payload["output"], WorkflowOutput)

    @staticmethod
    def test_workflow_output():
        msg = DmqResponseMessage(payload=WorkflowOutput(
            result={'responseContent': '上海', 'output': {}},
            state=WorkflowExecutionState.COMPLETED
        ))

        data = serialize_message(msg)
        logger.info(f"data:{data}")
        msg2 = deserialize_message(data)
        logger.info(f"deserialize_message:{msg2}")

        assert isinstance(msg2.payload, WorkflowOutput)
        assert msg2.payload.result == {'responseContent': '上海', 'output': {}}
        assert msg2.payload.state == WorkflowExecutionState.COMPLETED

    @staticmethod
    def test_output_schema_in_workflow_output():
        list_output_schema = [
            # Interrupt
            OutputSchema(type=INTERACTION, index=0,
                         payload=InteractionOutput(id="questioner", value="信息")),
            # Regular dict
            OutputSchema(
                type="answer",
                index=0,
                payload={"output": "123", "result_type": "answer"}
            ),
            # workflowoutput with nested OutputSchema
            OutputSchema(
                type="workflow_final",
                index=0,
                payload=WorkflowOutput(
                    result=OutputSchema(
                        type="answer",
                        index=0,
                        payload={"output": "456", "result_type": "answer"}
                    ),
                    state=WorkflowExecutionState.COMPLETED
                ))
        ]
        msg = DmqResponseMessage(payload=WorkflowOutput(
            result=list_output_schema,
            state=WorkflowExecutionState.COMPLETED
        ))

        data = serialize_message(msg)
        msg2 = deserialize_message(data)

        # Add these assertions after deserializing the message
        assert isinstance(msg2.payload, WorkflowOutput)
        assert isinstance(msg2.payload.result, list)
        assert len(msg2.payload.result) == 3
        assert msg2.payload.state == WorkflowExecutionState.COMPLETED

        # First item - OutputSchema with InteractionOutput (interrupt)
        assert isinstance(msg2.payload.result[0], OutputSchema)
        assert msg2.payload.result[0].type == INTERACTION
        assert isinstance(msg2.payload.result[0].payload, InteractionOutput)
        assert msg2.payload.result[0].payload.id == "questioner"
        assert msg2.payload.result[0].payload.value == "信息"

        # Second item - OutputSchema with plain dict payload
        assert isinstance(msg2.payload.result[1], OutputSchema)
        assert msg2.payload.result[1].type == "answer"
        assert isinstance(msg2.payload.result[1].payload, dict)
        assert msg2.payload.result[1].payload["output"] == "123"
        assert msg2.payload.result[1].payload["result_type"] == "answer"

        # Third item - OutputSchema with WorkflowOutput containing nested OutputSchema
        assert isinstance(msg2.payload.result[2], OutputSchema)
        assert msg2.payload.result[2].type == "workflow_final"
        assert isinstance(msg2.payload.result[2].payload, WorkflowOutput)
        assert msg2.payload.result[2].payload.state == WorkflowExecutionState.COMPLETED
        # Check the nested OutputSchema within the WorkflowOutput
        assert isinstance(msg2.payload.result[2].payload.result, OutputSchema)
        assert msg2.payload.result[2].payload.result.type == "answer"
        assert isinstance(msg2.payload.result[2].payload.result.payload, dict)
        assert msg2.payload.result[2].payload.result.payload["output"] == "456"
        assert msg2.payload.result[2].payload.result.payload["result_type"] == "answer"

    @staticmethod
    def test_trace_schema_payload():
        import datetime

        payload = TraceSchema(
            type='tracer_agent',
            payload={
                'traceId': '94884432-1558-40d3-aded-b09e1111e171',
                'startTime': datetime.datetime(2025, 11, 18, 19, 22,
                                               5, 728961),
                'endTime': None,
                'inputs': {
                    'inputs': [
                        {
                            'role': 'user',
                            'content': '你好',
                            'name': None
                        }
                    ]
                },
                'outputs': None,
                'error': None,
                'invokeId': 'ab4f7f56-e69e-460d-8ba2-ed6e2e2f6bb0',
                'parentInvokeId': 'bcfd261a-39b5-4897-832d-37522d337b8a',
                'childInvokes': [],
                'invokeType': 'llm',
                'name': 'OpenAILLM',
                'elapsedTime': None,
                'metaData': {
                    'class_name': 'OpenAILLM',
                    'type': 'llm'
                }
            }
        )

        msg = DmqResponseMessage(payload=payload)

        data = serialize_message(msg)
        msg2 = deserialize_message(data)

        # Add these assertions after deserializing the message
        assert isinstance(msg2.payload, TraceSchema)
        assert msg2.payload.type == 'tracer_agent'
        assert isinstance(msg2.payload.payload, dict)
        assert msg2.payload.payload['traceId'] == '94884432-1558-40d3-aded-b09e1111e171'
        assert msg2.payload.payload['invokeType'] == 'llm'
        assert msg2.payload.payload['name'] == 'OpenAILLM'
        assert len(msg2.payload.payload['inputs']['inputs']) == 1
        assert msg2.payload.payload['inputs']['inputs'][0]['content'] == '你好'
        assert msg2.payload.payload['inputs']['inputs'][0]['role'] == 'user'

        # Time consistency checks
        expected_start_time = datetime.datetime(2025, 11, 18, 19,
                                                22, 5, 728961)
        assert msg2.payload.payload['startTime'] == expected_start_time
        assert msg2.payload.payload['endTime'] is None
        assert msg2.payload.payload['elapsedTime'] is None

        # Additional metadata checks
        assert msg2.payload.payload['metaData']['class_name'] == 'OpenAILLM'
        assert msg2.payload.payload['metaData']['type'] == 'llm'
        assert msg2.payload.payload['invokeId'] == 'ab4f7f56-e69e-460d-8ba2-ed6e2e2f6bb0'
        assert msg2.payload.payload['parentInvokeId'] == 'bcfd261a-39b5-4897-832d-37522d337b8a'
        assert msg2.payload.payload['childInvokes'] == []
        assert msg2.payload.payload['outputs'] is None
        assert msg2.payload.payload['error'] is None

    @staticmethod
    def test_serialize_exceed_max_depth():
        """
        Test serialization of nested structures exceeding maximum recursion depth (10 levels),
        ensuring RecursionError is thrown.
        """
        nested = OutputSchema(type="x", index=0, payload="final")

        for _ in range(11):  # Exceeding MAX_RECURSE_DEPTH = 10
            nested = WorkflowOutput(result=nested, state=WorkflowExecutionState.COMPLETED)
            nested = OutputSchema(type="wrap", index=0, payload=nested)

        msg = DmqResponseMessage(payload=WorkflowOutput(
            result=nested,
            state=WorkflowExecutionState.COMPLETED
        ))

        with pytest.raises(RecursionError):
            serialize_message(msg)

    @staticmethod
    def test_serialize_large_list_should_pass():
        """
        Test serialization of a list containing 20 elements, each being an OutputSchema,
        ensuring recursion depth limit is not triggered.
        """
        items = [
            OutputSchema(
                type="answer",
                index=i,
                payload={"value": i}
            )
            for i in range(20)  # list length exceeds 10
        ]

        msg = DmqResponseMessage(payload=WorkflowOutput(
            result=items,  # This is a list, won't trigger recursion depth limit
            state=WorkflowExecutionState.COMPLETED
        ))

        data = serialize_message(msg)
        msg2 = deserialize_message(data)

        assert isinstance(msg2.payload, WorkflowOutput)
        assert isinstance(msg2.payload.result, list)
        assert len(msg2.payload.result) == 20

        for i in range(20):
            item = msg2.payload.result[i]
            assert isinstance(item, OutputSchema)
            assert item.index == i
            assert item.payload["value"] == i
