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
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail


class _AbilityManager:
    def __init__(self) -> None:
        self.cards = {}

    def add(self, card):
        self.cards[card.name] = card

    def remove(self, name: str):
        self.cards.pop(name, None)


class _Agent:
    def __init__(self) -> None:
        self.ability_manager = _AbilityManager()
        self.system_prompt_builder = type("_Builder", (), {"language": "en"})()


def test_filesystem_rail_registers_base_tools(tmp_path):
    async def _run():
        await Runner.start()
        try:
            card = SysOperationCard(
                id="test_filesystem_rail_base_tools",
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(work_dir=str(tmp_path)),
            )
            Runner.resource_mgr.add_sys_operation(card)
            sys_operation = Runner.resource_mgr.get_sys_operation(card.id)

            rail = FileSystemRail()
            rail.set_sys_operation(sys_operation)
            agent = _Agent()

            rail.init(agent)

            expected_cards = {
                "read_file",
                "write_file",
                "edit_file",
                "glob",
                "list_files",
                "grep",
                "bash",
            }
            assert expected_cards.issubset(set(agent.ability_manager.cards))
            assert "code" not in agent.ability_manager.cards
            assert "audio_transcription" not in agent.ability_manager.cards
            assert "audio_question_answering" not in agent.ability_manager.cards
            assert "audio_metadata" not in agent.ability_manager.cards
            assert "image_ocr" not in agent.ability_manager.cards
            assert "visual_question_answering" not in agent.ability_manager.cards
        finally:
            rail.uninit(agent)
            Runner.resource_mgr.remove_sys_operation(sys_operation_id=card.id)
            await Runner.stop()

    asyncio.run(_run())


def test_filesystem_rail_with_code_tool(tmp_path):
    async def _run():
        await Runner.start()
        try:
            card = SysOperationCard(
                id="test_filesystem_rail_with_code_tool",
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(work_dir=str(tmp_path)),
            )
            Runner.resource_mgr.add_sys_operation(card)
            sys_operation = Runner.resource_mgr.get_sys_operation(card.id)

            rail = FileSystemRail(with_code_tool=True)
            rail.set_sys_operation(sys_operation)
            agent = _Agent()

            rail.init(agent)

            assert "code" in agent.ability_manager.cards
        finally:
            rail.uninit(agent)
            Runner.resource_mgr.remove_sys_operation(sys_operation_id=card.id)
            await Runner.stop()

    asyncio.run(_run())
