"""
openJiuwen Core quick-start example - Using IntelliRouter

Demo scenarios:
  1. Unified routing — multi-provider, multi-model; Router auto-selects the best
     deployment (cross-model failover)
  2. Specific model — route only within a single model pool (backward compatible)
  3. Multi-Agent shared Router — multiple agents reuse the same ReliableRouter instance

Usage:
  export DEEPSEEK_API_KEY="sk-..."
  python examples/intelli_router/intelliRouter_demo.py

Optional: set environment variables to add more providers:
  export ZHIPU_API_KEY="..."               (optional, Zhipu AI)
  export OPENAI_API_KEY="sk-..."           (optional)
  export ANTHROPIC_API_KEY="sk-ant-..."    (optional)
"""

import os
import sys
import asyncio
from typing import Dict, List, Any


from openjiuwen.core.common.logging.log_config import log_config
from openjiuwen.core.common.logging.manager import LogManager
LogManager.initialize()

from openjiuwen.core.workflow import Start, End, LLMComponent, LLMCompConfig
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.legacy import WorkflowAgentConfig
from openjiuwen.core.application.workflow_agent import WorkflowAgent
from openjiuwen.core.workflow import Workflow, WorkflowCard

# ==================== Config ====================

DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY", "")
ZHIPU_KEY = os.getenv("ZHIPU_API_KEY", "")
OPENAI_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")

DEAD_BASE = "http://localhost:19999"  # simulated dead endpoint

if not DEEPSEEK_KEY:
    print("[!] 请设置 DEEPSEEK_API_KEY 环境变量")
    sys.exit(1)

# ==================== Deployment Config ====================
#
# Design notes:
#   - model="*" (or ModelRequestConfig without a model) triggers unified routing
#   - All deployments are equal peers; Router picks the best one
#   - When a deployment fails, Router automatically switches to another
#     (cross-model, cross-provider)
#

intelli_router_deployments = [
    # Dead endpoint (used to demonstrate failover)
    {
        "model_name": "deepseek-chat",
        "api_key": "fake-key-dead",
        "api_base": DEAD_BASE,
        "id": "dead-endpoint",
        "provider": "deepseek",
        "tpm": 100000,
        "rpm": 60,
        "tags": ["dead"],
        "timeout": 3.0,
    },
    # DeepSeek Chat (fast, low cost)
    {
        "model_name": "deepseek-chat",
        "api_key": DEEPSEEK_KEY,
        "api_base": "https://api.deepseek.com",
        "id": "deepseek-chat",
        "provider": "deepseek",
        "tpm": 100000,
        "rpm": 60,
        "tags": ["primary", "fast"],
        "timeout": 30.0,
    },
    # DeepSeek Reasoner (reasoning chain)
    {
        "model_name": "deepseek-reasoner",
        "api_key": DEEPSEEK_KEY,
        "api_base": "https://api.deepseek.com",
        "id": "deepseek-reasoner",
        "provider": "deepseek",
        "tpm": 100000,
        "rpm": 30,
        "tags": ["reasoning"],
        "timeout": 120.0,
    },
]

# Zhipu GLM-4
if ZHIPU_KEY:
    intelli_router_deployments.append({
        "model_name": "glm-4-flash",
        "api_key": ZHIPU_KEY,
        "api_base": "https://open.bigmodel.cn",
        "id": "zhipu-glm-4-flash",
        "provider": "zhipu",
        "tpm": 100000,
        "rpm": 60,
        "tags": ["zhipu", "fast"],
        "timeout": 30.0,
    })

# Optional: OpenAI
if OPENAI_KEY:
    intelli_router_deployments.append({
        "model_name": "gpt-4o-mini",
        "api_key": OPENAI_KEY,
        "api_base": "https://api.openai.com",
        "id": "openai-gpt4o-mini",
        "provider": "openai",
        "tpm": 100000,
        "rpm": 500,
        "tags": ["openai"],
        "timeout": 30.0,
    })

