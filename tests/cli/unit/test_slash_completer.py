"""Unit tests for slash command completion and ESC interrupt."""

from __future__ import annotations

from unittest.mock import MagicMock

from openjiuwen.harness.cli.ui.repl import (
    SLASH_COMMANDS,
    SlashCompleter,
    _SLASH_DESCRIPTIONS,
)


class TestSlashCompleter:
    """Tests for tab-completion of slash commands."""

    def _complete(self, text: str) -> list[str]:
        """Helper: get completion labels for *text*."""
        completer = SlashCompleter()
        doc = MagicMock()
        doc.text_before_cursor = text
        event = MagicMock()
        return [
            c.text for c in completer.get_completions(doc, event)
        ]

    def test_slash_prefix_matches_all(self) -> None:
        """'/' matches all commands (except /quit alias)."""
        results = self._complete("/")
        # Should include at least /help, /exit, /status, etc.
        assert "/help" in results
        assert "/exit" in results
        assert "/status" in results
        # /quit is excluded from completions
        assert "/quit" not in results

    def test_partial_match(self) -> None:
        """/co matches /compact, /cost."""
        results = self._complete("/co")
        assert "/compact" in results
        assert "/cost" in results
        assert "/help" not in results

    def test_exact_match(self) -> None:
        """/help matches only /help."""
        results = self._complete("/help")
        assert results == ["/help"]

    def test_no_match(self) -> None:
        """/xyz matches nothing."""
        results = self._complete("/xyz")
        assert results == []

    def test_no_completion_without_slash(self) -> None:
        """Normal text gets no completions."""
        results = self._complete("hello")
        assert results == []

    def test_no_completion_after_space(self) -> None:
        """/model name doesn't complete (second word)."""
        results = self._complete("/model gpt")
        assert results == []

    def test_descriptions_cover_all_commands(self) -> None:
        """All non-alias commands have descriptions."""
        for cmd in SLASH_COMMANDS:
            if cmd != "/quit":
                assert cmd in _SLASH_DESCRIPTIONS, (
                    f"Missing description for {cmd}"
                )

    def test_completion_has_meta(self) -> None:
        """Completions include display_meta descriptions."""
        completer = SlashCompleter()
        doc = MagicMock()
        doc.text_before_cursor = "/he"
        event = MagicMock()
        completions = list(
            completer.get_completions(doc, event)
        )
        assert len(completions) == 1
        assert completions[0].text == "/help"
        assert completions[0].display_meta is not None
