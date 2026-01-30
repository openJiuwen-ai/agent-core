#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SkillCreator - Use LLM to intelligently generate and optimize Skills
"""

import re
from pathlib import Path
from typing import Optional, Union

from openjiuwen.core.foundation.llm.model import Model
from openjiuwen.core.foundation.llm.schema.config import (
    ModelClientConfig,
    ModelRequestConfig,
)
from openjiuwen.dev_tools.skill_creator.base import SkillContent, SkillOptimizationResult


SKILL_GENERATION_PROMPT = '''你是一个 AI Agent Skill 专家，需要创建高质量的 Skill。

## 用户需求
{description}

## Skill 规范
1. **name**: hyphen-case 格式，全小写，最长 64 字符
2. **description**: 清晰说明功能和触发场景，50-300 字符
3. 使用 Markdown 格式
4. 提供具体的代码示例和步骤

## Skill 类型: {skill_type}
{skill_type_guide}

请生成完整的 SKILL.md 内容，格式如下:
---
name: {skill_name}
description: Complete description including when to use...
---

# Skill Title

[根据 Skill 类型组织内容结构]

请直接输出 SKILL.md 内容。
'''

# Skill 类型说明
SKILL_TYPE_GUIDES = {
    "workflow": """
**Workflow 类型** - 适用于顺序流程、步骤明确的任务
推荐结构:
- ## Overview - 简要说明
- ## Workflow Decision Tree - 决策树（可选）
- ## Step 1: xxx - 第一步
- ## Step 2: xxx - 第二步
- ## Step 3: xxx - 第三步
- ## Resources - 资源说明
特点: 使用清晰的步骤编号，包含决策分支和条件判断
""",
    "task": """
**Task 类型** - 适用于工具集合、提供多种操作
推荐结构:
- ## Overview - 简要说明
- ## Quick Start - 快速开始
- ## Task: xxx - 任务类别1
- ## Task: xxx - 任务类别2
- ## Resources - 资源说明
特点: 按任务类别组织，每个任务独立，可单独使用
""",
    "reference": """
**Reference 类型** - 适用于标准规范、指南文档
推荐结构:
- ## Overview - 简要说明
- ## Guidelines - 指南规范
- ## Specifications - 详细规格
- ## Usage - 使用方法
- ## Resources - 资源说明
特点: 详细的规范说明，作为参考文档使用
""",
    "capabilities": """
