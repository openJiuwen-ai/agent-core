# coding: utf-8
"""Tests for RemoteSkillSyncCallback."""

from unittest.mock import MagicMock, patch

from openjiuwen.agent_evolving.callbacks.remote_skill_sync_callback import RemoteSkillSyncCallback
from openjiuwen.agent_evolving.trainer.progress import Progress


def _make_progress(epoch: int = 1, best_score: float = 0.8) -> Progress:
    return Progress(current_epoch=epoch, best_score=best_score)


class TestRemoteSkillSyncCallback:
    @staticmethod
    def test_epoch_end_pushes_content():
        """on_train_epoch_end should POST skill content to endpoint."""
        content_provider = MagicMock(return_value="# Skill v1")
        cb = RemoteSkillSyncCallback(
            sync_endpoint="http://agent:8080/api/skills/update",
            skill_name="fund_advisor",
            content_provider=content_provider,
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("openjiuwen.agent_evolving.callbacks.remote_skill_sync_callback.requests") as mock_req:
            mock_req.post.return_value = mock_resp
            mock_req.RequestException = Exception
            cb.on_train_epoch_end(MagicMock(), _make_progress(epoch=1), [])

        mock_req.post.assert_called_once()
        call_kwargs = mock_req.post.call_args
        payload = call_kwargs.kwargs.get("json", call_kwargs.args[1] if len(call_kwargs.args) > 1 else {})
        assert payload["skill_name"] == "fund_advisor"
        assert payload["content"] == "# Skill v1"
        assert payload["epoch"] == 1

    @staticmethod
    def test_duplicate_epoch_not_pushed():
        """Same epoch called twice should only POST once."""
        content_provider = MagicMock(return_value="# Skill")
        cb = RemoteSkillSyncCallback(
            sync_endpoint="http://agent:8080/api",
            skill_name="test",
            content_provider=content_provider,
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("openjiuwen.agent_evolving.callbacks.remote_skill_sync_callback.requests") as mock_req:
            mock_req.post.return_value = mock_resp
            mock_req.RequestException = Exception
            cb.on_train_epoch_end(MagicMock(), _make_progress(epoch=1), [])
            cb.on_train_epoch_end(MagicMock(), _make_progress(epoch=1), [])

        assert mock_req.post.call_count == 1

    @staticmethod
    def test_retry_on_failure():
        """First POST fails, second succeeds."""
        content_provider = MagicMock(return_value="# Skill")
        cb = RemoteSkillSyncCallback(
            sync_endpoint="http://agent:8080/api",
            skill_name="test",
            content_provider=content_provider,
            max_retries=2,
        )

        with patch("openjiuwen.agent_evolving.callbacks.remote_skill_sync_callback.requests") as mock_req:
            mock_req.RequestException = Exception
            mock_resp_fail = MagicMock()
            mock_resp_fail.raise_for_status.side_effect = Exception("connection error")
            mock_resp_ok = MagicMock()
            mock_resp_ok.raise_for_status = MagicMock()
            mock_req.post.side_effect = [mock_resp_fail, mock_resp_ok]

            with patch("openjiuwen.agent_evolving.callbacks.remote_skill_sync_callback.time"):
                cb.on_train_epoch_end(MagicMock(), _make_progress(epoch=1), [])

        assert mock_req.post.call_count == 2

    @staticmethod
    def test_max_retries_exceeded():
        """All POSTs fail → warning logged, no exception raised."""
        content_provider = MagicMock(return_value="# Skill")
        cb = RemoteSkillSyncCallback(
            sync_endpoint="http://agent:8080/api",
            skill_name="test",
            content_provider=content_provider,
            max_retries=1,
        )

        with patch("openjiuwen.agent_evolving.callbacks.remote_skill_sync_callback.requests") as mock_req:
            mock_req.RequestException = Exception
            mock_resp = MagicMock()
            mock_resp.raise_for_status.side_effect = Exception("fail")
            mock_req.post.return_value = mock_resp

            with patch("openjiuwen.agent_evolving.callbacks.remote_skill_sync_callback.time"):
                # Should not raise
                cb.on_train_epoch_end(MagicMock(), _make_progress(epoch=1), [])

        assert mock_req.post.call_count == 2  # initial + 1 retry

    @staticmethod
    def test_on_train_end_pushes_final():
        """on_train_end should push with epoch='final'."""
        content_provider = MagicMock(return_value="# Final Skill")
        cb = RemoteSkillSyncCallback(
            sync_endpoint="http://agent:8080/api",
            skill_name="test",
            content_provider=content_provider,
        )

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("openjiuwen.agent_evolving.callbacks.remote_skill_sync_callback.requests") as mock_req:
            mock_req.post.return_value = mock_resp
            mock_req.RequestException = Exception
            cb.on_train_end(MagicMock(), _make_progress(epoch=5, best_score=0.95), [])

        call_kwargs = mock_req.post.call_args
        payload = call_kwargs.kwargs.get("json", call_kwargs.args[1] if len(call_kwargs.args) > 1 else {})
        assert payload["epoch"] == "final"
        assert payload["content"] == "# Final Skill"

    @staticmethod
    def test_from_operator_factory():
        """from_operator should build content_provider from operator.get_state()."""
        mock_operator = MagicMock()
        mock_operator.get_state.return_value = {"skill_content": "# Operator Skill"}

        cb = RemoteSkillSyncCallback.from_operator(
            sync_endpoint="http://agent:8080/api",
            skill_name="fund_advisor",
            operator=mock_operator,
        )

        assert cb._skill_name == "fund_advisor"
        assert cb._content_provider() == "# Operator Skill"
        mock_operator.get_state.assert_called_once()
