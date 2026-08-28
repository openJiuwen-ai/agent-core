from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from openjiuwen.agent_evolving.agent_rl.config.service_config import RLServiceConfig
from openjiuwen.agent_evolving.agent_rl.online.service import configure_rl_service_logging
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.logging.log_config import configure_log_config, get_log_config_snapshot


def _config(**overrides):
    values = {
        "redis_url": "redis://127.0.0.1:6379/9",
        "model_id": "model-1",
        "base_model_path": "/models/base",
        "lora_repository_path": "/loras",
        "min_samples_for_training": 2,
        "max_samples_per_run": 4,
    }
    values.update(overrides)
    return RLServiceConfig(**values)


def test_service_config_is_fixed_model_and_validates_batch_bounds() -> None:
    assert _config().model_id == "model-1"
    assert _config().aigw_endpoint == "http://127.0.0.1:8080"
    assert _config().lora_activation_timeout == 150.0
    with pytest.raises(ValidationError, match="max_samples_per_run"):
        _config(max_samples_per_run=1)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        _config(poll_interval=30)
    with pytest.raises(ValidationError, match="loopback"):
        _config(listen_host="0.0.0.0")


def test_service_config_export_preserves_existing_config_exports() -> None:
    from openjiuwen.agent_evolving.agent_rl import config

    assert "RLServiceConfig" in config.__all__
    assert "RLConfig" in config.__all__


def test_service_config_rejects_parent_traversal_path(tmp_path) -> None:
    (tmp_path / "config").mkdir()
    config_path = tmp_path / "rl-service.yaml"
    config_path.write_text(yaml.safe_dump(_config().model_dump()), encoding="utf-8")

    with pytest.raises(BaseError) as exc_info:
        RLServiceConfig.from_yaml(tmp_path / "config" / ".." / config_path.name)

    assert exc_info.value.status is StatusCode.AGENT_RL_SERVICE_CONFIG_ERROR


def test_service_logging_uses_dedicated_rotating_file(tmp_path) -> None:
    log_path = tmp_path / "rl-service.log"
    previous = get_log_config_snapshot()
    try:
        configure_rl_service_logging(path=str(log_path), max_bytes=256, backup_count=3, level="INFO")
        for index in range(4):
            logger.info("run started %s %s", index, "x" * 256)

        logs = [log_path, *sorted(tmp_path.glob("rl-service.log.*"))]
        assert any("run started" in item.read_text(encoding="utf-8") for item in logs)
        assert len(logs) > 1
    finally:
        configure_log_config(previous)
