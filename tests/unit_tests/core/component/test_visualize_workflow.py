import os
import textwrap
from typing import Literal
from unittest.mock import patch
import pytest

from openjiuwen.core.common.exception.exception import JiuWenBaseException
from openjiuwen.core.common.exception.status_code import StatusCode
from openjiuwen.core.workflow import BranchComponent
from openjiuwen.core.workflow import BranchRouter
from openjiuwen.core.workflow import NumberCondition
from openjiuwen.core.workflow import IntentDetectionCompConfig, IntentDetectionComponent
from openjiuwen.core.workflow import LLMCompConfig, LLMComponent
from openjiuwen.core.workflow.components.flow_related.loop.loop_callback.intermediate_loop_var import IntermediateLoopVarCallback
from openjiuwen.core.workflow.components.flow_related.loop.loop_callback.output import OutputCallback
from openjiuwen.core.workflow import LoopGroup, LoopComponent
from openjiuwen.core.workflow import SetVariableComponent
from openjiuwen.core.workflow import ToolComponent, ToolComponentConfig
from openjiuwen.core.workflow.components.flow_related.workflow_comp import SubWorkflowComponent
from openjiuwen.core.session import BaseSession
from openjiuwen.core.foundation.tool import RestfulApi, RestfulApiCard
from openjiuwen.core.workflow import Workflow
from openjiuwen.core.workflow import ComponentAbility
from openjiuwen.core.graph.visualization.drawable import Drawable
from openjiuwen.core.workflow.components.flow_related.loop.loop_comp import AdvancedLoopComponent
from openjiuwen.core.runner import Runner
from tests.unit_tests.core.workflow.mock_nodes import AddTenNode, MockEndNode, MockStartNode, CommonNode, \
    StreamCompNode, CollectCompNode, Node1


WORKFLOW_DRAWABLE = "WORKFLOW_DRAWABLE"


@patch.dict(os.environ, {WORKFLOW_DRAWABLE: "true"})
def test_visualize_simple_workflow():
    # flow: start -> a -> end
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"),
                        inputs_schema={
                            "a": "${a}",
                            "b": "${b}",
                            "c": 1,
                            "d": [1, 2, 3]})
    flow.add_workflow_comp("a", Node1("a"),
                           inputs_schema={
                               "aa": "${start.a}",
                               "ac": "${start.c}"})
    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={
                          "result": "${a.aa}"})
    flow.add_connection("start", "a")
    flow.add_connection("a", "end")
    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("start")
        \tnode_2["a"]
        \tnode_3("end")
        \tnode_1 --> node_2
        \tnode_2 --> node_3
        """).lstrip()
    assert flow.draw("jiuwen workflow") == mermaid_script


@patch.dict(os.environ, {WORKFLOW_DRAWABLE: "true"})
def test_visualize_simple_stream_workflow():
    # flow: start -> a ---> b -> end
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"), inputs_schema={"a": "${a}"})
    flow.add_workflow_comp("a", StreamCompNode("a"), inputs_schema={"value": "${start.a}"},
                           comp_ability=[ComponentAbility.STREAM], wait_for_all=True)
    flow.add_workflow_comp("b", CollectCompNode("b"), inputs_schema={"value": "${a.value}"},
                           stream_inputs_schema={"value": "${a.value}"}, comp_ability=[ComponentAbility.COLLECT],
                           wait_for_all=True)
    flow.set_end_comp("end", MockEndNode("end"), inputs_schema={"result1": "${b.value}"})
    flow.add_connection("start", "a")
    flow.add_stream_connection("a", "b")
    flow.add_connection("b", "end")
    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("start")
        \tnode_2["a"]
        \tnode_3["b"]
        \tnode_4("end")
        \tnode_1 --> node_2
        \tnode_2 ==> node_3
        \tnode_3 --> node_4
        """).lstrip()
    assert flow.draw("jiuwen workflow") == mermaid_script