# Optional: Anthropic
if ANTHROPIC_KEY:
    intelli_router_deployments.append({
        "model_name": "claude-3-haiku-20240307",
        "api_key": ANTHROPIC_KEY,
        "api_base": "https://api.anthropic.com",
        "id": "anthropic-haiku",
        "provider": "anthropic",
        "tpm": 100000,
        "rpm": 60,
        "tags": ["anthropic"],
        "timeout": 30.0,
    })

# ==================== Router Config (shared) ====================

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
        "w_latency": 0.2,
    },
)


# ==================== Scene A: Unified Routing (cross-model failover) ====================


async def scene_unified_routing():
    """
    Scene A: Unified routing

    model_config has no model (model=""); the client layer passes "*" to the Router.
    Router picks the best of all deployments. When the dead endpoint fails it
    automatically switches to another available deployment.

    Even when deployments have different model_names (deepseek-chat / gpt-4o-mini),
    in unified routing mode they are equal peers and Router selects an available one.
    """
    print("=" * 60)
    print("场景 A: 统一调度 (跨模型/跨 Provider failover)")
    print("=" * 60)

    # Key: ModelRequestConfig() without a model → model_name=""
    # → IntelliRouterModelClient layer converts to model="*" → Router unified routing
    model_config = ModelRequestConfig()

    print(f"\n[配置]")
    print(f"  model_config.model_name = '{model_config.model_name}' (空=统一调度)")
    print(f"  部署列表:")
    for dep in intelli_router_deployments:
        print(f"    - {dep['id']}: model={dep['model_name']}, provider={dep['provider']}, base={dep['api_base']}")
    print(f"  策略: adaptive")
    print(f"  预期: dead endpoint 失败 → 自动切到其他可用 deployment")

    workflow_card = WorkflowCard(
        id="unified_routing_workflow",
        name="unified_routing",
        version="1.0",
        description="Unified routing across all providers",
        input_params={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )

    flow = Workflow(card=workflow_card)
    start = Start()
    end = End({"responseTemplate": "{{output}}"})
    llm_config = LLMCompConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        template_content=[
            {"role": "system", "content": "You are a helpful assistant. Reply concisely in one sentence."},
            {"role": "user", "content": "{{query}}"},
        ],
        response_format={"type": "text"},
        output_config={"output": {"type": "string", "description": "LLM output"}},
    )
    llm = LLMComponent(llm_config)

    flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
    flow.add_workflow_comp("llm", llm, inputs_schema={"query": "${start.query}"})
    flow.set_end_comp("end", end, inputs_schema={"output": "${llm.output}"})
    flow.add_connection("start", "llm")
    flow.add_connection("llm", "end")

    agent = WorkflowAgent(WorkflowAgentConfig(
        id="unified_agent", version="0.1.0", description="Unified routing agent",
    ))
    agent.add_workflows([flow])

    print(f"\n[执行]")
    try:
        result = await Runner.run_agent(agent, {"query": "Say hello in 5 words"})
        output_result = result.get("output").result
        resp = output_result.get("response") if isinstance(output_result, dict) else str(output_result)
        print(f"  响应: {resp}")
        print(f"  [v] 统一调度成功! Router 自动选择了可用的 deployment (跳过了 dead)")
    except Exception as e:
        print(f"  [x] 失败: {e}")
        import traceback
        traceback.print_exc()

    _print_router_stats(agent)


# ==================== Scene B: Specific Model (backward compatible) ====================


