from openjiuwen.core.runner.runner_config import RunnerConfig, PulsarConfig
from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerConfig


class TestCheckpointerConfigRepr:
    def test_redis_url_password_redacted(self):
        config = CheckpointerConfig(
            type="redis",
            conf={
                "connection": {
                    "url": "redis://:My%23SecretPwd@127.0.0.1:6379/0",
                    "connection_args": {"protocol": 2},
                },
                "ttl": {"default_ttl": 60, "refresh_on_read": True},
            },
        )
        repr_str = repr(config)
        assert "My%23SecretPwd" not in repr_str
        assert "***" in repr_str
        assert "redis://:***@127.0.0.1:6379/0" in repr_str

    def test_str_also_redacts_password(self):
        config = CheckpointerConfig(
            type="redis",
            conf={
                "connection": {
                    "url": "redis://:secret123@host:6379/0",
                },
            },
        )
        str_output = str(config)
        assert "secret123" not in str_output
        assert "***" in str_output

    def test_url_without_password_not_modified(self):
        config = CheckpointerConfig(
            type="redis",
            conf={
                "connection": {
                    "url": "redis://127.0.0.1:6379/0",
                },
            },
        )
        repr_str = repr(config)
        assert "redis://127.0.0.1:6379/0" in repr_str

    def test_nested_url_redacted(self):
        config = CheckpointerConfig(
            type="redis",
            conf={
                "urls": [
                    "redis://:password1@host1:6379/0",
                    "redis://:password2@host2:6379/0",
                ],
            },
        )
        repr_str = repr(config)
        assert "password1" not in repr_str
        assert "password2" not in repr_str

    def test_empty_conf(self):
        config = CheckpointerConfig(type="in_memory")
        repr_str = repr(config)
        assert "CheckpointerConfig" in repr_str


class TestPulsarConfigRepr:
    def test_url_password_redacted(self):
        config = PulsarConfig(url="pulsar://admin:secret@localhost:6650")
        repr_str = repr(config)
        assert "secret" not in repr_str
        assert "***" in repr_str

    def test_str_also_redacts_password(self):
        config = PulsarConfig(url="pulsar://user:password123@broker:6650")
        str_output = str(config)
        assert "password123" not in str_output
        assert "***" in str_output

    def test_url_without_password(self):
        config = PulsarConfig(url="pulsar://localhost:6650")
        repr_str = repr(config)
        assert "pulsar://localhost:6650" in repr_str

    def test_none_url(self):
        config = PulsarConfig()
        repr_str = repr(config)
        assert "url=None" in repr_str


class TestRunnerConfigRepr:
    def test_checkpointer_config_redacted_in_repr(self):
        runner_config = RunnerConfig(
            checkpointer_config=CheckpointerConfig(
                type="redis",
                conf={
                    "connection": {
                        "url": "redis://:SuperSecretPassword@host:6379/0",
                    },
                },
            ),
        )
        repr_str = repr(runner_config)
        assert "SuperSecretPassword" not in repr_str

    def test_distributed_config_pulsar_url_redacted(self):
        runner_config = RunnerConfig(
            distributed_mode=True,
        )
        runner_config.distributed_config.message_queue_config.pulsar_config = PulsarConfig(
            url="pulsar://user:password@broker:6650"
        )
        model_dump = runner_config.model_dump()
        pulsar_url = model_dump["distributed_config"]["message_queue_config"]["pulsar_config"]["url"]
        assert pulsar_url == "pulsar://user:password@broker:6650"
