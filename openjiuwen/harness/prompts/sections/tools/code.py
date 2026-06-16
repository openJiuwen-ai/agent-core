# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for the Code execution tool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.harness.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

DESCRIPTION: Dict[str, str] = {
    "cn": (
        "执行代码（Python 或 JavaScript）。\n\n"
        "每次调用在独立进程中执行，变量与对象不跨调用保留，请提交可独立运行的完整代码。\n\n"
        "重要：本工具用于运行计算、数据处理、算法逻辑等代码。"
        "对于文件类的读写操作（尤其涉及到大文件），尽量使用专用工具如：\n"
        " - 读取文件：使用 read_file 工具\n"
        " - 写入文件：使用 write_file 工具\n"
        " - 编辑文件：使用 edit_file 工具\n"
        "专用工具提供更好的安全性、权限控制和用户体验。"
    ),
    "en": (
        "Execute code (Python or JavaScript).\n\n"
        "Each invocation runs in an isolated process; variables and objects do not persist "
        "across calls—submit self-contained code every time.\n\n"
        "IMPORTANT: This tool is for running computations, data processing, and algorithmic logic. "
        "For file read/write operations (especially involving large files), prefer using dedicated tools such as:\n"
        " - Read files: Use read_file tool\n"
        " - Write files: Use write_file tool\n"
        " - Edit files: Use edit_file tool\n"
        "Dedicated tools provide better safety, permission control, and user experience."
    ),
}

CODE_PARAMS: Dict[str, Dict[str, str]] = {
    "code": {
        "cn": "要执行的代码",
        "en": "Code to execute",
    },
    "language": {
        "cn": "编程语言，支持 python 或 javascript，默认 python",
        "en": "Programming language, supports python or javascript, default python",
    },
    "timeout": {
        "cn": "超时时间（秒），默认 300，上限 3600",
        "en": "Timeout in seconds, default 300, max 3600",
    },
}


def get_code_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for code tool input_params."""
    p = CODE_PARAMS
    return {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": p["code"].get(language, p["code"]["cn"])},
            "language": {"type": "string", "description": p["language"].get(language, p["language"]["cn"])},
            "timeout": {"type": "integer", "description": p["timeout"].get(language, p["timeout"]["cn"])},
        },
        "required": ["code"],
    }


class CodeMetadataProvider(ToolMetadataProvider):
    """Code 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "code"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_code_input_params(language)
