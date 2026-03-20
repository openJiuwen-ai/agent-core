# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from __future__ import annotations

import asyncio

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperationCard,
)
from openjiuwen.deepagents.rails.filesystem_rail import FileSystemRail
from openjiuwen.deepagents.schema.config import VisionModelConfig


class _AbilityManager:
    def __init__(self) -> None:
        self.cards = {}

    def add(self, card):
        self.cards[card.name] = card

    def remove(self, name: str):
        self.cards.pop(name, None)


class _Agent:
    def __init__(self, vision_model_config: VisionModelConfig | None = None) -> None:
        self.ability_manager = _AbilityManager()
        self.deep_config = type(
            "_DeepConfig",
            (),
            {"vision_model_config": vision_model_config},
        )()


def test_filesystem_rail_registers_vision_tools(tmp_path):
    vision_model_config = VisionModelConfig(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="mock-model",
    )

    async def _run():
        await Runner.start()
        try:
            card = SysOperationCard(
                id="test_filesystem_rail_with_vision",
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(work_dir=str(tmp_path)),
            )
            Runner.resource_mgr.add_sys_operation(card)
            sys_operation = Runner.resource_mgr.get_sys_operation(card.id)

            rail = FileSystemRail(language="en")
            rail.set_sys_operation(sys_operation)
            agent = _Agent(vision_model_config=vision_model_config)

            rail.init(agent)

            assert "image_ocr" in agent.ability_manager.cards
            assert "visual_question_answering" in agent.ability_manager.cards
            image_ocr_tool = Runner.resource_mgr.get_tool("ImageOCRTool")
            visual_question_answering_tool = Runner.resource_mgr.get_tool(
                "VisualQuestionAnsweringTool"
            )
            assert image_ocr_tool is not None
            assert visual_question_answering_tool is not None
            assert image_ocr_tool.vision_model_config is vision_model_config
            assert (
                visual_question_answering_tool.vision_model_config
                is vision_model_config
            )
        finally:
            rail.uninit(agent)
            Runner.resource_mgr.remove_sys_operation(sys_operation_id=card.id)
            await Runner.stop()

    asyncio.run(_run())
