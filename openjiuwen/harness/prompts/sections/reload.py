# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Offload prompt section for DeepAgent."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openjiuwen.harness.prompts.builder import PromptSection


RELOAD_HINT_CN = (
    "# 上下文压缩\n\n"
    "你的上下文在过长时会被自动压缩，并以如下 marker 标记：\n\n"
    "[[OFFLOAD: handle=<id>, type=<type>]]\n"
    "[[OFFLOAD: handle=<id>, type=<type>, path=<path>]]\n\n"
    "当你看到这类 marker，并且回答问题需要被隐藏的原始内容时，"
    "优先调用 read_file 工具读取 marker 中 path 指向的 offload 文件，"
    "从文件中恢复被压缩前的原始消息内容。\n\n"
    "调用规则：\n"
    "- 必须使用 marker 中 path= 后面的精确文件路径作为 read_file 的 file_path。\n"
    "- handle 是 offload 内容的标识，不是文件路径；不要把 handle 当作 file_path。\n"
    "- type 表示存储类型；只有 marker 中包含 path 字段时，才能通过 read_file 精确恢复原始内容。\n"
    "- 如果 marker 没有 path 字段，不要猜测路径，也不要从 handle 或其他字段自行拼接路径；"
    "应说明无法通过 read_file 精确恢复原始内容。\n"
    "- 如果 read_file 提示文件超过大小限制，请使用 offset 和 limit 分段读取同一个 path。\n"
    "- 如果只需要确认或定位特定内容，优先使用搜索工具读取相关片段，不要盲目读取整个 offload 文件。\n\n"
    "示例：看到 [[OFFLOAD: handle=abc123, type=filesystem, path=C:\\\\x\\\\MessageSummaryOffloader_abc123.json]] 时，"
    "应调用 read_file(file_path=\"C:\\\\x\\\\MessageSummaryOffloader_abc123.json\")。\n\n"
    "请勿猜测或编造缺失的内容。\n\n"
    "存储类型：\"filesystem\" 表示内容已持久化到 path 指向的文件；"
    "\"in_memory\" 表示会话缓存内容，如果 marker 没有 path，则无法通过 read_file 恢复。"
)

RELOAD_HINT_EN = (
    "# Context Compression\n\n"
    "Your context may be automatically compressed when it becomes too long "
    "and marked with one of these markers:\n\n"
    "[[OFFLOAD: handle=<id>, type=<type>]]\n"
    "[[OFFLOAD: handle=<id>, type=<type>, path=<path>]]\n\n"
    "When you see one of these markers and the hidden original content would help, "
    "prefer calling read_file on the exact path in the marker to restore the original "
    "message content from the offload file.\n\n"
    "Call rules:\n"
    "- Use the exact value after path= as read_file.file_path.\n"
    "- handle identifies the offloaded content, but it is not a file path; do not pass handle as file_path.\n"
    "- type identifies the storage backend; only markers with a path field can be precisely restored with read_file.\n"
    "- If the marker has no path field, do not guess, infer, or construct a path from handle or other fields; "
    "explain that read_file cannot precisely restore the original content.\n"
    "- If read_file reports that the file exceeds the size limit, "
    "use offset and limit to read the same path in chunks.\n"
    "- If you only need to confirm or locate specific content, prefer search tools for relevant portions instead of "
    "blindly reading the whole offload file.\n\n"
    'Example: for [[OFFLOAD: handle=abc123, type=filesystem, path=C:\\\\x\\\\MessageSummaryOffloader_abc123.json]], '
    'call read_file(file_path="C:\\\\x\\\\MessageSummaryOffloader_abc123.json").\n\n'
    "Do not guess or fabricate missing content.\n\n"
    'Storage type "filesystem" means the content is persisted at path; '
    '"in_memory" means session-cache content and cannot be restored with read_file when no path is present.'
)


def build_reload_section(
        language: str = "cn",
) -> "PromptSection":
    """Build a PromptSection for context offload hints."""
    from openjiuwen.harness.prompts.builder import PromptSection

    hint = RELOAD_HINT_CN if language == "cn" else RELOAD_HINT_EN

    return PromptSection(
        name="offload",
        content={language: hint},
        priority=90,
    )
