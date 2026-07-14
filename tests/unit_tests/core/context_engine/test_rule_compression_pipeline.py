import json
from pathlib import Path

from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.pipeline import (
    RuleCompressionPipeline,
)
from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.compressors.search_results_compressor import (
    SearchResultsCompressor,
)
from openjiuwen.core.context_engine.processor.forked.offloader.rule_compression.types import RuleContext
from openjiuwen.core.foundation.llm import ToolMessage, UserMessage


class _Context:
    def __init__(self, workspace_dir: Path | None):
        self._workspace_dir = workspace_dir

    def context_window_tokens(self) -> int:
        return 100

    def get_messages(self):
        return []

    def session_id(self) -> str:
        return "session-a"

    def context_id(self) -> str:
        return "context-a"

    def workspace_dir(self) -> str:
        if self._workspace_dir is None:
            return ""
        return str(self._workspace_dir)


def test_rule_compression_dump_is_written_when_enabled(tmp_path):
    context = _Context(tmp_path)
    pipeline = RuleCompressionPipeline(
        time_func=lambda: 1234.567,
        enable_dump=True,
    )
    original = "same line\nsame line\nsame line"

    compressed = pipeline.compress(
        ToolMessage(content=original, tool_call_id="tc-repeat"),
        context,
        pass_name="add",
        max_chars=10,
        force=True,
        context_messages=[],
    )

    log_dir = tmp_path / "context" / "session-a_context" / "rule_compression_logs"
    log_files = list(log_dir.glob("*.json"))
    assert len(log_files) == 1
    payload = json.loads(log_files[0].read_text(encoding="utf-8"))
    assert payload["session_id"] == "session-a"
    assert payload["context_id"] == "context-a"
    assert payload["tool_call_id"] == "tc-repeat"
    assert payload["rule_compression_type"] == "PLAIN_TEXT"
    assert payload["rule_compression_pass"] == "add"
    assert payload["original_content"] == original
    assert payload["compressed_content"] == "same line"
    assert compressed.metadata["rule_compression_dump_path"] == str(log_files[0])


def test_rule_compression_dump_is_not_written_by_default(tmp_path):
    context = _Context(tmp_path)
    pipeline = RuleCompressionPipeline(time_func=lambda: 1234.567)
    original = "same line\nsame line\nsame line"

    pipeline.compress(
        ToolMessage(content=original, tool_call_id="tc-repeat"),
        context,
        pass_name="add",
        max_chars=10,
        force=True,
        context_messages=[],
    )

    log_dir = tmp_path / "context" / "session-a_context" / "rule_compression_logs"
    assert not log_dir.exists()


def test_rule_compression_dump_uses_env_directory_without_workspace(tmp_path, monkeypatch):
    dump_dir = tmp_path / "rule-dumps"
    monkeypatch.setenv("OPENJIUWEN_RULE_COMPRESSION_DUMP_DIR", str(dump_dir))
    context = _Context(None)
    pipeline = RuleCompressionPipeline(
        time_func=lambda: 1234.567,
        enable_dump=True,
    )
    original = "same line\nsame line\nsame line"

    compressed = pipeline.compress(
        ToolMessage(content=original, tool_call_id="tc-repeat"),
        context,
        pass_name="add",
        max_chars=10,
        force=True,
        context_messages=[],
    )

    log_files = list(dump_dir.glob("*.json"))
    assert len(log_files) == 1
    payload = json.loads(log_files[0].read_text(encoding="utf-8"))
    assert payload["original_content"] == original
    assert payload["compressed_content"] == "same line"
    assert compressed.metadata["rule_compression_dump_path"] == str(log_files[0])


def test_search_results_compression_ignores_numbered_line_prefixes():
    content = "\n".join(
        f"{index}\topenjiuwen/core/context_engine/processor/offloader/message_offloader.py:{index}:"
        f"    ForkedMessageOffloader match {index}"
        for index in range(1, 9)
    )

    result = SearchResultsCompressor().compress(
        content,
        RuleContext(
            max_tokens=100,
            search_max_matches_per_file=3,
            search_max_total_matches=3,
            search_max_files=15,
            count_tokens=lambda text: max(len(text) // 3, 1),
        ),
    )

    assert result.modified
    assert result.details["files_affected"] == 1
    assert result.details["retained_match_count"] == 3
    assert "1\topenjiuwen" not in result.content
    assert "[... and 5 more matches in openjiuwen/core/context_engine/processor/offloader/message_offloader.py]" in result.content
    assert "more files omitted" not in result.content


def test_rule_compression_pipeline_adds_builtin_cjk_query_terms():
    pipeline = RuleCompressionPipeline()

    terms = pipeline._query_terms_for_message(
        [UserMessage(content="检查密码刷新失败")],
        tool_name=None,
        tool_arguments=None,
    )

    assert "password" in terms
    assert "refresh" in terms
    assert "failure" in terms