async def scene_specific_model():
    """
    Scene B: Specific model

    model_config sets model="deepseek-chat" → route only within the deepseek-chat pool.
    This is the backward-compatible behavior.
    """
    print("\n" + "=" * 60)
    print("场景 B: 指定模型 (仅 deepseek-chat 池内路由)")
    print("=" * 60)

    model_config = ModelRequestConfig(model="deepseek-chat")

    print(f"\n[配置]")
    print(f"  model_config.model_name = '{model_config.model_name}'")
    print(f"  预期: 仅在 deepseek-chat 部署中路由 (dead → real)")

    workflow_card = WorkflowCard(
        id="specific_model_workflow",
        name="specific_model",
        version="1.0",
        description="Specific model routing",
        input_params={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )

    flow = Workflow(card=workflow_card)
    start = Start()
    end = End({"responseTemplate": "{{output}}"})
    llm_config = LLMCompConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        template_content=[
            {"role": "system", "content": "You are a helpful assistant. Reply concisely."},
            {"role": "user", "content": "{{query}}"},
        ],
        response_format={"type": "text"},
        output_config={"output": {"type": "string", "description": "LLM output"}},
    )
    llm = LLMComponent(llm_config)

    flow.set_start_comp("start", start, inputs_schema={"query": "${query}"})
    flow.add_workflow_comp("llm", llm, inputs_schema={"query": "${start.query}"})
    flow.set_end_comp("end", end, inputs_schema={"output": "${llm.output}"})
    flow.add_connection("start", "llm")
    flow.add_connection("llm", "end")

    agent = WorkflowAgent(WorkflowAgentConfig(
        id="specific_model_agent", version="0.1.0", description="Specific model agent",
    ))
    agent.add_workflows([flow])

    print(f"\n[执行]")
    try:
        result = await Runner.run_agent(agent, {"query": "Tell me a joke in 10 words"})
        output_result = result.get("output").result
        resp = output_result.get("response") if isinstance(output_result, dict) else str(output_result)
        print(f"  响应: {resp}")
        print(f"  [v] 指定模型路由成功 (dead-endpoint → deepseek-chat failover)")
    except Exception as e:
        print(f"  [x] 失败: {e}")

    _print_router_stats(agent)


# ==================== Scene C: Multi-Agent Shared Router ====================


