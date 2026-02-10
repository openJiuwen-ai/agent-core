# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Self-Evolving ReAct Agent Example

This example demonstrates how to use ReActAgent with agent_evolving for
self-evolving training, including:
- Creating ReActAgent with configurable LLM client
- Preparing training and validation datasets
- Configuring evaluator and instruction optimizer
- Training with checkpoint save/resume capability
- Testing the evolved agent with inference

Prerequisites:
- Install agent_evolving dependencies
- Configure LLM API credentials
"""

from __future__ import annotations

import asyncio
import os

from openjiuwen.agent_evolving import (
    Case,
    CaseLoader,
    DefaultEvaluator,
    InstructionOptimizer,
    SingleDimUpdater,
    Trainer,
    TuneConstant,
)
from openjiuwen.core.foundation.llm import ModelClientConfig, ModelRequestConfig
from openjiuwen.core.single_agent import ReActAgentEvolve, ReActAgentConfig, AgentCard
from openjiuwen.core.common.logging import logger


# Configuration (modify according to your environment)
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "your model provider")
API_BASE = os.getenv("API_BASE", "your api base")
API_KEY = os.getenv("API_KEY", "your api key")
MODEL_NAME = os.getenv("MODEL_NAME", "your model name")
MODEL_TEMPERATURE = float(os.getenv("MODEL_TEMPERATURE", 0.3))
MODEL_TOP_P = float(os.getenv("MODEL_TOP_P", 0.9))
MODEL_TIMEOUT = int(os.getenv("MODEL_TIMEOUT", 120))


def create_react_agent(
    system_prompt: str,
    agent_id: str = "self_evolving_agent",
) -> ReActAgentEvolve:
    """Create a ReActAgent instance.

    Args:
        model_name: Model name (e.g., "gpt-4o", "deepseek-chat")
        api_key: API key
        api_base: API base URL
        system_prompt: Initial system prompt for instruction optimization
        agent_id: Unique agent identifier

    Returns:
        ReActAgent instance
    """
    agent_card = AgentCard(
        id=agent_id,
        name=f"{agent_id.title()}",
        description="A self-evolving agent with instruction optimization",
    )

    config = ReActAgentConfig()
    config.configure_model_client(
        provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        model_name=MODEL_NAME,
    )
    config.configure_prompt_template([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "{{query}}"},
    ])
    config.configure_max_iterations(TuneConstant.default_iteration_num)

    agent = ReActAgentEvolve(card=agent_card)
    agent.configure(config)

    return agent


def create_qa_cases() -> CaseLoader:
    """Create QA dataset for training and validation.

    The optimizer will use these cases to learn and improve the system prompt
    through instruction optimization.

    Returns:
        CaseLoader with sample QA pairs
    """
    cases = [
        Case(
            inputs={"query": "什么是机器学习？"},
            label={"answer": "机器学习是人工智能的一个分支，通过算法从数据中学习规律。"},
        ),
        Case(
            inputs={"query": "Python 如何读取文件？"},
            label={"answer": "使用 open() 函数，例如：with open('file.txt', 'r') as f: content = f.read()"},
        ),
        Case(
            inputs={"query": "水的化学式是什么？"},
            label={"answer": "水的化学式是 H₂O，由两个氢原子和一个氧原子组成。"}
        ),
        Case(
            inputs={"query": "光速大约是多少？"},
            label={"answer": "光速在真空中约为每秒 30 万公里，即 3×10⁸ 米/秒。"}
        ),
        Case(
            inputs={"query": "地球的直径是多少？"},
            label={"answer": "地球的平均直径约为 12,742 公里。"}
        ),
    ]
    return CaseLoader(cases)


async def test_agent(agent: ReActAgentEvolve, test_queries: list[dict]) -> None:
    """Test evolved agent with sample queries.

    Args:
        agent: ReActAgent instance (with optimized system prompt)
        test_queries: List of query dictionaries
    """
    logger.info("\n[test] Testing evolved agent with optimized prompt...")
    for query in test_queries:
        result = await agent.invoke(query)
        logger.info(f"\n[query] {query['query']}")
        logger.info(f"[answer] {result.get('output', result)}")


def main() -> None:
    """Run the complete self-evolving agent workflow."""
    # =========================================================
    # 1. Create ReActAgent with initial system prompt
    # =========================================================
    # The optimizer will improve this prompt based on training feedback
    initial_prompt = """你是一个 helpful 的 AI 助手。
请直接回答用户的问题，如果需要可以使用工具来辅助回答。"""

    agent = create_react_agent(
        system_prompt=initial_prompt,
        agent_id="react_agent_evolving",
    )

    logger.info(f"[agent] ReActAgent created with ID: {agent.card.id}")

    # =========================================================
    # 2. Prepare Dataset
    # =========================================================
    train_loader, val_loader = create_qa_cases().split(ratio=0.6)
    logger.info(f"[data] train: {len(train_loader)}, val: {len(val_loader)}")

    # =========================================================
    # 3. Configure Model, Evaluator, and Instruction Optimizer
    # =========================================================
    model_config = ModelRequestConfig(
        model=MODEL_NAME,
        temperature=MODEL_TEMPERATURE,
        max_tokens=1000,
        top_p=MODEL_TOP_P,
    )
    model_client_config = ModelClientConfig(
        client_provider=MODEL_PROVIDER,
        api_key=API_KEY,
        api_base=API_BASE,
        timeout=MODEL_TIMEOUT,
        verify_ssl=False,
    )

    # Evaluator scores model outputs against expected answers
    evaluator = DefaultEvaluator(
        model_config=model_config,
        model_client_config=model_client_config,
        metric="",
    )

    # InstructionOptimizer improves system_prompt and user_prompt based on gradients
    optimizer = InstructionOptimizer(
        model_config=model_config,
        model_client_config=model_client_config,
    )

    # =========================================================
    # 4. Configure Updater and Trainer
    # =========================================================
    ckpt_dir = os.path.join(os.path.dirname(__file__), ".checkpoints")
    os.makedirs(ckpt_dir, exist_ok=True)

    # SingleDimUpdater wraps optimizer to generate Updates from trajectories
    updater = SingleDimUpdater(optimizer)

    trainer = Trainer(
        updater=updater,
        evaluator=evaluator,
        num_parallel=2,
        early_stop_score=0.95,
        checkpoint_dir=ckpt_dir,
        resume_from=os.path.join(ckpt_dir, "latest.json"),
        checkpoint_every_n_epochs=1,
        checkpoint_on_improve=True,
    )

    # =========================================================
    # 5. Train (with checkpoint/resume)
    # =========================================================
    logger.info("\n[info] Starting self-evolving training with instruction optimization...")
    evolved_agent = trainer.train(
        agent=agent,
        train_cases=train_loader,
        val_cases=val_loader,
        num_iterations=3,
    )
    logger.info("[done] Training finished. Checkpoints saved.")

    # =========================================================
    # 6. Test Inference
    # =========================================================
    test_queries = [
        {"query": "请介绍一下机器学习的基本概念。"},
        {"query": "Python 怎么写文件？"},
    ]
    asyncio.run(test_agent(evolved_agent, test_queries))


if __name__ == "__main__":
    main()
