"""
openJiuwen Core quick-start example - Using IntelliRouter
Create a simple WorkflowAgent that calls a workflow to generate text via IntelliRouter
"""

import os
import sys
import asyncio
from typing import Dict, List, Any


asyncio.set_event_loop(asyncio.new_event_loop())

from openjiuwen.core.common.logging.log_config import log_config
from openjiuwen.core.common.logging.manager import LogManager
LogManager.initialize()

from openjiuwen.core.workflow import Start, End, LLMComponent, LLMCompConfig, generate_workflow_key
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig
from openjiuwen.core.application.workflow_agent import WorkflowAgent
from openjiuwen.core.workflow import Workflow, WorkflowCard

# ==================== IntelliRouter Config ====================

LLM_DEPLOYMENT_CONFIGS = {
     "deepseek-v4-flash": [
 	         {
 	             "url": "https://api.deepseek.com",
 	             "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
 	             "tpm": 100000,
 	             "rpm": 60,
 	             "tags": ["primary", "high-throughput"],
        },
    ],
}

# ==================== Create IntelliRouter Config ====================

intelli_router_deployments = []
deployment_counter = {}

for model_name, deployments in LLM_DEPLOYMENT_CONFIGS.items():
    deployment_counter[model_name] = 0
    for dep_config in deployments:
        deployment_counter[model_name] += 1
        deployment_id = f"{model_name}-dep{deployment_counter[model_name]}"

        intelli_router_deployments.append({
            "model_name": model_name,
            "api_key": dep_config["api_key"],
            "api_base": dep_config["url"],
            "id": deployment_id,
            "tpm": dep_config.get("tpm", 100000),
            "rpm": dep_config.get("rpm", 60),
            "tags": dep_config.get("tags", []),
            "timeout": dep_config.get("timeout", 30.0)
        })

model_client_config = ModelClientConfig(
    client_provider="intelli_router",
    api_key="placeholder",
    api_base="http://placeholder",
    verify_ssl=False,
    intelli_router_deployments=intelli_router_deployments,
    intelli_router_strategy="adaptive",
    intelli_router_num_retries=3,
    intelli_router_timeout=30.0,
    intelli_router_strategy_kwargs={
        "token_threshold": 1000,
        "rpm_threshold": 10,
        "exploration_ratio": 0.1,
        "w_health": 1.0,
        "w_token": 0.5,
        "w_rpm": 0.3,
        "w_latency": 0.2
    }
)

model_config = ModelRequestConfig(
    model="deepseek-v4-flash"
)

# ==================== Create Workflow Config ====================

workflow_card = WorkflowCard(
    id="generate_text_workflow",
    name="generate_text",
    version="1.0",
    description="Generate text from user input (via IntelliRouter)",
    input_params={
        "type": "object",
        "properties": {"query": {"type": "string", "description": "User input"}},
        "required": ['query']
    }
)

flow = Workflow(card=workflow_card)

start = Start()
end = End({"responseTemplate": "Workflow output text: {{output}}"})
llm_config = LLMCompConfig(
    model_client_config=model_client_config,
    model_config=model_config,
    template_content=[
        {"role": "system", "content": "You are an AI assistant helping me complete tasks.\nNote: Do not reason, just output the result directly!"},
        {"role": "user", "content": "{{query}}"}
    ],
    response_format={"type": "json"},
    output_config={
        "type": "object",
        "description": "LLM output schema",
        "properties": {
            "output": {
                "type": "string",
                "description": "LLM output"
            }
        },
        "required": ["output"]
    }
)
llm = LLMComponent(llm_config)

flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
flow.add_workflow_comp("llm", llm, inputs_schema={"query": "${start.query}"})
flow.set_end_comp("end", end, inputs_schema={"output": "${llm.output}"})
flow.add_connection("start", "llm")
flow.add_connection("llm", "end")

Runner.resource_mgr.add_workflow(
    WorkflowCard(id=generate_workflow_key(flow.card.id, flow.card.version)),
    lambda: flow,
)

