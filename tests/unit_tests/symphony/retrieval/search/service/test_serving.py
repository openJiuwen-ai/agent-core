import json
import os
from pathlib import Path
from unittest import mock

import pytest

from openjiuwen.symphony.retrieval.search.service import serving
from openjiuwen.symphony.retrieval.search.service.serving import RetriverTest


class _StopAfterPathResolution(Exception):
    pass


def _env(**overrides):
    env = {"MODEL_OBJECT_ID": "", "MODEL_SFS": "", "SERVED_MODEL_NAME": "", "TOP_K": "", "SKILL_INDEX_PATH": ""}
    env.update(overrides)
    return env


def _load_stopped_at_path_resolution(env):
    service = RetriverTest()
    with (
        mock.patch.dict(os.environ, env),
        mock.patch.object(RetriverTest, "_build_search_config", side_effect=_StopAfterPathResolution),
    ):
        with pytest.raises(_StopAfterPathResolution):
            service.load()
    return service


def test_load_falls_back_to_local_parentdir_when_only_model_object_id_set():
    service = _load_stopped_at_path_resolution(_env(MODEL_OBJECT_ID="obj-123"))
    currentdir = Path(serving.__file__).resolve().parent
    expected = os.path.abspath(os.path.join(currentdir, os.pardir, "model"))
    assert service.model_path == expected


def test_load_falls_back_to_local_parentdir_when_only_model_sfs_set():
    service = _load_stopped_at_path_resolution(_env(MODEL_SFS='{"sfsBasePath": "sfs://bucket/root"}'))
    currentdir = Path(serving.__file__).resolve().parent
    expected = os.path.abspath(os.path.join(currentdir, os.pardir, "model"))
    assert service.model_path == expected


def test_load_rejects_invalid_model_sfs_json():
    service = RetriverTest()
    with mock.patch.dict(os.environ, _env(MODEL_OBJECT_ID="obj-123", MODEL_SFS="{not-json")):
        with pytest.raises(ValueError, match="MODEL_SFS must be a JSON object string"):
            service.load()


def test_load_rejects_model_sfs_without_sfs_base_path():
    service = RetriverTest()
    with mock.patch.dict(os.environ, _env(MODEL_OBJECT_ID="obj-123", MODEL_SFS='{"other": "x"}')):
        with pytest.raises(ValueError, match="must contain sfsBasePath"):
            service.load()


def test_load_resolves_model_path_from_valid_sfs_pair():
    service = _load_stopped_at_path_resolution(
        _env(MODEL_OBJECT_ID="obj-123", MODEL_SFS='{"sfsBasePath": "sfs://bucket/root/"}')
    )
    parentdir = "sfs://bucket/root" + "/" + "obj-123"
    assert service.model_path == os.path.join(parentdir, "model")
    assert service.tokenizer_path == service.model_path


def test_calc_returns_empty_for_none_payload():
    service = RetriverTest()
    assert service.calc(None) == "[]"


def test_calc_returns_empty_when_query_missing():
    service = RetriverTest()
    assert service.calc({}) == "[]"
    assert service.calc({"data": {}}) == "[]"


def test_calc_returns_empty_when_query_blank():
    service = RetriverTest()
    assert service.calc({"data": {"query": "   "}}) == "[]"


def test_calc_returns_top_k_results():
    service = RetriverTest()
    service._loaded = True
    service.default_top_k = 2
    service.retriever = mock.MagicMock()
    service.retriever.search.return_value = iter(["skill-a", "skill-b", "skill-c"])
    assert service.calc({"data": {"query": "weather"}}) == json.dumps(["skill-a", "skill-b"], ensure_ascii=False)
