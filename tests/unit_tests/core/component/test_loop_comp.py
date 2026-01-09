from typing import AsyncIterator

import pytest

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.workflow import Input, Output
from openjiuwen.core.workflow import End
from openjiuwen.core.workflow import LoopGroup, LoopComponent
from openjiuwen.core.workflow import SetVariableComponent
from openjiuwen.core.workflow import Start
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.session import Session
from openjiuwen.core.session import WorkflowSession
from openjiuwen.core.session.stream import BaseStreamMode
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import WorkflowComponent
from tests.unit_tests.core.workflow.mock_nodes import AddTenNode

pytestmark = pytest.mark.asyncio


async def test_loop_number_exceeds_max_limit():
    flow = Workflow()
    flow.set_start_comp("start", Start(), inputs_schema={"input_num": "${num}"})
    flow.set_end_comp("end", End(), inputs_schema={"end_out": "${loop}"})

    loop_group = LoopGroup()
    loop_group.add_workflow_comp("loop_1", AddTenNode("loop_1"), inputs_schema={"source": "${loop.index}"})
    loop_group.start_nodes(["loop_1"])
    loop_group.end_nodes(["loop_1"])

    loop_component = LoopComponent(loop_group, output_schema={"l_out": "${loop_1.result}"})
    flow.add_workflow_comp("loop", loop_component,
                           inputs_schema={"loop_type": "number", "loop_number": 1001})

    flow.add_connection("start", "loop")
    flow.add_connection("loop", "end")

    with pytest.raises(JiuWenBaseException) as exc_info:
        await flow.invoke(inputs={"num": 0}, session=WorkflowSession())

    assert exc_info.value.error_code == StatusCode.COMPONENT_EXECUTION_RUNTIME_ERROR.code
    assert "exceeds maximum limit" in exc_info.value.message

