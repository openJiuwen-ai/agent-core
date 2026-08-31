# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pathlib import Path
import base64
import json

from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace

from openjiuwen.core.context_engine import (
    CompatibleTokenizerSpec,
    NativeTokenizerCounter,
    StringLengthCounter,
    TokenizerArtifactManager,
    TokenizerRegistry,
    TokenizerSelector,
    TokenizerSpec,
    TiktokenModelCounter,
)


def _write_tokenizer(path: Path) -> None:
    tokenizer = Tokenizer(
        WordLevel(
            {"[UNK]": 0, "hello": 1, "world": 2},
            unk_token="[UNK]",
        )
    )
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer.save(str(path))


def _write_tiktoken_model(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "tiktoken.model"
    path.write_text(
        "\n".join(f"{base64.b64encode(bytes([value])).decode()} {value}" for value in range(256)),
        encoding="ascii",
    )
    (directory / "tokenizer_config.json").write_text(
        json.dumps(
            {
                "added_tokens_decoder": {
                    "256": {"content": "[BOS]"},
                    "257": {"content": "[EOS]"},
                    "258": {"content": "<|end_of_msg|>"},
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def test_exact_native_tokenizer_is_selected(tmp_path: Path) -> None:
    artifact = tmp_path / "tokenizer.json"
    _write_tokenizer(artifact)

    counter = TokenizerSelector(
        provider="deepseek",
        model="deepseek-chat",
        spec=TokenizerSpec(
            provider="deepseek",
            model="deepseek-chat",
            tokenizer_id="deepseek-native",
            artifact_path=str(artifact),
        ),
    ).select()

    assert isinstance(counter, NativeTokenizerCounter)
    measurement = counter.measure("hello world")
    assert measurement.source == "native_tokenizer"
    assert measurement.tokenizer == "deepseek-native"
    assert measurement.estimated is False
    assert measurement.tokens == 2


def test_exact_tiktoken_model_is_selected(tmp_path: Path) -> None:
    artifact = _write_tiktoken_model(tmp_path / "kimi-k2.6")

    counter = TokenizerSelector(
        provider="kimi",
        model="kimi-k2.6",
        spec=TokenizerSpec(
            provider="kimi",
            model="kimi-k2.6",
            tokenizer_id="moonshotai/Kimi-K2.6",
            engine="tiktoken",
            artifact_path=str(artifact),
        ),
    ).select()

    assert isinstance(counter, TiktokenModelCounter)
    measurement = counter.measure("hello")
    assert measurement.source == "native_tokenizer"
    assert measurement.tokenizer == "moonshotai/Kimi-K2.6"
    assert measurement.estimated is False
    assert measurement.tokens == 5


def test_registry_matches_model_variants_case_insensitively(tmp_path: Path) -> None:
    artifact = tmp_path / "tokenizer.json"
    _write_tokenizer(artifact)

    registry = TokenizerRegistry(
        [
            TokenizerSpec(
                provider="OpenAI",
                model="GLM-5.2",
                source="LOCAL",
                artifact_path=str(artifact),
            )
        ]
    )
    counter = TokenizerSelector(
        provider="openai",
        model="glm-5.2_Thinking",
        registry=registry,
        allow_tiktoken_fallback=False,
    ).select()

    measurement = counter.measure("hello world")
    assert isinstance(counter, NativeTokenizerCounter)
    assert measurement.source == "family_tokenizer_fallback"
    assert measurement.fallback_reason == "model_variant_tokenizer_family"
    assert measurement.fallback_tokenizer_model == "GLM-5.2"


def test_registry_does_not_guess_ambiguous_model_variants(tmp_path: Path) -> None:
    artifact = tmp_path / "tokenizer.json"
    _write_tokenizer(artifact)

    registry = TokenizerRegistry(
        [
            TokenizerSpec(
                provider="openai",
                model="glm-5.2-a",
                artifact_path=str(artifact),
            ),
            TokenizerSpec(
                provider="OPENAI",
                model="glm-5.2-b",
                artifact_path=str(artifact),
            ),
        ]
    )

    assert registry.resolve("OpenAI", "GLM-5.2-c") is None


def test_explicit_family_fallback_is_used_for_missing_target(tmp_path: Path) -> None:
    artifact = tmp_path / "tokenizer.json"
    _write_tokenizer(artifact)

    counter = TokenizerSelector(
        provider="kimi",
        model="k3",
        spec=TokenizerSpec(
            provider="kimi",
            model="k3",
            tokenizer_id="kimi-k3",
            artifact_path=str(tmp_path / "missing-k3"),
            compatible_fallbacks=[
                CompatibleTokenizerSpec(
                    model="k2.6",
                    tokenizer_id="kimi-k2.6",
                    artifact_path=str(artifact),
                )
            ],
        ),
    ).select()

    measurement = counter.measure("hello world")
    assert isinstance(counter, NativeTokenizerCounter)
    assert measurement.source == "family_tokenizer_fallback"
    assert measurement.fallback_tokenizer_model == "k2.6"
    assert measurement.tokenizer == "kimi-k2.6"


def test_tiktoken_family_fallback_is_used_once(tmp_path: Path) -> None:
    fallback_artifact = _write_tiktoken_model(tmp_path / "kimi-k2.7")

    counter = TokenizerSelector(
        provider="kimi",
        model="kimi-k3",
        spec=TokenizerSpec(
            provider="kimi",
            model="kimi-k3",
            tokenizer_id="moonshotai/Kimi-K3",
            engine="tiktoken",
            artifact_path=str(tmp_path / "missing-k3"),
            compatible_fallbacks=[
                CompatibleTokenizerSpec(
                    model="kimi-k2.7",
                    tokenizer_id="moonshotai/Kimi-K2.7-Code",
                    engine="tiktoken",
                    artifact_path=str(fallback_artifact),
                )
            ],
        ),
    ).select()

    measurement = counter.measure("hello")
    assert isinstance(counter, TiktokenModelCounter)
    assert measurement.source == "family_tokenizer_fallback"
    assert measurement.fallback_tokenizer_model == "kimi-k2.7"
    assert measurement.tokenizer == "moonshotai/Kimi-K2.7-Code"


def test_only_one_configured_family_fallback_is_attempted(tmp_path: Path) -> None:
    first = tmp_path / "missing-first"
    second = _write_tokenizer(tmp_path / "second.json")
    counter = TokenizerSelector(
        provider="glm",
        model="glm-5.2",
        spec=TokenizerSpec(
            provider="glm",
            model="glm-5.2",
            artifact_path=str(tmp_path / "missing-target"),
            compatible_fallbacks=[
                CompatibleTokenizerSpec(model="glm-5", artifact_path=str(first)),
                CompatibleTokenizerSpec(model="glm-5.1", artifact_path=str(second)),
            ],
        ),
        allow_tiktoken_fallback=False,
    ).select()

    assert isinstance(counter, StringLengthCounter)


def test_unknown_native_model_uses_tiktoken_fallback(monkeypatch) -> None:
    monkeypatch.setattr(TokenizerSelector, "_tiktoken_available", staticmethod(lambda: True))
    counter = TokenizerSelector(provider="deepseek", model="deepseek-chat").select()

    measurement = counter.measure("中文")
    assert measurement.source == "tiktoken_fallback"
    assert measurement.fallback_reason == "native_tokenizer_unavailable"
    assert measurement.estimated is True


def test_tiktoken_failure_uses_unicode_length(monkeypatch) -> None:
    monkeypatch.setattr(TokenizerSelector, "_tiktoken_available", staticmethod(lambda: False))
    counter = TokenizerSelector(provider="deepseek", model="deepseek-chat").select()

    assert isinstance(counter, StringLengthCounter)
    measurement = counter.measure("中文🙂")
    assert measurement.source == "string_length_fallback"
    assert measurement.tokenizer == "unicode_codepoints"
    assert measurement.tokens == 3


def test_context_mode_falls_back_to_unicode_length_without_tiktoken(monkeypatch) -> None:
    monkeypatch.setattr(TokenizerSelector, "_tiktoken_available", staticmethod(lambda: True))
    counter = TokenizerSelector(
        provider="deepseek",
        model="deepseek-chat",
        allow_tiktoken_fallback=False,
    ).select()

    assert isinstance(counter, StringLengthCounter)
    assert counter.measure("中文🙂").source == "string_length_fallback"


def test_artifact_resolution_failure_reaches_fallback(monkeypatch) -> None:
    class RaisingManager:
        def resolve(self, spec):
            raise OSError("cache is unavailable")

    monkeypatch.setattr(TokenizerSelector, "_tiktoken_available", staticmethod(lambda: False))
    counter = TokenizerSelector(
        provider="deepseek",
        model="deepseek-chat",
        spec=TokenizerSpec(provider="deepseek", model="deepseek-chat", source="local"),
        manager=RaisingManager(),
    ).select()

    assert isinstance(counter, StringLengthCounter)


def test_modelscope_cached_artifact_is_available_offline(tmp_path: Path) -> None:
    artifact = tmp_path / "hub" / "models" / "org" / "model" / "tokenizer.json"
    artifact.parent.mkdir(parents=True)
    _write_tokenizer(artifact)

    manager = TokenizerArtifactManager(cache_dir=tmp_path, offline=True, enable_download=False)
    spec = TokenizerSpec(provider="qwen", model="qwen-chat", source="modelscope", tokenizer_id="org/model")

    assert manager.resolve(spec) == artifact


def test_huggingface_endpoint_accepts_env_and_explicit_timeout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HF_ENDPOINT", "https://company-hf.example/")
    manager = TokenizerArtifactManager(
        cache_dir=tmp_path,
        enable_download=True,
        endpoint="https://configured-hf.example/",
        request_timeout=7.5,
    )
    spec = TokenizerSpec(
        provider="qwen",
        model="qwen-chat",
        source="huggingface",
        tokenizer_id="org/model",
    )
    calls = {}

    def fake_snapshot_download(**kwargs):
        calls.update(kwargs)
        return str(tmp_path)

    manager._snapshot_download_huggingface(fake_snapshot_download, spec)

    assert manager.endpoint == "https://configured-hf.example"
    assert calls["endpoint"] == "https://configured-hf.example"
    assert calls["etag_timeout"] == 7.5


def test_huggingface_endpoint_defaults_to_environment(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HF_ENDPOINT", "https://company-hf.example/")

    manager = TokenizerArtifactManager(cache_dir=tmp_path)

    assert manager.endpoint == "https://company-hf.example"


def test_network_download_error_is_not_misclassified_as_revision_failure() -> None:
    error = RuntimeError(
        "LocalEntryNotFoundError: Got: ConnectError: [Errno 54] "
        "Connection reset by peer; no snapshot folder for revision"
    )

    assert TokenizerArtifactManager._error_reason(error) == "tokenizer_network_unavailable"


def test_tokenizer_spec_accepts_frontend_id_alias() -> None:
    spec = TokenizerSpec.model_validate(
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "id": "deepseek-ai/DeepSeek-V3",
        }
    )

    assert spec.tokenizer_id == "deepseek-ai/DeepSeek-V3"
