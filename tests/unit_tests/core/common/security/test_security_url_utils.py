import socket

import pytest

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.security.url_utils import UrlUtils


def _resolve_to(monkeypatch, *addresses):
    def fake_getaddrinfo(host, port, *args, **kwargs):
        infos = []
        for address in addresses:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            sockaddr = (address, 0, 0, 0) if family == socket.AF_INET6 else (address, 0)
            infos.append((family, socket.SOCK_STREAM, 6, "", sockaddr))
        return infos

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://0.0.0.0/",
        "http://100.64.0.1/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[fd00::1]/",
        "http://[::ffff:169.254.169.254]/",
    ],
)
def test_check_url_is_valid_rejects_non_global_addresses(monkeypatch, url):
    monkeypatch.delenv("SSRF_PROTECT_ENABLED", raising=False)

    with pytest.raises(BaseError, match="illegal ip address"):
        UrlUtils.check_url_is_valid(url)


def test_check_url_is_valid_rejects_when_any_resolved_address_is_internal(monkeypatch):
    monkeypatch.delenv("SSRF_PROTECT_ENABLED", raising=False)
    _resolve_to(monkeypatch, "93.184.216.34", "169.254.169.254")

    with pytest.raises(BaseError, match="illegal ip address"):
        UrlUtils.check_url_is_valid("http://mixed.example.com/")


def test_check_url_is_valid_rejects_internal_ipv6_resolution(monkeypatch):
    monkeypatch.delenv("SSRF_PROTECT_ENABLED", raising=False)
    _resolve_to(monkeypatch, "93.184.216.34", "fd00::1")

    with pytest.raises(BaseError, match="illegal ip address"):
        UrlUtils.check_url_is_valid("http://dual-stack.example.com/")


def test_check_url_is_valid_accepts_public_addresses(monkeypatch):
    monkeypatch.delenv("SSRF_PROTECT_ENABLED", raising=False)
    _resolve_to(monkeypatch, "93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")

    UrlUtils.check_url_is_valid("https://public.example.com/path")


def test_check_url_is_valid_allows_internal_addresses_when_protection_disabled(monkeypatch):
    monkeypatch.setenv("SSRF_PROTECT_ENABLED", "false")

    UrlUtils.check_url_is_valid("http://169.254.169.254/latest/meta-data/")


def test_check_url_is_valid_reports_resolution_failure(monkeypatch):
    def fail(*args, **kwargs):
        raise socket.gaierror("name not known")

    monkeypatch.setattr(socket, "getaddrinfo", fail)

    with pytest.raises(BaseError, match="resolving IP address failed"):
        UrlUtils.check_url_is_valid("http://unknown.example.invalid/")


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
