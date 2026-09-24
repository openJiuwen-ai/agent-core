# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TLS verification defaults for the web tools' HTTP transport (#1339).

``fetch_webpage`` / ``free_search`` hand the model-chosen URL's response body
straight into the agent's context. With verification off by default, a MITM
with a self-signed certificate could forge that content — indirect prompt
injection — so the default must be verify-on, with an explicit env opt-out
for intranet deployments that terminate TLS themselves.
"""

import pytest

from openjiuwen.harness.tools.web._common import _free_search_ssl_verify
from openjiuwen.harness.tools.web._http import _make_connector


@pytest.fixture(autouse=True)
def clean_ssl_env(monkeypatch):
    """Keep the env var out of every test's default state."""
    monkeypatch.delenv("FREE_SEARCH_SSL_VERIFY", raising=False)
    yield


def test_ssl_verify_defaults_to_on(monkeypatch):
    """Unset env → verification on (the #1339 fix)."""
    assert _free_search_ssl_verify() is True


def test_ssl_verify_can_be_opted_out_for_intranet(monkeypatch):
    """Explicit opt-out keeps the intranet escape hatch working."""
    for value in ("0", "false", "no", "off"):
        monkeypatch.setenv("FREE_SEARCH_SSL_VERIFY", value)
        assert _free_search_ssl_verify() is False, value


def test_ssl_verify_stays_on_for_explicit_truthy(monkeypatch):
    for value in ("1", "true", "yes", "on"):
        monkeypatch.setenv("FREE_SEARCH_SSL_VERIFY", value)
        assert _free_search_ssl_verify() is True, value


@pytest.mark.asyncio
async def test_make_connector_verifies_by_default():
    """The default connector must not carry the ssl=False opt-out."""
    connector = _make_connector()
    try:
        assert connector._ssl is not False
    finally:
        await connector.close()


@pytest.mark.asyncio
async def test_make_connector_honors_intranet_opt_out(monkeypatch):
    """The env opt-out still produces the verify-off connector."""
    monkeypatch.setenv("FREE_SEARCH_SSL_VERIFY", "0")
    connector = _make_connector()
    try:
        assert connector._ssl is False
    finally:
        await connector.close()