@patch.dict(os.environ, {WORKFLOW_DRAWABLE: "true"})
def test_visualize_workflow_with_branch_comp():
    # flow: start -> sw[a,b] -> end
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"))
    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={"a": "${a.result}", "b": "${b.result}"})

    sw = BranchComponent()
    sw.add_branch("${a} <= 10", ["b"], "1")
    sw.add_branch("${a} > 10", ["a"], "2")

    flow.add_workflow_comp("sw", sw)

    flow.add_workflow_comp("a", CommonNode("a"),
                           inputs_schema={"result": "${a}"})

    flow.add_workflow_comp("b", AddTenNode("b"),
                           inputs_schema={"source": "${a}"})

    flow.add_connection("start", "sw")
    flow.add_connection("a", "end")
    flow.add_connection("b", "end")

    mermaid_script = textwrap.dedent("""
        ---
        title: 
        ---
        flowchart TB
        \tnode_1("start")
        \tnode_2("end")
        \tnode_3["sw"]
        \tnode_4["a"]
        \tnode_5["b"]
        \tnode_3 -.->|"${a} <= 10"| node_5
        \tnode_3 -.->|"${a} > 10"| node_4
        \tnode_1 --> node_3
        \tnode_4 --> node_2
        \tnode_5 --> node_2
        """).lstrip()
    assert flow.draw() == mermaid_script


@patch.dict(os.environ, {WORKFLOW_DRAWABLE: "true"})
def test_visualize_workflow_with_branch_router():
    """
    flow: start -> condition[a,b] -> end
    """
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"),
                        inputs_schema={"a": "${a}",
                                       "b": "${b}",
                                       "c": 1,
                                       "d": [1, 2, 3]})

    router = BranchRouter()
    router.add_branch("${start.a} is not None", "a")
    router.add_branch("${start.b} is not None", "b")

    flow.add_conditional_connection("start", router=router)
    flow.add_workflow_comp("a", Node1("a"), inputs_schema={"a": "${start.a}", "b": "${start.c}"})
    flow.add_workflow_comp("b", Node1("b"), inputs_schema={"b": "${start.b}"})
    flow.set_end_comp("end", MockEndNode("end"), {"result1": "${a.a}", "result2": "${b.b}"})
    flow.add_connection("a", "end")
    flow.add_connection("b", "end")
    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("start")
        \tnode_2["a"]
        \tnode_3["b"]
        \tnode_4("end")
        \tnode_1 -.->|"${start.a} is not None"| node_2
        \tnode_1 -.->|"${start.b} is not None"| node_3
        \tnode_2 --> node_4
        \tnode_3 --> node_4
        """).lstrip()
    assert flow.draw("jiuwen workflow") == mermaid_script


@patch.dict(os.environ, {WORKFLOW_DRAWABLE: "true"})
def test_visualize_workflow_with_condition():
    """
    start -> condition[a,b] -> end
    """
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"),
                        inputs_schema={"a": "${a}",
                                       "b": "${b}",
                                       "c": 1,
                                       "d": [1, 2, 3]})

    # Literal is for visualization
    def router(session: BaseSession) -> Literal["a", "b"]:
        val = session.state().get_global("start.a")
        if val is not None:
            return "a"
        val = session.state().get_global("start.b")
        if val is not None:
            return "b"
        return "a"

    flow.add_conditional_connection("start", router=router)
    flow.add_workflow_comp("a", Node1("a"), inputs_schema={"a": "${start.a}", "b": "${start.c}"})
    flow.add_workflow_comp("b", Node1("b"), inputs_schema={"b": "${start.b}"})
    flow.set_end_comp("end", MockEndNode("end"), {"result1": "${a.a}", "result2": "${b.b}"})
    flow.add_connection("a", "end")
    flow.add_connection("b", "end")

    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("start")
        \tnode_2["a"]
        \tnode_3["b"]
        \tnode_4("end")
        \tnode_1 -.-> node_2
        \tnode_1 -.-> node_3
        \tnode_2 --> node_4
        \tnode_3 --> node_4
        """).lstrip()
    assert flow.draw("jiuwen workflow") == mermaid_script


