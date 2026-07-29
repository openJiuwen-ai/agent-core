# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import pytest

from openjiuwen.extensions.external_provider import (
    ExternalAuthProvider,
    ExternalProviderRegistryError,
    ProviderModelCatalog,
    create_auth_provider,
    create_model_catalog,
    list_auth_providers,
    list_model_catalogs,
)
from openjiuwen.extensions.external_provider.openai_auth import (
    OPENAI_ACCOUNT_PROVIDER,
    OpenAIAccountAuthManager,
    OpenAIAccountModelCatalog,
)


def test_builtin_openai_account_provider_is_registered(tmp_path):
    auth_path = tmp_path / "auth.json"
    cache_path = tmp_path / "models.json"

    auth = create_auth_provider(OPENAI_ACCOUNT_PROVIDER, auth_path=auth_path)
    catalog = create_model_catalog(OPENAI_ACCOUNT_PROVIDER, cache_path=cache_path)

    assert isinstance(auth, OpenAIAccountAuthManager)
    assert isinstance(auth, ExternalAuthProvider)
    assert isinstance(catalog, OpenAIAccountModelCatalog)
    assert isinstance(catalog, ProviderModelCatalog)
    assert OPENAI_ACCOUNT_PROVIDER in list_auth_providers()
    assert OPENAI_ACCOUNT_PROVIDER in list_model_catalogs()


def test_registry_normalizes_provider_case(tmp_path):
    catalog = create_model_catalog("openaiaccount", cache_path=tmp_path / "models.json")

    assert isinstance(catalog, OpenAIAccountModelCatalog)


def test_registry_creates_new_instances(tmp_path):
    first = create_auth_provider(OPENAI_ACCOUNT_PROVIDER, auth_path=tmp_path / "auth.json")
    second = create_auth_provider(OPENAI_ACCOUNT_PROVIDER, auth_path=tmp_path / "auth.json")

    assert first is not second


def test_unknown_provider_raises_clear_error():
    with pytest.raises(ExternalProviderRegistryError, match="Unknown external model catalog"):
        create_model_catalog("MissingProvider")
