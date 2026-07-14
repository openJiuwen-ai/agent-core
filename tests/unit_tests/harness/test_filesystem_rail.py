# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
import os

from openjiuwen.core.runner import Runner
from openjiuwen.core.sys_operation import (
    LocalWorkConfig,
    OperationMode,
    SysOperationCard,
)
from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail
from openjiuwen.harness.tools.filesystem import ReadFileTool


class _AbilityManager:
    def __init__(self) -> None:
        self.cards = {}

    def add(self, card):
        self.cards[card.name] = card

    def remove(self, name: str):
        self.cards.pop(name, None)


class _Agent:
    def __init__(self, *, enable_read_image_multimodal: bool = True) -> None:
        self.ability_manager = _AbilityManager()
        self.system_prompt_builder = type("_Builder", (), {"language": "en"})()
        self.deep_config = type(
            "_Config",
            (),
            {"enable_read_image_multimodal": enable_read_image_multimodal},
        )()


def test_sys_operation_rail_registers_base_tools(tmp_path):
    async def _run():
        await Runner.start()
        try:
            card = SysOperationCard(
                id="test_sys_operation_rail_base_tools",
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(work_dir=str(tmp_path)),
            )
            Runner.resource_mgr.add_sys_operation(card)
            sys_operation = Runner.resource_mgr.get_sys_operation(card.id)

            rail = SysOperationRail()
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
            if os.name == "nt":
                assert "powershell" in agent.ability_manager.cards
            else:
                assert "powershell" not in agent.ability_manager.cards
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


def test_sys_operation_rail_read_only(tmp_path):
    async def _run():
        await Runner.start()
        try:
            card = SysOperationCard(
                id="test_sys_operation_rail_read_only",
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(work_dir=str(tmp_path)),
            )
            Runner.resource_mgr.add_sys_operation(card)
            sys_operation = Runner.resource_mgr.get_sys_operation(card.id)

            rail = SysOperationRail(read_only=True)
            rail.set_sys_operation(sys_operation)
            agent = _Agent()

            rail.init(agent)

            assert {"read_file", "glob", "list_files", "grep", "bash"}.issubset(
                set(agent.ability_manager.cards)
            )
            assert "write_file" not in agent.ability_manager.cards
            assert "edit_file" not in agent.ability_manager.cards
            assert "code" not in agent.ability_manager.cards
        finally:
            rail.uninit(agent)
            Runner.resource_mgr.remove_sys_operation(sys_operation_id=card.id)
            await Runner.stop()

    asyncio.run(_run())


def test_sys_operation_rail_with_code_tool(tmp_path):
    async def _run():
        await Runner.start()
        try:
            card = SysOperationCard(
                id="test_sys_operation_rail_with_code_tool",
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(work_dir=str(tmp_path)),
            )
            Runner.resource_mgr.add_sys_operation(card)
            sys_operation = Runner.resource_mgr.get_sys_operation(card.id)

            rail = SysOperationRail(with_code_tool=True)
            rail.set_sys_operation(sys_operation)
            agent = _Agent()

            rail.init(agent)

            assert "code" in agent.ability_manager.cards
        finally:
            rail.uninit(agent)
            Runner.resource_mgr.remove_sys_operation(sys_operation_id=card.id)
            await Runner.stop()

    asyncio.run(_run())


def test_sys_operation_rail_applies_read_image_multimodal_config(tmp_path):
    async def _run():
        await Runner.start()
        try:
            card = SysOperationCard(
                id="test_sys_operation_rail_read_image_multimodal_config",
                mode=OperationMode.LOCAL,
                work_config=LocalWorkConfig(work_dir=str(tmp_path)),
            )
            Runner.resource_mgr.add_sys_operation(card)
            sys_operation = Runner.resource_mgr.get_sys_operation(card.id)

            rail = SysOperationRail()
            rail.set_sys_operation(sys_operation)
            agent = _Agent(enable_read_image_multimodal=False)

            rail.init(agent)

            read_tool = next(tool for tool in rail.tools if isinstance(tool, ReadFileTool))
            assert read_tool.enable_image_multimodal is False
        finally:
            rail.uninit(agent)
            Runner.resource_mgr.remove_sys_operation(sys_operation_id=card.id)
            await Runner.stop()

    asyncio.run(_run())
