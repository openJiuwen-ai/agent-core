"""System-prompt guidance for compressed-context recall."""

from __future__ import annotations

from openjiuwen.harness.prompts.builder import PromptSection

_HINTS = {
    "cn": (
        "# 压缩上下文召回\n\n"
        "上下文中出现 `[[COMPRESSION_RECALL: id=<memory_id>]]` 标记，表示该处的历史消息已被压缩，"
        "压缩前的原始内容已归档。\n\n"
        "当压缩后的摘要不足以回答当前问题、需要找回被压缩掉的内容时，调用 `recall_compressed_context` 工具："
        "传入标记中的 `memory_id`，并用需要查找的信息作为 `query`。"
        "工具会从该标记对应的原始上下文中检索，返回与 query 相关的内容。\n\n"
        "如果没有匹配到内容，可以更换关键词重试——使用同义词、换另一种语言、"
        "或使用原文中的标识符（如函数名、文件路径、错误码）。"
        "工具同时返回归档路径 `archive_path`，你可以根据需要自行使用。"
    ),
    "en": (
        "# Compressed Context Recall\n\n"
        "A `[[COMPRESSION_RECALL: id=<memory_id>]]` marker in the context means the messages at that point "
        "have been compressed, and their original content has been archived.\n\n"
        "When the compressed summary is insufficient and you need to retrieve what was compressed away, "
        "call the `recall_compressed_context` tool: pass the `memory_id` from the marker and use the "
        "information you are looking for as the `query`. The tool searches the original context "
        "corresponding to that marker and returns content relevant to the query.\n\n"
        "If nothing matches, retry with different keywords — use synonyms, another language, or "
        "identifiers from the original text (such as function names, file paths, or error codes). "
        "The tool also returns the archive path as `archive_path`, which you may use as you see fit."
    ),
}


def build_compression_recall_section(language: str = "cn") -> PromptSection:
    """Build the system-prompt section explaining compression recall usage."""
    selected_language = language if language in _HINTS else "cn"
    return PromptSection(
        name="compression_recall",
        content={selected_language: _HINTS[selected_language]},
        priority=90,
    )