async def scene_multi_agent():
    """
    Scene C: Multi-Agent shared Router

    Two agents use the same model_client_config → share the same ReliableRouter.
    Both use unified routing (model="") to verify Router instance sharing.
    """
    print("\n" + "=" * 60)
    print("场景 C: Multi-Agent 共享 Router")
    print("=" * 60)

    model_config = ModelRequestConfig()  # unified routing

    # --- Agent A: Translator ---
    workflow_card_a = WorkflowCard(
        id="translate_workflow_v2", name="translate", version="2.0",
        description="Translate to Chinese (unified routing)",
        input_params={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    )
    flow_a = Workflow(card=workflow_card_a)
    start_a = Start()
    end_a = End({"responseTemplate": "{{output}}"})
    llm_a = LLMComponent(LLMCompConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        template_content=[
            {"role": "system", "content": "Translate to Chinese. Only output the translation."},
            {"role": "user", "content": "{{query}}"},
        ],
        response_format={"type": "text"},
        output_config={"output": {"type": "string", "description": "Translation"}},
    ))
    flow_a.set_start_comp("start", start_a, inputs_schema={"query": "${query}"})
    flow_a.add_workflow_comp("llm", llm_a, inputs_schema={"query": "${start.query}"})
    flow_a.set_end_comp("end", end_a, inputs_schema={"output": "${llm.output}"})
    flow_a.add_connection("start", "llm")
    flow_a.add_connection("llm", "end")
    agent_a = WorkflowAgent(WorkflowAgentConfig(id="translator_v2", version="0.1.0", description="Translator"))
    agent_a.add_workflows([flow_a])

    # --- Agent B: Summarizer ---
    workflow_card_b = WorkflowCard(
        id="summarize_workflow_v2", name="summarize", version="2.0",
        description="Summarize input (unified routing)",
        input_params={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    )
    flow_b = Workflow(card=workflow_card_b)
    start_b = Start()
    end_b = End({"responseTemplate": "{{output}}"})
    llm_b = LLMComponent(LLMCompConfig(
        model_client_config=model_client_config,
        model_config=model_config,
        template_content=[
            {"role": "system", "content": "Summarize in under 20 words. Only output the summary."},
            {"role": "user", "content": "{{query}}"},
        ],
        response_format={"type": "text"},
        output_config={"output": {"type": "string", "description": "Summary"}},
    ))
    flow_b.set_start_comp("start", start_b, inputs_schema={"query": "${query}"})
    flow_b.add_workflow_comp("llm", llm_b, inputs_schema={"query": "${start.query}"})
    flow_b.set_end_comp("end", end_b, inputs_schema={"output": "${llm.output}"})
    flow_b.add_connection("start", "llm")
    flow_b.add_connection("llm", "end")
    agent_b = WorkflowAgent(WorkflowAgentConfig(id="summarizer_v2", version="0.1.0", description="Summarizer"))
    agent_b.add_workflows([flow_b])

    # --- Execute ---
    print(f"\n[Agent A: translator]")
    try:
        result_a = await Runner.run_agent(agent_a, {"query": "Hello, world!"})
        output_a = result_a.get("output").result
        resp_a = output_a.get("response") if isinstance(output_a, dict) else str(output_a)
        print(f"  翻译: {resp_a}")
    except Exception as e:
        print(f"  失败: {e}")

    print(f"\n[Agent B: summarizer]")
    try:
        result_b = await Runner.run_agent(agent_b, {
            "query": "AI is transforming industries by automating tasks and enabling new capabilities."
        })
        output_b = result_b.get("output").result
        resp_b = output_b.get("response") if isinstance(output_b, dict) else str(output_b)
        print(f"  摘要: {resp_b}")
    except Exception as e:
        print(f"  失败: {e}")

    # --- Router sharing verification ---
    router_a = _get_router(agent_a)
    router_b = _get_router(agent_b)

    print(f"\n[Router 共享验证]")
    print(f"  Agent A router id: {id(router_a)}")
    print(f"  Agent B router id: {id(router_b)}")

    if router_a is not None and router_a is router_b:
        print(f"  [v] 同一个 Router 实例! 状态共享，failover 记录跨 Agent 生效")
        print(f"  [v] 管理的 deployment: {[d.id for d in router_a.deployments]}")
    elif router_a is None:
        print(f"  [?] 无法提取 router")
    else:
        print(f"  [x] 不同 router 实例")


# ==================== Helpers ====================


def _get_router(agent):
    """Extract the ReliableRouter from inside a WorkflowAgent (demo verification only)."""
    for wf in (agent._workflows or []):
        for comp in (getattr(wf, '_components', None) or {}).values():
            model = getattr(comp, 'model', None) or getattr(comp, '_llm', None)
            if model and hasattr(model, '_client') and hasattr(model._client, '_router'):
                return model._client._router
    return None


def _print_router_stats(agent):
    """Print Router statistics."""
    router = _get_router(agent)
    if router is None:
        return
    stats = router.get_stats()
    print(f"\n  [Router 状态]")
    print(f"    模型池: {stats.get('model_list', [])}")
    print(f"    部署状态:")
    for dep_id, status in stats.get("deployment_status", {}).items():
        print(f"      {dep_id}: {status}")


# ==================== Main ====================


async def main_streaming():
    """Streaming counterpart of main(): consume chunks via Runner.run_agent_streaming."""
    print("\n" + "=" * 60)
    print("Hello Agent with IntelliRouter Demo (Streaming)")
    print("=" * 60)
    print(f"\n[Config Info]")
    print(f"  Model: {model_config.model_name}")
    print(f"  Deployments: {len(intelli_router_deployments)}")
    print(f"  Routing Strategy: adaptive")

    print(f"\n[Streaming]")
    try:
        chunk_count = 0
        # stream_modes=None falls back to the agent's default streaming events.
        # Pass a list[BaseStreamMode] (e.g. [BaseStreamMode.MESSAGES]) to filter.
        async for chunk in Runner.run_agent_streaming(
            workflow_agent,
            {"query": "Hello, tell me a joke in under 20 words"},
            stream_modes=None,
        ):
            chunk_count += 1
            print(f"  [chunk #{chunk_count}] {chunk}")
        print(f"\n[Done] received {chunk_count} streaming chunks")
    except Exception as e:
        print(f"\n[Streaming Failed] Error: {e}")
        import traceback
        traceback.print_exc()


async def async_main():
    await main_streaming()
    await scene_unified_routing()
    await scene_specific_model()
    await scene_multi_agent()

    print("\n" + "=" * 60)
    print("All demos complete.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(async_main())