agent_config = WorkflowAgentConfig(
    id="hello_agent_with_router",
    version="0.1.1",
    description="First Agent using IntelliRouter",
)
workflow_agent = WorkflowAgent(agent_config)
workflow_agent.add_workflows([flow])


async def main():
    print("=" * 60)
    print("Hello Agent with IntelliRouter Demo")
    print("=" * 60)
    print(f"\n[Config Info]")
    print(f"  Model: {model_config.model_name}")
    print(f"  Deployments: {len(intelli_router_deployments)}")
    print(f"  Routing Strategy: adaptive")
    print(f"  Endpoints:")
    for dep in intelli_router_deployments:
        print(f"    - {dep['id']}: {dep['api_base']}")

    print(f"\n[Executing]")

    for _ in range(2):
        try:
            invoke_result = await Runner.run_agent(
                workflow_agent,
                {"query": "Hello, tell me a joke in under 20 words"}
            )
            output_result = invoke_result.get("output").result
            print(f"\n[Result]")
            print(f"  WorkflowAgent output result >>> {output_result.get('response')}")

            if hasattr(workflow_agent, '_workflows') and workflow_agent._workflows:
                for wf in workflow_agent._workflows:
                    if hasattr(wf, '_components'):
                        for comp_name, comp in wf._components.items():
                            if hasattr(comp, 'llm_config'):
                                llm_comp = comp
                                if hasattr(llm_comp, 'model') and hasattr(llm_comp.model, '_client'):
                                    client = llm_comp.model._client
                                    if hasattr(client, '_router'):
                                        stats = client._router.get_stats()
                                        print(f"\n[IntelliRouter Stats]")
                                        print(f"  Total Deployments: {stats.get('total_deployments', 0)}")
                                        print(f"  Model List: {stats.get('model_list', [])}")

        except Exception as e:
            print(f"\n[Execution Failed] Error: {e}")
            import traceback
            traceback.print_exc()