@patch.dict(os.environ, {WORKFLOW_DRAWABLE: "true"})
def test_visualize_sub_workflow():
    # flow: start -> a -> (sub_start -> sub_a -> sub_end) -> end
    sub_flow = Workflow()
    sub_flow.set_start_comp("sub_start", MockStartNode("start"),
                            inputs_schema={
                                "a": "${a}",
                                "b": "${b}",
                                "c": 1,
                                "d": [1, 2, 3]})
    sub_flow.add_workflow_comp("sub_a", Node1("a"),
                               inputs_schema={
                                   "aa": "${start.a}",
                                   "ac": "${start.c}"})
    sub_flow.set_end_comp("sub_end", MockEndNode("end"),
                          inputs_schema={
                              "result": "${a.aa}"})
    sub_flow.add_connection("sub_start", "sub_a")
    sub_flow.add_connection("sub_a", "sub_end")

    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"),
                        inputs_schema={
                            "a": "${a}",
                            "b": "${b}",
                            "c": 1,
                            "d": [1, 2, 3]})
    flow.add_workflow_comp("a", Node1("a"),
                           inputs_schema={
                               "aa": "${start.a}",
                               "ac": "${start.c}"})
    flow.add_workflow_comp("sub_flow", SubWorkflowComponent(sub_flow))
    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={
                          "result": "${a.aa}"})
    flow.add_connection("start", "a")
    flow.add_connection("a", "sub_flow")
    flow.add_connection("sub_flow", "end")

    # no expand sub graph
    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("start")
        \tnode_2["a"]
        \tnode_3["sub_flow"]
        \tnode_4("end")
        \tnode_1 --> node_2
        \tnode_2 --> node_3
        \tnode_3 --> node_4
        """).lstrip()
    assert flow.draw("jiuwen workflow") == mermaid_script

    # expand sub graph
    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("start")
        \tnode_2["a"]
        \tnode_7("end")
        \tsubgraph node_6 ["sub_flow"]
        \tdirection TB
        \tnode_3("sub_start")
        \tnode_4["sub_a"]
        \tnode_5("sub_end")
        end
        \tnode_1 --> node_2
        \tnode_2 --> node_3
        \tnode_5 --> node_7
        \tnode_3 --> node_4
        \tnode_4 --> node_5
        """).lstrip()
    assert flow.draw(title="jiuwen workflow", expand_subgraph=True) == mermaid_script


