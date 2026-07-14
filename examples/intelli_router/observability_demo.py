"""
IntelliRouter Observability Demo

Demonstrates using the intelli_router observability subsystem in agent-core:
  1. LoggingHook — structured routing event logs
  2. MetricsCollector — in-memory metrics collection
  3. TerminalDashboard — rich real-time terminal dashboard

Usage:
  export DEEPSEEK_API_KEY="sk-..."
  python examples/intelli_router/observability_demo.py
"""

import os
import sys
import time
import asyncio
import logging

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
DEAD_BASE = "http://localhost:19999"  # simulated unreachable endpoint

if not DEEPSEEK_KEY:
    print("[!] 请设置 DEEPSEEK_API_KEY 环境变量")
    sys.exit(1)

# Route routing-event logs to the console
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")

# ==================== Deployment Config ====================

intelli_router_deployments = [
    # Dead endpoint (triggers failover + retry events)
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
    # DeepSeek Chat
    {
        "model_name": "deepseek-chat",
        "api_key": DEEPSEEK_KEY,
        "api_base": "https://api.deepseek.com",
        "id": "deepseek-chat",
        "provider": "deepseek",
        "tpm": 100000,
        "rpm": 60,
        "tags": ["primary"],
        "timeout": 30.0,
    },
    # DeepSeek Reasoner
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

# ==================== ModelClientConfig (observability enabled) ====================

model_client_config = ModelClientConfig(
    client_provider="intelli_router",
    api_key="placeholder",
    api_base="http://placeholder",
    verify_ssl=False,
    intelli_router_deployments=intelli_router_deployments,
    intelli_router_strategy="adaptive",
    intelli_router_num_retries=2,
    intelli_router_timeout=30.0,
    intelli_router_enable_observability=True,  # enable observability
    intelli_router_strategy_kwargs={
        "token_threshold": 1000,
        "rpm_threshold": 10,
        "exploration_ratio": 0.1,
    },
)


# ==================== Helper ====================


def _get_router():
    """Extract the ReliableRouter from the module-level router cache."""
    from openjiuwen.core.foundation.llm.model_clients.intelli_router_model_client import _router_cache
    if _router_cache:
        return next(iter(_router_cache.values()))
    return None


def _get_metrics_collector(router):
    """Extract the MetricsCollector from the router's event_bus."""
    if not hasattr(router, 'event_bus') or router.event_bus is None:
        return None
    for handler in router.event_bus.handlers:
        if handler.__class__.__name__ == "MetricsCollector":
            return handler
    return None


# ==================== Demo Scenario ====================


async def run_requests():
    """Send multiple requests to trigger failover and normal routing."""
    print("=" * 60)
    print("IntelliRouter Observability Demo")
    print("=" * 60)

    model_config = ModelRequestConfig()  # unified routing

    workflow_card = WorkflowCard(
        id="observability_demo_workflow",
        name="observability_demo",
        version="1.0",
        description="Observability demo workflow",
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
        id="observability_demo_agent", version="0.1.0", description="Observability demo agent",
    ))
    agent.add_workflows([flow])

    # --- Send multiple requests ---
    queries = [
        "Say hello in 3 words",
        "What is 2+2?",
        "Name one color",
    ]

    print("\n[Phase 1] 发送请求，观察路由事件日志...")
    print("-" * 60)

    for i, query in enumerate(queries, 1):
        print(f"\n  Request #{i}: {query}")
        try:
            result = await Runner.run_agent(agent, {"query": query}, session=f"session_{i}")
            output_result = result.get("output").result
            resp = output_result.get("response") if isinstance(output_result, dict) else str(output_result)
            print(f"  Response: {resp}")
        except Exception as e:
            print(f"  Error: {e}")

    # --- Extract metrics ---
    router = _get_router()
    if router is None:
        print("\n[!] 无法提取 router，跳过指标展示")
        return

    collector = _get_metrics_collector(router)

    # --- Dashboard demo ---
    print("\n" + "=" * 60)
    print("[Phase 2] 启动 TerminalDashboard (3秒实时看板)...")
    print("=" * 60)

    try:
        from intelli_router import TerminalDashboard
        dashboard = TerminalDashboard(max_events=10, refresh_rate=0.5, title="Observability Demo Dashboard")
        router.event_bus.register(dashboard)
        dashboard.start()

        # Send another request so the dashboard has data to refresh
        await Runner.run_agent(agent, {"query": "Say bye in 2 words"}, session="session_dashboard")
        time.sleep(3)  # let the dashboard render for a few seconds

        dashboard.stop()
        router.event_bus.unregister(dashboard)
        print("\n  Dashboard 已停止")
    except ImportError:
        print("  [skip] rich 未安装，跳过 Dashboard 演示")
        print("  安装方式: pip install intelli-router[dashboard]")

    # --- Metrics summary ---
    print("\n" + "=" * 60)
    print("[Phase 3] MetricsCollector 指标汇总")
    print("=" * 60)

    if collector:
        stats = collector.get_stats()
        print(f"\n  总请求数:    {stats['total_requests']}")
        print(f"  成功:        {stats['successful']}")
        print(f"  失败:        {stats['failed']}")
        print(f"  重试:        {stats['retries']}")
        print(f"  部署耗尽:    {stats['exhausted']}")

        latency = stats['latency']
        if latency['count'] > 0:
            print(f"\n  延迟统计:")
            print(f"    平均: {latency['avg']:.3f}s")
            print(f"    最小: {latency['min']:.3f}s")
            print(f"    最大: {latency['max']:.3f}s")

        tokens = stats['tokens']
        print(f"\n  Token 消耗:")
        print(f"    Prompt:     {tokens['prompt']}")
        print(f"    Completion: {tokens['completion']}")
        print(f"    Total:      {tokens['total']}")

        if stats['by_model']:
            print(f"\n  按模型统计:")
            for model, model_stats in stats['by_model'].items():
                print(f"    {model}: requests={model_stats['requests']}, "
                      f"success={model_stats['successes']}, fail={model_stats['failures']}")

        if stats['by_deployment']:
            print(f"\n  按部署统计:")
            for dep_id, count in stats['by_deployment'].items():
                print(f"    {dep_id}: {count} 次成功")

        if stats['errors_by_type']:
            print(f"\n  错误类型:")
            for err_type, count in stats['errors_by_type'].items():
                print(f"    {err_type}: {count}")
    else:
        print("  [!] MetricsCollector 未找到")

    # --- Prometheus metrics export ---
    print("\n" + "=" * 60)
    print("[Phase 4] Prometheus 指标导出")
    print("=" * 60)

    try:
        import urllib.request
        from prometheus_client import start_http_server  # noqa: F401

        prom_port = 9092
        # Enable Prometheus on the existing collector and expose the HTTP endpoint
        collector.expose_prometheus(port=prom_port)
        print(f"\n  Prometheus HTTP server 已启动: http://localhost:{prom_port}/metrics")

        # Send a few more requests so the Prometheus counters have data
        print("  发送 2 个请求以填充 Prometheus 指标...")
        await Runner.run_agent(agent, {"query": "What is AI?"})
        await Runner.run_agent(agent, {"query": "Say yes"})

        # Scrape the metrics endpoint
        time.sleep(0.5)
        url = f"http://localhost:{prom_port}/metrics"
        with urllib.request.urlopen(url) as resp:
            metrics_text = resp.read().decode()

        # Filter intelli_router-related lines
        print(f"\n  curl {url} | grep intelli_router:")
        print("  " + "-" * 50)
        for line in metrics_text.splitlines():
            if line.startswith("intelli_router"):
                print(f"    {line}")
        print("  " + "-" * 50)
        print("  (生产环境中建议从启动时就开启 enable_prometheus=True)")
    except ImportError:
        print("  [skip] prometheus-client 未安装，跳过 Prometheus 演示")
        print("  安装方式: pip install prometheus-client")

    # --- Router status ---
    print("\n" + "=" * 60)
    print("[Phase 5] Router 内部状态")
    print("=" * 60)

    router_stats = router.get_stats()
    print(f"\n  部署状态:")
    for dep_id, status in router_stats.get("deployment_status", {}).items():
        print(f"    {dep_id}: {status}")

    print("\n" + "=" * 60)
    print("Demo 完成!")
    print("=" * 60)


# ==================== Main ====================

if __name__ == "__main__":
    asyncio.run(run_requests())
