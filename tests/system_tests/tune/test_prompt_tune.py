"""
端到端（End-to-End）测试：信息抽取类任务提示词自优化
"""
import os
import unittest
import asyncio

from openjiuwen.dev_tools.tune import (
    create_chat_agent_config,
    create_chat_agent,
    JointOptimizer,
    DefaultEvaluator,
    Case,
    Trainer,
    CaseLoader
)
from openjiuwen.core.single_agent.legacy.config import LLMCallConfig
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig, ToolCall
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.common.logging import logger

API_BASE = os.getenv("API_BASE", "mock://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

# ——————————————————————————————————————————工具信息————————————————————————————————————#
TOOLS = [
    LocalFunction(
        card=ToolCard(
            name="ac_open",
            description="空调控制工具，根据用户指令打开空调",
        ),
        func=lambda a: a
    ),
    LocalFunction(
        card=ToolCard(
            name="ac_close",
            description="空调控制工具，根据用户指令关闭空调",
        ),
        func=lambda a: a
    ),
    LocalFunction(
        card=ToolCard(
            name="ac_control",
            description="空调温度调节工具，按用户指令设置温度",
            input_params={
                "type": "object",
                "properties": {
                    "temperature": {"description": "需要设置的温度", "type": "integer"},
                },
                "required": ["temperature"],
            },
        ),
        func=lambda a: a
    ),
]

TOOL_INFOS = [tool.card.tool_info() for tool in TOOLS]

# --------------------------- 待优化提示词 --------------------------- #
INFORMATION_EXTRACTION_TEMPLATE = """
你是一个信息抽取助手，请从给定句子中提取所有的人名名称
输出格式为[人名1, 人名2, ...]的列表形式，不要输出其他内容
以下是用户输入：
"""

TOOL_CALLS_TEMPLATE = """
你是一个工具调用助手，请根据用户的指令，调用工具
"""

INFORMATION_EXTRACTION_TEMPLATE_WITH_VARIABLES = """
你是一个{{role}}助手，请从给定句子中提取所有的人名名称
输出格式为[人名1, 人名2, ...]的列表形式，不要输出其他内容
以下是用户输入：
{{query}}
"""

# --------------------------- 提示词相关用例 --------------------------- #
INFORMATION_EXTRACTION_CASES = [
    Case(
        inputs={"query": "潘之恒（约1536—1621）字景升，号鸾啸生，冰华生，安徽歙县、岩寺人，侨寓金陵（今江苏南京）"},
        label={"output": "[潘之恒]"}
    ),
    Case(
        inputs={
            "query": "高祖二十二子：窦皇后生建成（李建成）、太宗皇帝（李世民）、玄霸（李玄霸）、元吉（李元吉），万贵妃生智云（李智云），莫嫔生元景（李元景），孙嫔生元昌（李元昌））"},
        label={"output": "[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]"}
    ),
    Case(
        inputs={
            "query": "郭造卿（1532—1593），字建初，号海岳，福建福清县化南里人（今福清市人），郭遇卿之弟，郭造卿少年的时候就很有名气，曾游学吴越"},
        label={"output": "[郭造卿, 郭遇卿]"}
    ),
    Case(
        inputs={
            "query": "沈自邠，字茂仁，号几轩，又号茂秀，浙江秀水长溪（今嘉兴南汇）人"},
        label={"output": "[沈自邠]"}
    )
]

TOOL_CALL_CASES = [
    Case(inputs=dict(query="请帮我打开空调"),
         label=dict(output="", tool_calls=[
             ToolCall(id="", type='function', name="ac_open", arguments="{}")]),
         ),
    Case(inputs=dict(query="请帮我关闭空调"),
         label=dict(output="", tool_calls=[
             ToolCall(id="", type='function', name="ac_close", arguments="{}")]),
         ),
    Case(inputs=dict(query="天气太热了，开一下空调"),
         label=dict(output="", tool_calls=[
             ToolCall(id="", type='function', name="ac_open", arguments="{}")]),
         ),
    Case(inputs=dict(query="有点冷，先帮我关窗，再调整到21度"),
         label=dict(output="", tool_calls=[
             ToolCall(id="", type='function', name="ac_control", arguments="{\"temperature\":21}")]),
         ),
    Case(inputs=dict(query="有点热，先帮我开窗，再调整到29度"),
         label=dict(output="", tool_calls=[
             ToolCall(id="", type='function', name="ac_control", arguments="{\"temperature\":29}")]),
         )
]

INFORMATION_EXTRACTION_CASES_WITH_VARIABLES = [
    Case(inputs={
        "role": "信息提取",
        "query": "潘之恒（约1536—1621）字景升，号鸾啸生，冰华生，安徽歙县、岩寺人，侨寓金陵（今江苏南京）"
    },
        label={"output": "[潘之恒]"}
    ),
    Case(inputs={
        "role": "信息提取",
        "query": "高祖二十二子：窦皇后生建成（李建成）、太宗皇帝（李世民）、玄霸（李玄霸）、元吉（李元吉），万贵妃生智云（李智云），莫嫔生元景（李元景），孙嫔生元昌（李元昌））"
    },
        label={"output": "[李建成, 李世民, 李玄霸, 李元吉, 李智云, 李元景, 李元昌]"}
    ),
    Case(inputs={
        "role": "信息提取",
        "query": "郭造卿（1532—1593），字建初，号海岳，福建福清县化南里人（今福清市人），郭遇卿之弟，郭造卿少年的时候就很有名气，曾游学吴越"
    },
        label={"output": "[郭造卿, 郭遇卿]"}
    ),
    Case(inputs={
        "role": "信息提取",
        "query": "沈自邠，字茂仁，号几轩，又号茂秀，浙江秀水长溪（今嘉兴南汇）人"
    },
        label={"output": "[沈自邠]"}
    ),
]


class PromptTuneTest(unittest.IsolatedAsyncioTestCase):
    # ------------------------------------------------------------------ #
    #                          提示词自由化初始化方法                        #
    # ------------------------------------------------------------------ #
    def show_result(self, evaluated_cases):
        for eval_result in evaluated_cases:
            logger.info(f"score: {eval_result.score}, reason: {eval_result.reason}, "
                  f"answer: {eval_result.answer}, label: {eval_result.case.label}")

    def create_agent(self, prompt: str, tools=None):
        # 0. define a chat single_agent
        config = create_chat_agent_config(
            agent_id='chat_agent',
            agent_version='1.0.0',
            description='<UNK>',
            model=LLMCallConfig(
                model=ModelRequestConfig(
                    model=MODEL_NAME
                ),
                model_client=ModelClientConfig(
                    client_provider=MODEL_PROVIDER,
                    api_key=API_KEY,
                    api_base=API_BASE,
                    verify_ssl=False
                ),
                system_prompt=[{"role": "system", "content": prompt}],
            )
        )
        agent = create_chat_agent(config, tools)
        return agent

    def create_trainer(self):
        # 1. define optimizer
        model_client_config = ModelClientConfig(
            client_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            verify_ssl=False
        )

        model_config = ModelRequestConfig(
            model=MODEL_NAME
        )

        optimizer = JointOptimizer(
            model_config,
            model_client_config,
            num_examples=0
        )

        # 2. define evaluator
        evaluator = DefaultEvaluator(
            model_config,
            model_client_config,
            metric="1. 如果是非工具调用，两个回答需要一致，包括数量和名字。注意：但可以忽略对引号格式问题以及tool_calls字段"
                   "2. 如果是工具调用，则只需要关注tool_calls字段中插件名称和插件参数是否一致，无需关注文本内容"
        )

        # 3. define trainer
        trainer = Trainer(
            evaluator=evaluator,
            optimizer=optimizer,
            num_parallel=5
        )
        return trainer

    # ------------------------------------------------------------------ #
    #                            测试用例本身                              #
    # ------------------------------------------------------------------ #
    @unittest.skip("skip system test")
    def test_agent_optimization(self):
        # 前向推理函数
        async def forward(agent, cases):
            return [await agent.invoke(case.inputs) for case in cases]

        # 创建agent，测试基线
        agent = self.create_agent(INFORMATION_EXTRACTION_TEMPLATE)

        # 创建评估器
        model_client_config = ModelClientConfig(
            client_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            api_base=API_BASE,
            verify_ssl=False
        )

        model_config = ModelRequestConfig(
            model=MODEL_NAME
        )

        evaluator = DefaultEvaluator(
            model_config,
            model_client_config,
            metric="1. 如果是非工具调用，两个回答需要一致，包括数量和名字。注意：但可以忽略对引号格式问题以及tool_calls字段"
                   "2. 如果是工具调用，则只需要关注tool_calls字段中插件名称和插件参数是否一致，无需关注文本内容"
        )

        # 创建优化器，执行优化
        with JointOptimizer(
                model_config,
                model_client_config,
                parameters=agent.get_llm_calls(),
                num_examples=1
        ) as optimizer:
            predicts = asyncio.run(forward(agent, INFORMATION_EXTRACTION_CASES))
            results = evaluator.batch_evaluate(INFORMATION_EXTRACTION_CASES, predicts)
            self.show_result(results)
            optimizer.backward(results)
            optimizer.update()

        # 评估优化后agent
        predicts = asyncio.run(forward(agent, INFORMATION_EXTRACTION_CASES))
        results = evaluator.batch_evaluate(INFORMATION_EXTRACTION_CASES, predicts)
        self.show_result(results)

    @unittest.skip("skip system test")
    def test_information_extraction_prompt_optimization(self):
        agent = self.create_agent(INFORMATION_EXTRACTION_TEMPLATE)
        trainer = self.create_trainer()
        case_loader = CaseLoader(cases=INFORMATION_EXTRACTION_CASES)

        from openjiuwen.dev_tools.tune.trainer.base import Callbacks, Progress

        class MyCallbacks(Callbacks):
            def on_train_epoch_end(self, agent, progress: Progress, cases):
                logger.info(f"cur_epoch_accuracy {progress.current_epoch}, {progress.best_batch_score}")

        trainer.set_callbacks(MyCallbacks())
        score, result = trainer.evaluate(agent, case_loader)
        logger.info(f"[原提示词推理效果]: score={score}")
        self.show_result(result)

        optimized_agent = trainer.train(agent, case_loader)

        score, result = trainer.evaluate(optimized_agent, case_loader)
        logger.info(f"[优化后提示词推理效果]: score={score}")
        self.show_result(result)

    @unittest.skip("skip system test")
    def test_tool_calls_prompt_optimization(self):
        agent = self.create_agent(TOOL_CALLS_TEMPLATE, TOOLS)
        trainer = self.create_trainer()
        case_loader = CaseLoader(cases=TOOL_CALL_CASES)

        score, result = trainer.evaluate(agent, case_loader)
        logger.info(f"[原提示词推理效果]: score={score}")
        self.show_result(result)

        optimized_agent = trainer.train(agent, case_loader, num_iterations=2)

        score, result = trainer.evaluate(optimized_agent, case_loader)
        logger.info(f"[优化后提示词推理效果]: score={score}")
        self.show_result(result)

    @unittest.skip("skip system test")
    def test_information_extraction_prompt_optimization_with_variables(self):
        agent = self.create_agent(INFORMATION_EXTRACTION_TEMPLATE_WITH_VARIABLES, TOOLS)
        trainer = self.create_trainer()
        case_loader = CaseLoader(cases=INFORMATION_EXTRACTION_CASES_WITH_VARIABLES)

        score, result = trainer.evaluate(agent, case_loader)
        logger.info(f"[原提示词推理效果]: score={score}")
        self.show_result(result)

        optimized_agent = trainer.train(agent, case_loader, num_iterations=3)

        score, result = trainer.evaluate(optimized_agent, case_loader)
        logger.info(f"[优化后提示词推理效果]: score={score}")
        self.show_result(result)