@patch.dict(os.environ, {WORKFLOW_DRAWABLE: "true"})
def test_visualize_multi_layer_sub_workflow():
    # flow: start -> a -> (sub_start -> sub_a -> (sub_sub_start -> sub_sub_a -> sub_sub_end) -> sub_end) -> end
    sub_sub_flow = Workflow()
    sub_sub_flow.set_start_comp("sub_sub_start", MockStartNode("start"),
                            inputs_schema={
                                "a": "${a}",
                                "b": "${b}",
                                "c": 1,
                                "d": [1, 2, 3]})
    sub_sub_flow.add_workflow_comp("sub_sub_a", Node1("a"),
                               inputs_schema={
                                   "aa": "${start.a}",
                                   "ac": "${start.c}"})
    sub_sub_flow.set_end_comp("sub_sub_end", MockEndNode("end"),
                          inputs_schema={
                              "result": "${a.aa}"})
    sub_sub_flow.add_connection("sub_sub_start", "sub_sub_a")
    sub_sub_flow.add_connection("sub_sub_a", "sub_sub_end")


    sub_flow = Workflow()
    sub_flow.set_start_comp("sub_start", MockStartNode("start"),
                            inputs_schema={
                                "a": "${a}",
                                "b": "${b}",
                                "c": 1,
                                "d": [1, 2, 3]})
    sub_flow.add_workflow_comp("sub_a", Node1("a"),
                               inputs_schema={
                                   "aa": "${start.a}",
                                   "ac": "${start.c}"})
    sub_flow.add_workflow_comp("sub_sub_flow", SubWorkflowComponent(sub_sub_flow))
    sub_flow.set_end_comp("sub_end", MockEndNode("end"),
                          inputs_schema={
                              "result": "${a.aa}"})
    sub_flow.add_connection("sub_start", "sub_a")
    sub_flow.add_connection("sub_a", "sub_sub_flow")
    sub_flow.add_connection("sub_sub_flow", "sub_end")

    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"),
                        inputs_schema={
                            "a": "${a}",
                            "b": "${b}",
                            "c": 1,
                            "d": [1, 2, 3]})
    flow.add_workflow_comp("a", Node1("a"),
                           inputs_schema={
                               "aa": "${start.a}",
                               "ac": "${start.c}"})
    flow.add_workflow_comp("sub_flow", SubWorkflowComponent(sub_flow))
    flow.set_end_comp("end", MockEndNode("end"),
                      inputs_schema={
                          "result": "${a.aa}"})
    flow.add_connection("start", "a")
    flow.add_connection("a", "sub_flow")
    flow.add_connection("sub_flow", "end")

    # no expand sub graph
    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("start")
        \tnode_2["a"]
        \tnode_3["sub_flow"]
        \tnode_4("end")
        \tnode_1 --> node_2
        \tnode_2 --> node_3
        \tnode_3 --> node_4
        """).lstrip()
    assert flow.draw("jiuwen workflow") == mermaid_script

    # expand first layer sub graph
    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("start")
        \tnode_2["a"]
        \tnode_8("end")
        \tsubgraph node_7 ["sub_flow"]
        \tdirection TB
        \tnode_3("sub_start")
        \tnode_4["sub_a"]
        \tnode_5["sub_sub_flow"]
        \tnode_6("sub_end")
        end
        \tnode_1 --> node_2
        \tnode_2 --> node_3
        \tnode_6 --> node_8
        \tnode_3 --> node_4
        \tnode_4 --> node_5
        \tnode_5 --> node_6
        """).lstrip()
    assert flow.draw(title="jiuwen workflow", expand_subgraph=1) == mermaid_script

    # expand second layer sub graph
    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("start")
        \tnode_2["a"]
        \tnode_11("end")
        \tsubgraph node_10 ["sub_flow"]
        \tdirection TB
        \tnode_3("sub_start")
        \tnode_4["sub_a"]
        \tnode_9("sub_end")
        \tsubgraph node_8 ["sub_sub_flow"]
        \tdirection TB
        \tnode_5("sub_sub_start")
        \tnode_6["sub_sub_a"]
        \tnode_7("sub_sub_end")
        end
        end
        \tnode_1 --> node_2
        \tnode_2 --> node_3
        \tnode_9 --> node_11
        \tnode_3 --> node_4
        \tnode_4 --> node_5
        \tnode_7 --> node_9
        \tnode_5 --> node_6
        \tnode_6 --> node_7
        """).lstrip()
    assert flow.draw(title="jiuwen workflow", expand_subgraph=2) == mermaid_script

    # expand all layer sub graph
    assert flow.draw(title="jiuwen workflow", expand_subgraph=True) == mermaid_script


@patch.dict(os.environ, {WORKFLOW_DRAWABLE: "true"})
def test_visualize_workflow_with_advanced_loop():
    flow = Workflow()
    flow.set_start_comp("s", MockStartNode("s"))
    flow.add_workflow_comp("a", CommonNode("a"))
    # create  loop: (1->2->3)
    loop_group = LoopGroup()
    loop_group.add_workflow_comp("1", AddTenNode("1"), inputs_schema={"source": "${l.index}"})
    loop_group.add_workflow_comp("2", AddTenNode("2"),
                                 inputs_schema={"source": "${l.intermediate_loop_var.user_var}"})
    set_variable_component = SetVariableComponent({"${l.intermediate_loop_var.user_var}": "${2.result}"})
    loop_group.add_workflow_comp("3", set_variable_component)
    loop_group.start_nodes(["1"])
    loop_group.end_nodes(["3"])
    loop_group.add_connection("1", "2")
    loop_group.add_connection("2", "3")
    output_callback = OutputCallback({"results": "${1.result}", "user_var": "${l.intermediate_loop_var.user_var}"})
    intermediate_callback = IntermediateLoopVarCallback({"user_var": "${input_number}"}, "intermediate_loop_var")
    loop = AdvancedLoopComponent(loop_group, NumberCondition("${loop_number}"),
                                 callbacks=[output_callback, intermediate_callback])
    flow.add_workflow_comp("l", loop, inputs_schema={"input_number": "${input_number}"})
    flow.add_workflow_comp("b", CommonNode("b"),
                           inputs_schema={"array_result": "${l.results}", "user_var": "${l.user_var}"})
    flow.set_end_comp("e", MockEndNode("e"),
                      inputs_schema={"array_result": "${b.array_result}", "user_var": "${b.user_var}"})
    # s->a->(1->2->3)->b->e
    flow.add_connection("s", "a")
    flow.add_connection("a", "l")
    flow.add_connection("l", "b")
    flow.add_connection("b", "e")

    # no expand loop
    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("s")
        \tnode_2["a"]
        \tnode_3["l"]
        \tnode_4["b"]
        \tnode_5("e")
        \tnode_3 -.-> node_3
        \tnode_1 --> node_2
        \tnode_2 --> node_3
        \tnode_3 -.-> node_4
        \tnode_4 --> node_5
        """).lstrip()
    assert flow.draw("jiuwen workflow") == mermaid_script

    # expand loop
    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("s")
        \tnode_2["a"]
        \tnode_7["b"]
        \tnode_8("e")
        \tsubgraph node_6 ["l"]
        \tdirection TB
        \tnode_3("1")
        \tnode_4["2"]
        \tnode_5("3")
        end
        \tnode_5 -.-> node_3
        \tnode_1 --> node_2
        \tnode_2 --> node_3
        \tnode_5 -.-> node_7
        \tnode_7 --> node_8
        \tnode_3 --> node_4
        \tnode_4 --> node_5
        """).lstrip()
    assert flow.draw("jiuwen workflow", expand_subgraph=True) == mermaid_script


@patch.dict(os.environ, {WORKFLOW_DRAWABLE: "true"})
def test_visualize_workflow_with_loop():
    flow = Workflow()
    flow.set_start_comp("s", MockStartNode("s"), inputs_schema={"a": "${input_number}"})
    flow.add_workflow_comp("a", CommonNode("a"),
                           inputs_schema={"array": "${input_array}"})

    # create  loop: (1->2->3)
    loop_group = LoopGroup()
    loop_group.add_workflow_comp("1", AddTenNode("1", {"check": "${s.a}"}),
                                 inputs_schema={"source": "${l.item}", "check": "${s.a}"})
    loop_group.add_workflow_comp("2", AddTenNode("2"), inputs_schema={"source": "${l.user_var}"})
    set_variable_component = SetVariableComponent({"${l.user_var}": "${2.result}"})
    loop_group.add_workflow_comp("3", set_variable_component)
    loop_group.add_workflow_comp("4", CommonNode("4"), inputs_schema={"index": "${l.index}"})
    loop_group.start_comp("1")
    loop_group.end_comp("4")
    loop_group.add_connection("1", "2")
    loop_group.add_connection("2", "3")
    loop_group.add_connection("3", "4")

    loop_component = LoopComponent(loop_group, {"results": "${1.result}", "user_var": "${l.user_var}",
                                                "index_collect": "${4.index}"})

    flow.add_workflow_comp("l", loop_component, inputs_schema={"loop_type": "array",
                                                               "loop_array": {"item": "${a.array}"},
                                                               "intermediate_var": {"user_var": "${s.a}"}})

    flow.add_workflow_comp("b", CommonNode("b"),
                           inputs_schema={"array_result": "${l.results}", "user_var": "${l.user_var}"})
    flow.set_end_comp("e", MockEndNode("e"),
                      inputs_schema={"array_result": "${b.array_result}", "user_var": "${b.user_var}",
                                     "index": "${l.index_collect}"})

    # s->a->(1->2->3)->b->e
    flow.add_connection("s", "a")
    flow.add_connection("a", "l")
    flow.add_connection("l", "b")
    flow.add_connection("b", "e")

    # no expand loop
    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("s")
        \tnode_2["a"]
        \tnode_3["l"]
        \tnode_4["b"]
        \tnode_5("e")
        \tnode_3 -.-> node_3
        \tnode_1 --> node_2
        \tnode_2 --> node_3
        \tnode_3 -.-> node_4
        \tnode_4 --> node_5
        """).lstrip()
    assert flow.draw("jiuwen workflow") == mermaid_script

    # expand loop
    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("s")
        \tnode_2["a"]
        \tnode_8["b"]
        \tnode_9("e")
        \tsubgraph node_7 ["l"]
        \tdirection TB
        \tnode_3("1")
        \tnode_4["2"]
        \tnode_5["3"]
        \tnode_6("4")
        end
        \tnode_6 -.-> node_3
        \tnode_1 --> node_2
        \tnode_2 --> node_3
        \tnode_6 -.-> node_8
        \tnode_8 --> node_9
        \tnode_3 --> node_4
        \tnode_4 --> node_5
        \tnode_5 --> node_6
        """).lstrip()
    assert flow.draw("jiuwen workflow", expand_subgraph=True) == mermaid_script


@patch.dict(os.environ, {WORKFLOW_DRAWABLE: "true"})
def test_visualize_workflow_with_loop_unset_end_nodes():
    flow = Workflow()
    flow.set_start_comp("s", MockStartNode("s"), inputs_schema={"a": "${input_number}"})
    flow.add_workflow_comp("a", CommonNode("a"),
                           inputs_schema={"array": "${input_array}"})

    # create  loop: (1->2->3)
    loop_group = LoopGroup()
    loop_group.add_workflow_comp("1", AddTenNode("1", {"check": "${s.a}"}),
                                 inputs_schema={"source": "${l.item}", "check": "${s.a}"})
    loop_group.add_workflow_comp("2", AddTenNode("2"), inputs_schema={"source": "${l.user_var}"})
    set_variable_component = SetVariableComponent({"${l.user_var}": "${2.result}"})
    loop_group.add_workflow_comp("3", set_variable_component)
    loop_group.add_workflow_comp("4", CommonNode("4"), inputs_schema={"index": "${l.index}"})
    loop_group.start_comp("1")
    loop_group.add_connection("1", "2")
    loop_group.add_connection("2", "3")
    loop_group.add_connection("3", "4")

    loop_component = LoopComponent(loop_group, {"results": "${1.result}", "user_var": "${l.user_var}",
                                                "index_collect": "${4.index}"})

    flow.add_workflow_comp("l", loop_component, inputs_schema={"loop_type": "array",
                                                               "loop_array": {"item": "${a.array}"},
                                                               "intermediate_var": {"user_var": "${s.a}"}})

    flow.add_workflow_comp("b", CommonNode("b"),
                           inputs_schema={"array_result": "${l.results}", "user_var": "${l.user_var}"})
    flow.set_end_comp("e", MockEndNode("e"),
                      inputs_schema={"array_result": "${b.array_result}", "user_var": "${b.user_var}",
                                     "index": "${l.index_collect}"})

    # s->a->(1->2->3)->b->e
    flow.add_connection("s", "a")
    flow.add_connection("a", "l")
    flow.add_connection("l", "b")
    flow.add_connection("b", "e")

    # no expand loop
    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("s")
        \tnode_2["a"]
        \tnode_3["l"]
        \tnode_4["b"]
        \tnode_5("e")
        \tnode_3 -.-> node_3
        \tnode_1 --> node_2
        \tnode_2 --> node_3
        \tnode_3 -.-> node_4
        \tnode_4 --> node_5
        """).lstrip()
    assert flow.draw("jiuwen workflow") == mermaid_script

    # expand loop
    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("s")
        \tnode_2["a"]
        \tnode_8["b"]
        \tnode_9("e")
        \tsubgraph node_7 ["l"]
        \tdirection TB
        \tnode_3("1")
        \tnode_4["2"]
        \tnode_5["3"]
        \tnode_6("4")
        end
        \tnode_6 -.-> node_3
        \tnode_1 --> node_2
        \tnode_2 --> node_3
        \tnode_6 -.-> node_8
        \tnode_8 --> node_9
        \tnode_3 --> node_4
        \tnode_4 --> node_5
        \tnode_5 --> node_6
        """).lstrip()
    assert flow.draw("jiuwen workflow", expand_subgraph=True) == mermaid_script


def test_drawable_exception():
    drawable = Drawable()
    # set start node failed
    node_id = "start"
    with pytest.raises(JiuWenBaseException) as cm:
        drawable.set_start_node(node_id)
    assert cm.value.error_code == StatusCode.DRAWABLE_GRAPH_SET_START_NODE_FAILED.code
    assert cm.value.message == StatusCode.DRAWABLE_GRAPH_SET_START_NODE_FAILED.errmsg.format(
        node_id=node_id)

    # set end node failed
    node_id = "end"
    with pytest.raises(JiuWenBaseException) as cm:
        drawable.set_end_node(node_id)
    assert cm.value.error_code == StatusCode.DRAWABLE_GRAPH_SET_END_NODE_FAILED.code
    assert cm.value.message == StatusCode.DRAWABLE_GRAPH_SET_END_NODE_FAILED.errmsg.format(
        node_id=node_id)

    # set end node failed
    node_id = "break"
    with pytest.raises(JiuWenBaseException) as cm:
        drawable.set_break_node(node_id)
    assert cm.value.error_code == StatusCode.DRAWABLE_GRAPH_SET_BREAK_NODE_FAILED.code
    assert cm.value.message == StatusCode.DRAWABLE_GRAPH_SET_BREAK_NODE_FAILED.errmsg.format(
        node_id=node_id)

    # to mermaid failed, title is not str
    invalid_titles = [-1, {}, {"a": "b"}, [], [1, 2]]
    for invalid_title in invalid_titles:
        with pytest.raises(JiuWenBaseException) as cm:
            drawable.to_mermaid(title=invalid_title)
        assert cm.value.error_code == StatusCode.DRAWABLE_GRAPH_INVALID_TITLE.code
        assert cm.value.message == StatusCode.DRAWABLE_GRAPH_INVALID_TITLE.errmsg

    # to mermaid failed, expand_subgraph is not boolean or non-negative integer
    invalid_expand_subgraphs = [-1, "", "true", "xxx", {}, {"a": "b"}, [], [1, 2]]
    for invalid_expand_subgraph in invalid_expand_subgraphs:
        with pytest.raises(JiuWenBaseException) as cm:
            drawable.to_mermaid(expand_subgraph=invalid_expand_subgraph)
        assert cm.value.error_code == StatusCode.DRAWABLE_GRAPH_INVALID_EXPAND_SUBGRAPH.code
        assert cm.value.message == StatusCode.DRAWABLE_GRAPH_INVALID_EXPAND_SUBGRAPH.errmsg

    # to mermaid failed, enable_animation is not boolean
    invalid_enable_animations = ["", "true", "xxx", 1, 0, {}, {"a": "b"}, [], [1, 2]]
    for invalid_enable_animation in invalid_enable_animations:
        with pytest.raises(JiuWenBaseException) as cm:
            drawable.to_mermaid(expand_subgraph=1, enable_animation=invalid_enable_animation)
        assert cm.value.error_code == StatusCode.DRAWABLE_GRAPH_INVALID_ENABLE_ANIMATION.code
        assert cm.value.message == StatusCode.DRAWABLE_GRAPH_INVALID_ENABLE_ANIMATION.errmsg

    # to mermaid svg failed, expand_subgraph is non-negative integer
    for invalid_expand_subgraph in invalid_expand_subgraphs:
        with pytest.raises(JiuWenBaseException) as cm:
            drawable.to_mermaid_svg(expand_subgraph=invalid_expand_subgraph)
            assert cm.value.error_code == StatusCode.DRAWABLE_GRAPH_INVALID_EXPAND_SUBGRAPH.code
            assert cm.value.message == StatusCode.DRAWABLE_GRAPH_INVALID_EXPAND_SUBGRAPH.errmsg

    # to mermaid svg failed, title is not str
    for invalid_title in invalid_titles:
        with pytest.raises(JiuWenBaseException) as cm:
            drawable.to_mermaid_svg(title=invalid_title)
        assert cm.value.error_code == StatusCode.DRAWABLE_GRAPH_INVALID_TITLE.code
        assert cm.value.message == StatusCode.DRAWABLE_GRAPH_INVALID_TITLE.errmsg

    # to mermaid png failed, expand_subgraph is non-negative integer
    for invalid_expand_subgraph in invalid_expand_subgraphs:
        with pytest.raises(JiuWenBaseException) as cm:
            drawable.to_mermaid_png(expand_subgraph=invalid_expand_subgraph)
            assert cm.value.error_code == StatusCode.DRAWABLE_GRAPH_INVALID_EXPAND_SUBGRAPH.code
            assert cm.value.message == StatusCode.DRAWABLE_GRAPH_INVALID_EXPAND_SUBGRAPH.errmsg

    # to mermaid png failed, title is not str
    for invalid_title in invalid_titles:
        with pytest.raises(JiuWenBaseException) as cm:
            drawable.to_mermaid_png(title=invalid_title)
        assert cm.value.error_code == StatusCode.DRAWABLE_GRAPH_INVALID_TITLE.code
        assert cm.value.message == StatusCode.DRAWABLE_GRAPH_INVALID_TITLE.errmsg


@patch.dict(os.environ, {WORKFLOW_DRAWABLE: "true"})
def test_visualize_simple_stream_workflow_animation():
    # flow: start -> a ---> b -> end
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"), inputs_schema={"a": "${a}"})
    flow.add_workflow_comp("a", StreamCompNode("a"), inputs_schema={"value": "${start.a}"},
                           comp_ability=[ComponentAbility.STREAM], wait_for_all=True)
    flow.add_workflow_comp("b", CollectCompNode("b"), inputs_schema={"value": "${a.value}"},
                           stream_inputs_schema={"value": "${a.value}"}, comp_ability=[ComponentAbility.COLLECT],
                           wait_for_all=True)
    flow.set_end_comp("end", MockEndNode("end"), inputs_schema={"result1": "${b.value}"})
    flow.add_connection("start", "a")
    flow.add_stream_connection("a", "b")
    flow.add_connection("b", "end")
    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("start")
        \tnode_2["a"]
        \tnode_3["b"]
        \tnode_4("end")
        \tnode_1 --> node_2
        \tnode_2 link_1@==> node_3
        link_1@{animate: true}
        \tnode_3 --> node_4
        """).lstrip()
    assert flow.draw("jiuwen workflow", enable_animation=True) == mermaid_script


