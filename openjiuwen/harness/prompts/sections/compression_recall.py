"""System-prompt guidance for compressed-context recall."""

from __future__ import annotations

from openjiuwen.harness.prompts.builder import PromptSection

_HINTS = {
    "cn": (
        "# 压缩上下文召回\n\n"
        "历史上下文可能包含 `[[COMPRESSION_RECALL: id=<memory_id>]]` 标记。"
        "当压缩摘要不足以回答当前问题时，调用 `recall_compressed_context`，传入标记中的 memory_id，"
        "并用当前需要查找的信息作为 query。该工具只检索当前 session，在返回预算内返回相关原文片段。"
        "如果没有匹配片段：① 换关键词重试——使用同义词、换另一种语言、或使用原文中的标识符"
        "（如函数名、文件路径、错误码）；② 或按工具返回的 `archive_path` 自行读取归档目录"
        "（`turns.jsonl` 查看轮次概览，`chunks/` 目录读取原文）。"
    ),
    "en": (
        "# Compressed Context Recall\n\n"
        "Earlier context may contain a `[[COMPRESSION_RECALL: id=<memory_id>]]` marker. "
        "When the compressed summary is insufficient, call `recall_compressed_context` with that memory_id and "
        "a query describing the information you need. The tool searches only the current session and returns "
        "relevant source chunks within a return budget. If nothing matches: 1) retry with different keywords — "
        "use synonyms, another language, or identifiers from the original text (such as function names, file "
        "paths, or error codes); 2) or inspect the archive directory yourself at the `archive_path` returned "
        "by the tool (`turns.jsonl` for a turn overview, `chunks/` for the original text)."
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