**Capabilities 类型** - 适用于多功能集成系统
推荐结构:
- ## Overview - 简要说明
- ## Core Capabilities - 核心能力
- ### 1. Feature A - 功能A
- ### 2. Feature B - 功能B
- ### 3. Feature C - 功能C
- ## Resources - 资源说明
特点: 展示多个相互关联的功能，强调能力集成
""",
}

SKILL_OPTIMIZATION_PROMPT = '''你是一个 AI Agent Skill 专家，需要优化现有的 Skill。

## 当前 SKILL.md 内容
```markdown
{current_content}
```

## 优化要求
1. 优化 description，包含功能说明和触发场景
2. 优化 workflow，使用清晰的步骤编号
3. 移除所有 TODO 占位符
4. 添加必要的代码示例
5. 保持原有核心功能

请返回优化后的完整 SKILL.md 内容。
'''

SKILL_OPTIMIZATION_WITH_DIRECTION_PROMPT = '''你是一个 AI Agent Skill 专家，需要按照用户的优化方向优化现有的 Skill。

## 当前 SKILL.md 内容
```markdown
{current_content}
```

## 用户的优化方向
{optimization_direction}

## 基本要求
1. 根据用户的优化方向进行针对性优化
2. 保持 SKILL.md 的标准格式（frontmatter + markdown body）
3. 保持原有核心功能
4. 确保 name 使用 hyphen-case 格式
5. 确保 description 清晰说明功能和触发场景

请返回优化后的完整 SKILL.md 内容。
'''


class SkillCreator:
    """
    Use LLM to intelligently generate and optimize Skills
    """

    def __init__(
        self,
        model_client_config: ModelClientConfig,
        model_request_config: ModelRequestConfig,
    ):
        """
        Initialize SkillCreator

        Args:
            model_client_config: Model client configuration
            model_request_config: Model request configuration
        """
        self.model_client_config = model_client_config
        self.model_request_config = model_request_config
        self.model = Model(
            model_client_config=model_client_config,
            model_config=model_request_config,
        )

    async def generate(
        self,
        mode: str = "create",
            *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        output_path: Optional[Union[str, Path]] = None,
        skill_type: str = "workflow",
        skill_path: Optional[Union[str, Path]] = None,
        optimization_direction: Optional[str] = None,
        auto_apply: bool = False,
    ) -> Union[SkillContent, SkillOptimizationResult]:
        """
        Generate or optimize Skill

        Args:
            mode: Operation mode, "create" to create new Skill, "optimize" to optimize existing Skill

            Create mode parameters (mode="create"):
                name: Skill name (hyphen-case)
                description: Skill description/requirements
                output_path: Output directory
                skill_type: Skill type (workflow/task/reference/capabilities)

            Optimize mode parameters (mode="optimize"):
                skill_path: Existing Skill directory path
                optimization_direction: Natural language description of optimization direction (optional)
                    - When not provided, perform comprehensive optimization according to default rules
                    - When provided, perform targeted optimization according to user-specified direction
                    - Example: "Optimize workflow process to make it clearer and more concise"
                auto_apply: Whether to automatically save changes

        Returns:
            Create mode: Returns SkillContent
            Optimize mode: Returns SkillOptimizationResult
        """
        if mode == "create":
            return await self._create(
                name=name,
                description=description,
                output_path=output_path,
                skill_type=skill_type,
            )
        elif mode == "optimize":
            return await self._optimize(
                skill_path=skill_path,
                optimization_direction=optimization_direction,
                auto_apply=auto_apply,
            )
        else:
            raise ValueError(f"Invalid mode: {mode}. Must be 'create' or 'optimize'.")

    async def _create(
        self,
        name: str,
        description: str,
        output_path: Union[str, Path],
        skill_type: str = "workflow",
    ) -> SkillContent:
        """
        Use LLM to create new Skill

        Args:
            name: Skill name (hyphen-case)
            description: Skill description/requirements
            output_path: Output directory
            skill_type: Skill type

        Returns:
            Created SkillContent
        """
        if not name:
            raise ValueError("name is required for create mode")
        if not description:
            raise ValueError("description is required for create mode")
        if not output_path:
            raise ValueError("output_path is required for create mode")

        output_path = Path(output_path)
        skill_dir = output_path / name

        if skill_dir.exists():
            raise FileExistsError(f"Skill directory already exists: {skill_dir}")

        # Use LLM to generate content
        skill_type_guide = SKILL_TYPE_GUIDES.get(skill_type, SKILL_TYPE_GUIDES["workflow"])
        prompt = SKILL_GENERATION_PROMPT.format(
            description=description,
            skill_name=name,
            skill_type=skill_type,
            skill_type_guide=skill_type_guide,
        )

        response = await self.model.invoke(messages=prompt)
        skill_md_content = self._extract_skill_md(response.content)

        # Create directory and files
        skill_dir.mkdir(parents=True, exist_ok=False)
        (skill_dir / "SKILL.md").write_text(skill_md_content, encoding="utf-8")

        # Create resource directories
        (skill_dir / "scripts").mkdir(exist_ok=True)
        (skill_dir / "references").mkdir(exist_ok=True)

        return SkillContent.from_path(skill_dir)

    async def _optimize(
        self,
        skill_path: Union[str, Path],
        optimization_direction: Optional[str] = None,
        auto_apply: bool = False,
    ) -> SkillOptimizationResult:
        """
        Use LLM to optimize existing Skill

        Args:
            skill_path: Skill directory path
            optimization_direction: Natural language description of optimization direction (optional)
            auto_apply: Whether to automatically save changes

        Returns:
            Optimization result
        """
        if not skill_path:
            raise ValueError("skill_path is required for optimize mode")

        skill_path = Path(skill_path)
        original = SkillContent.from_path(skill_path)

        # Use LLM to optimize
        current_content = original.to_skill_md()

        if optimization_direction:
            prompt = SKILL_OPTIMIZATION_WITH_DIRECTION_PROMPT.format(
                current_content=current_content,
                optimization_direction=optimization_direction,
            )
        else:
            prompt = SKILL_OPTIMIZATION_PROMPT.format(current_content=current_content)

        response = await self.model.invoke(messages=prompt)
        optimized_content = self._extract_skill_md(response.content)

        # Parse optimized content
        metadata, body = SkillContent._parse_skill_md(optimized_content)
        optimized = SkillContent(
            metadata=metadata,
            body=body,
            path=skill_path,
        )

        # Record changes
        changes = []
        if original.metadata.name != optimized.metadata.name:
            changes.append(f"Name: {original.metadata.name} → {optimized.metadata.name}")
        if original.metadata.description != optimized.metadata.description:
            changes.append("Description: Updated")
        if original.body != optimized.body:
            changes.append("Body: Content optimized")

        result = SkillOptimizationResult(
            original=original,
            optimized=optimized,
            changes=changes,
        )

        # Auto-save
        if auto_apply:
            self._save_skill(optimized)

        return result

    def _extract_skill_md(self, llm_response: str) -> str:
        """Extract SKILL.md content from LLM response"""
        code_block = re.search(r'```(?:markdown)?\n(---\n.*?\n---.*)```', llm_response, re.DOTALL)
        if code_block:
            return code_block.group(1).strip()

        # Search for frontmatter directly
        match = re.search(r'(---\n.*?\n---\n.*)', llm_response, re.DOTALL)
        if match:
            return match.group(1).strip()

        if llm_response.strip().startswith("---"):
            return llm_response.strip()

        raise ValueError("Cannot extract SKILL.md content from LLM response")

    def _save_skill(self, skill: SkillContent) -> None:
        """Save Skill to file"""
        if not skill.path:
            raise ValueError("Skill path not set")

        skill_md_path = skill.path / "SKILL.md"
        content = skill.to_skill_md()
        skill_md_path.write_text(content, encoding="utf-8")
