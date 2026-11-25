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
    "从 marker 中 path 指向的 offload 文件恢复被压缩前的原始消息内容（读取方式见下方规则）。\n\n"
    "调用规则：\n"
    "- 必须使用 marker 中 path= 后面的精确文件路径作为 read_file 的 file_path。\n"
    "- handle 是 offload 内容的标识，不是文件路径；不要把 handle 当作 file_path。\n"
    "- type 表示存储类型；只有 marker 中包含 path 字段时，才能通过 read_file 精确恢复原始内容。\n"
    "- 如果 marker 没有 path 字段，不要猜测路径，也不要从 handle 或其他字段自行拼接路径；"
    "应说明无法通过 read_file 精确恢复原始内容。\n"
    "- path 指向 .txt 文本副本时，文件按行组织：前 3 行是文件头，超长行已折成多行（只插入换行，内容不变）。"
    "该文件通常超过单次读取上限：先用搜索工具在该文件中定位需要的内容，"
    "再用 read_file 的 offset（要跳过的行数，搜索结果第 N 行对应 offset=N-1）和 limit 只读取相关行，"
    "不要不带 limit 读取整个文件；也不要改读同名 .json 文件（供程序回读）。\n"
    "- 需要分页读取时，如果 marker 前给出了建议的 limit，按该值分页；"
    "read_file 提示超过 token 上限时，把 limit 减半后重试同一位置。\n"
    "- 卸载是为了不让原文占用上下文；确需通读时逐页处理、及时归纳，不要把全部原文累积在上下文中。\n\n"
    "示例：看到 [[OFFLOAD: handle=abc123, type=filesystem, path=C:\\\\x\\\\MessageSummaryOffloader_abc123.txt]] 时，"
    "先用搜索工具在该 path 中定位，再调用 "
    "read_file(file_path=\"C:\\\\x\\\\MessageSummaryOffloader_abc123.txt\", offset=<跳过的行数>, limit=<行数>)。\n\n"
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
    "restore the original message content from the offload file at the exact path in the marker "
    "(see the rules below for how to read it).\n\n"
    "Call rules:\n"
    "- Use the exact value after path= as read_file.file_path.\n"
    "- handle identifies the offloaded content, but it is not a file path; do not pass handle as file_path.\n"
    "- type identifies the storage backend; only markers with a path field can be precisely restored with read_file.\n"
    "- If the marker has no path field, do not guess, infer, or construct a path from handle or other fields; "
    "explain that read_file cannot precisely restore the original content.\n"
    "- When path points to a .txt text copy, the file is line-oriented: the first 3 lines are a header and "
    "over-long lines are wrapped (only line breaks are inserted; content is unchanged). The file usually exceeds "
    "a single read: locate the needed content with search tools first, then read only the relevant lines with "
    "read_file offset (lines to skip; search result line N means offset=N-1) and limit. Do not read the whole "
    "file without limit, and do not switch to the sibling .json file (it is for program reload).\n"
    "- When paging, use the suggested limit given before the marker if present; "
    "if read_file reports the token limit, halve limit and retry the same offset.\n"
    "- Offloading keeps the original out of the context; if you must go through all of it, "
    "process it page by page and summarize as you go instead of accumulating the whole original in context.\n\n"
    'Example: for [[OFFLOAD: handle=abc123, type=filesystem, path=C:\\\\x\\\\MessageSummaryOffloader_abc123.txt]], '
    "locate content in that path with search tools first, then call "
    'read_file(file_path="C:\\\\x\\\\MessageSummaryOffloader_abc123.txt", offset=<lines to skip>, limit=<lines>).\n\n'
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