async def demo_router_sharing():
    """
    Multi-Agent demo: two agents (Translator + Summarizer) share the same ReliableRouter.

    Architecture:
         Shared Router (ReliableRouter from model_client_config)
            ─┬──────────────────┬─
             │                  │
         Agent A            Agent B
        "translator"       "summarizer"
        Workflow A         Workflow B
        LLMComponent       LLMComponent
    """
    print("\n" + "=" * 60)
    print("Multi-Agent Router Sharing Demo")
    print("=" * 60)

    # ---------------------------------------------------------------
    # Workflow A: Translator
    # ---------------------------------------------------------------
    workflow_card_a = WorkflowCard(
        id="translate_workflow",
        name="translate",
        version="1.0",
        description="Translate user input to Chinese",
        input_params={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Text to translate"}},
            "required": ["query"],
        },
    )

    flow_a = Workflow(card=workflow_card_a)
    start_a = Start()
    end_a = End({"responseTemplate": "{{output}}"})
    llm_config_a = LLMCompConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        template_content=[
            {
                "role": "system",
                "content": (
                    "You are a translator. Translate the user's input to Chinese. "
                    "Only output the translation, nothing else."
                ),
            },
            {"role": "user", "content": "{{query}}"},
        ],
        response_format={"type": "text"},
        output_config={"output": {"type": "string", "description": "Translation result"}},
    )
    llm_a = LLMComponent(llm_config_a)

    flow_a.set_start_comp("start", start_a, inputs_schema={"query": "${query}"})
    flow_a.add_workflow_comp("llm", llm_a, inputs_schema={"query": "${start.query}"})
    flow_a.set_end_comp("end", end_a, inputs_schema={"output": "${llm.output}"})
    flow_a.add_connection("start", "llm")
    flow_a.add_connection("llm", "end")

    Runner.resource_mgr.add_workflow(
        WorkflowCard(id=generate_workflow_key(flow_a.card.id, flow_a.card.version)),
        lambda: flow_a,
    )

    agent_a = WorkflowAgent(WorkflowAgentConfig(
        id="translator_agent", version="0.1.0", description="Translation agent",
    ))
    agent_a.add_workflows([flow_a])

    # ---------------------------------------------------------------
    # Workflow B: Summarizer
    # ---------------------------------------------------------------
    workflow_card_b = WorkflowCard(
        id="summarize_workflow",
        name="summarize",
        version="1.0",
        description="Summarize user input",
        input_params={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Text to summarize"}},
            "required": ["query"],
        },
    )

    flow_b = Workflow(card=workflow_card_b)
    start_b = Start()
    end_b = End({"responseTemplate": "{{output}}"})
    llm_config_b = LLMCompConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        template_content=[
            {
                "role": "system",
                "content": (
                    "You are a summarizer. Summarize the user's input in under 30 words. "
                    "Only output the summary, nothing else."
                ),
            },
            {"role": "user", "content": "{{query}}"},
        ],
        response_format={"type": "text"},
        output_config={"output": {"type": "string", "description": "Summary result"}},
    )
    llm_b = LLMComponent(llm_config_b)

    flow_b.set_start_comp("start", start_b, inputs_schema={"query": "${query}"})
    flow_b.add_workflow_comp("llm", llm_b, inputs_schema={"query": "${start.query}"})
    flow_b.set_end_comp("end", end_b, inputs_schema={"output": "${llm.output}"})
    flow_b.add_connection("start", "llm")
    flow_b.add_connection("llm", "end")

    Runner.resource_mgr.add_workflow(
        WorkflowCard(id=generate_workflow_key(flow_b.card.id, flow_b.card.version)),
        lambda: flow_b,
    )

    agent_b = WorkflowAgent(WorkflowAgentConfig(
        id="summarizer_agent", version="0.1.0", description="Summarization agent",
    ))
    agent_b.add_workflows([flow_b])

    # ---------------------------------------------------------------
    # Execute agents and verify router sharing
    # ---------------------------------------------------------------
    def _get_router(agent):
        """Extract the ReliableRouter from a WorkflowAgent's internals (for demo only)."""
        for wf in (agent._workflows or []):
            for comp in (getattr(wf, '_components', None) or {}).values():
                model = getattr(comp, 'model', None) or getattr(comp, '_llm', None)
                if model and hasattr(model, '_client') and hasattr(model._client, '_router'):
                    return model._client._router
        return None

    print(f"\n[Running Agent A: translator_agent]")
    try:
        result_a = await Runner.run_agent(
            agent_a,
            {"query": "Hello, welcome to the world of artificial intelligence."},
        )
        output_result = result_a.get("output").result
        resp = output_result.get("response") if isinstance(output_result, dict) else str(output_result)
        print(f"  Translation: {resp}")
    except Exception as e:
        print(f"  Agent A failed: {e}")

    print(f"\n[Running Agent B: summarizer_agent]")
    try:
        result_b = await Runner.run_agent(
            agent_b,
            {"query": "Artificial intelligence is transforming industries by automating tasks, "
                      "improving decision-making, and enabling new capabilities."},
        )
        output_result = result_b.get("output").result
        print(f"  Output result: {output_result}")
        print(f"  Summary: {output_result.get('response')}")
    except Exception as e:
        print(f"  Agent B failed: {e}")

    # Router sharing verification
    router_a = _get_router(agent_a)
    router_b = _get_router(agent_b)

    print(f"\n[Router Sharing Verification]")
    print(f"  Agent A router id: {id(router_a)}")
    print(f"  Agent B router id: {id(router_b)}")

    if router_a is not None and router_a is router_b:
        print(f"  [v] Same router instance shared across two agents")
        print(f"  [v] Both agents get model info from the same ReliableRouter")
        print(f"  [v] Health/rate-limit state is shared across the team")
    elif router_a is None:
        print(f"  [?] Could not extract router from agents (internals may differ)")
    else:
        print(f"  [x] Different router instances (cache miss)")

    print("\n" + "=" * 60)
    print("Multi-Agent Router Sharing Demo Complete")
    print("=" * 60)


async def async_main():
    await main()
    await demo_router_sharing()


if __name__ == "__main__":
    asyncio.run(async_main())