@patch.dict(os.environ, {WORKFLOW_DRAWABLE: "true"})
def test_visualize_simple_workflow_intent():
    # flow: start → intent → (分支路由)
    #                    ├─ llm → plugin → end (天气查询)
    #                    └─ end (其他意图)
    flow = Workflow()
    flow.set_start_comp("start", MockStartNode("start"), inputs_schema={"a": "${a}"})
    config = IntentDetectionCompConfig(
        user_prompt="请判断用户意图，识别是否为天气查询请求",
        category_name_list=["查询某地天气"]
    )

    intent = IntentDetectionComponent(config)
    # 分支配置：
    # classification_id == 1: 识别为"查询某地天气" → 走 llm 处理流程
    # classification_id == 0: 其他意图 → 走 end 默认回复
    intent.add_branch("${intent.classification_id} == 1", ["llm"], "天气查询分支")
    intent.add_branch("${intent.classification_id} == 0", ["end"], "默认分支")
    flow.add_workflow_comp(
        "intent",
        intent,
        inputs_schema={"query": "${start.query}"},
    )

    config = LLMCompConfig(
        template_content=[{"role": "user", "content": ""}],
        response_format={"type": "json"},
        output_config={
            "location": {"type": "string", "description": "地点（英文）", "required": True},
            "date": {"type": "string", "description": "日期（YYYY-MM-DD）", "required": True},
            "query": {"type": "string", "description": "改写后的query", "required": True}
        },
    )
    llm = LLMComponent(config)
    flow.add_workflow_comp(
        "llm",
        llm,
        inputs_schema={"query": "${start.query}"},
    )

    tool_config = ToolComponentConfig(tool_id="WeatherReporter")
    weather_tool = RestfulApi(
        card=RestfulApiCard(
            id="WeatherReporter",
            name="WeatherReporter",
            description="天气查询插件",
            input_params={
                "type": "object",
                "properties": {
                    "location": {"description": "地点", "type": "string"},
                    "date": {"description": "日期", "type": "string"},
                },
                "required": ["location", "date"],
            },
            url="http://127.0.0.1:9000/weather",
            headers={},
            method="GET",
        ),
    )
    Runner.resource_mgr.add_tool(weather_tool)
    plugin = ToolComponent(tool_config)
    flow.add_workflow_comp(
        "plugin",
        plugin,
        inputs_schema={
            "location": "${llm.location}",
            "date": "${llm.date}",
        },
    )
    flow.set_end_comp("end", MockEndNode("end"), inputs_schema={"output": "${plugin.data}"})

    flow.add_connection("start", "intent")
    # intent 通过分支自动路由到 llm 或 end
    flow.add_connection("llm", "plugin")
    flow.add_connection("plugin", "end")

    mermaid_script = textwrap.dedent("""
        ---
        title: jiuwen workflow
        ---
        flowchart TB
        \tnode_1("start")
        \tnode_2["intent"]
        \tnode_3["llm"]
        \tnode_4["plugin"]
        \tnode_5("end")
        \tnode_2 -.->|"${intent.classification_id} == 1"| node_3
        \tnode_2 -.->|"${intent.classification_id} == 0"| node_5
        \tnode_1 --> node_2
        \tnode_3 --> node_4
        \tnode_4 --> node_5
        """).lstrip()
    assert flow.draw("jiuwen workflow") == mermaid_script