class CustomStream(WorkflowComponent):
    def __init__(self):
        super().__init__()

    # async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
    #     await session.write_stream(OutputSchema(type='第一条流式消息', index = 0, payload="output_stream"))
    #     await session.write_stream(OutputSchema(type='第二条流式消息', index = 1, payload="output_stream"))
    #     return {'custom_output': inputs}

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        print(f"11111 line 32 custom stream")
        if inputs is None:
            yield 1
        else:
            value = inputs.get("value")
            # Handle both iterable and single integer values
            if isinstance(value, int):
                print(f"11111 line 37 custom stream index: {value}")
                yield {"value": "stream_{}".format(value)}
            else:
                for index in value:
                    print(f"11111 line 39 custom stream index: {index}")
                    yield {"value": "stream_{}".format(index)}

    async def collect(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        print(f"33333 line 42 custom collect")
        total_result = ""
        values = inputs.get("value")
        # Handle single value vs iterable
        if hasattr(values, '__aiter__'):
            async for item in values:
                print(f"33333 line 45 custom collect item: {item}")
                total_result = total_result + str(item) + ";"
        else:
            print(f"33333 line 47 custom collect single item: {values}")
            total_result = str(values)
        return {"value": total_result}

    async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        print("22222 line 49 custom transform")
        values = inputs.get("value")
        # Handle both iterable and single value inputs
        if hasattr(values, '__aiter__'):
            async for item in values:
                print(f"22222 line 52 custom transform item: {item}")
                yield {"value": "transform_{}".format(item)}
        else:
            yield {"value": "transform_{}".format(values)}

async def test_loop_number():
    flow = Workflow()
    flow.set_start_comp("start", Start(), inputs_schema={"input_arr": "${array}", "input_num": "${num}"})
    flow.set_end_comp("end", End(), inputs_schema={"end_out": "${loop}"})

    loop_group = LoopGroup()
    loop_group.add_workflow_comp("loop_1", AddTenNode("loop_1"), inputs_schema={"source": "${loop.index}"})
    loop_group.add_workflow_comp("loop_2", AddTenNode("loop_2"), inputs_schema={"source": "${loop.user_num}"})

    set_variable_component = SetVariableComponent({"${loop.user_num}": "${loop_2.result}"})

    loop_group.add_workflow_comp("loop_3", set_variable_component)
    loop_group.start_nodes(["loop_1"])
    loop_group.end_nodes(["loop_3"])
    loop_group.add_connection("loop_1", "loop_2")
    loop_group.add_connection("loop_2", "loop_3")

    loop_component = LoopComponent(loop_group,
                                   output_schema={"l_out1": "${loop_1.result}", "l_out2": "${loop_2.result}"})

    flow.add_workflow_comp("loop", loop_component, inputs_schema={"loop_type": "number", "loop_number": 12,
                                                                  "intermediate_var": {
                                                                      "user_num": "${start.input_num}"}})

    flow.add_connection("start", "loop")
    flow.add_connection("loop", "end")

    inputs = {"array": [4, 5, 6], "num": -3}

    results = await flow.invoke(inputs, session=WorkflowSession())
    assert results.result == {'output': {
        'end_out': {'user_num': 117, 'index': 0, 'l_out1': [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21],
                    'l_out2': [7, 17, 27, 37, 47, 57, 67, 77, 87, 97, 107, 117]}}}


async def test_loop_group_component_stream():
    """
    Test loop component's internal streaming capabilities between components.
    This test verifies that components within a loop can communicate via streaming.
    """
    # Create a loop group with streaming-capable components
    loop_group = LoopGroup()
    
    # Create producer component that generates stream data
    # Pass loop index directly as input value
    loop_group.add_workflow_comp("producer", CustomStream(), inputs_schema={"value": "${loop.index}"})
    
    # Create transformer component that processes stream data from producer
    loop_group.add_workflow_comp("transformer", CustomStream(), stream_inputs_schema={"value": "${producer.value}"})
    
    # Create consumer component that collects stream data from transformer
    loop_group.add_workflow_comp("consumer", CustomStream(), stream_inputs_schema={"value": "${transformer.value}"})
    
    # Set loop start and end nodes
    loop_group.start_nodes(["producer"])
    loop_group.end_nodes(["consumer"])
    
    # Add connections within the loop
    loop_group.add_stream_connection("producer", "transformer")
    loop_group.add_stream_connection("transformer", "consumer")

    # Create main workflow
    flow = Workflow()
    flow.set_start_comp("start", Start(), inputs_schema={})
    
    # Create end component with proper output schema
    end = End()
    flow.set_end_comp("end", end,
                     inputs_schema={"result": "${loop}"},
                     response_mode="streaming")
    
    # Create loop component with output schema
    loop_component = LoopComponent(loop_group, 
                                 output_schema={"final_result": "${consumer.value}",
                                              "loop_iteration": "${loop.index}"})
    flow.add_workflow_comp("loop", loop_component, 
                          inputs_schema={"loop_type": "number",
                                       "loop_number": 2})
    
    # Connect main workflow
    flow.add_connection("start", "loop")
    flow.add_connection("loop", "end")

    # Collect streaming outputs
    collected_chunks = []
    stream_count = 0
    
    # Test streaming execution
    async for chunk in flow.stream(inputs={}, 
                                  session=WorkflowSession(),
                                  stream_modes=[BaseStreamMode.OUTPUT]):
        assert chunk is not None
        print(f"Stream chunk {stream_count}: {chunk}")
        collected_chunks.append(chunk)
        stream_count += 1
        
        # Verify chunk structure
        assert hasattr(chunk, 'type')
        assert hasattr(chunk, 'payload')
        
    # Verify that we received streaming outputs from the loop execution
    assert len(collected_chunks) > 0, "Should receive at least one streaming output"
    
    # Verify streaming outputs contain loop iteration results
    loop_results_found = False
    for chunk in collected_chunks:
        # Check if this is a loop iteration result
        if hasattr(chunk, 'payload') and chunk.payload:
            payload_str = str(chunk.payload)
            if "stream_" in payload_str or "transform_" in payload_str or "custom_stream" in payload_str:
                loop_results_found = True
                break
    
    assert loop_results_found, "Should receive streaming results from loop iterations"
    
    print(f"Test completed successfully. Received {len(collected_chunks)} streaming chunks.")