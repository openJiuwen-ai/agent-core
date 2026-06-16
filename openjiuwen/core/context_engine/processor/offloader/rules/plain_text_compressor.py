from __future__ import annotations

from openjiuwen.core.context_engine.processor.offloader.rules.types import (
    ContentType,
    RuleCompressionResult,
    RuleContext,
)


class PlainTextCompressor:
    def compress(self, content: str, ctx: RuleContext) -> RuleCompressionResult:
        _ = ctx
        seen: set[str] = set()
        lines: list[str] = []
        for line in content.splitlines():
            normalized = " ".join(line.split())
            if normalized and normalized not in seen:
                seen.add(normalized)
                lines.append(normalized)
            elif not normalized and (not lines or lines[-1] != ""):
                lines.append("")
        compressed = "\n".join(lines).strip()
        return RuleCompressionResult(
            content=compressed,
            content_type=ContentType.PLAIN_TEXT,
            modified=compressed != content,
        )
