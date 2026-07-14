from openjiuwen.core.common.security.url_utils import UrlUtils


def test_should_bypass_proxy_matches_cidr_for_schemeless_ip_port(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "192.0.2.0/23")
    monkeypatch.delenv("no_proxy", raising=False)

    assert UrlUtils.should_bypass_proxy("192.0.2.15:8001")


def test_get_global_proxy_url_ignores_proxy_for_schemeless_no_proxy_cidr(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "192.0.2.0/23")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.setenv("http_proxy", "http://proxy.example.com:8080")

    assert UrlUtils.get_global_proxy_url("192.0.2.15:8001/v1") is None


def test_get_proxy_url_ignores_configured_proxy_for_no_proxy_cidr(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "192.0.2.0/23")
    monkeypatch.delenv("no_proxy", raising=False)

    assert UrlUtils.get_proxy_url(
        "192.0.2.15:8001/v1",
        "http://configured-proxy.example.com:8080",
    ) is None


def test_should_bypass_proxy_keeps_existing_url_cidr_behavior(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "192.0.2.0/23")
    monkeypatch.delenv("no_proxy", raising=False)

    assert UrlUtils.should_bypass_proxy("http://192.0.2.15:8001/v1")
