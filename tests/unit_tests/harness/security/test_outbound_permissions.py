# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Behavioral tests for URL arrays and pinned, key-scoped outbound HTTP."""

import asyncio
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock

import pytest
from agent_shield.tool_permission import PermissionLevel, PermissionResult, get_permission_manager

from openjiuwen.core.common.security.outbound import bind_outbound_permission_key, outbound_permission_key
from openjiuwen.core.common.security import outbound_http
from openjiuwen.harness.security.permission_checks import check_tool_permission, merge_permission_results


@pytest.fixture
def policy():
    manager = get_permission_manager()
    key = "w3-outbound-test"
    config = {
        "enabled": True,
        "tools": {"mcp_fetch_webpage": "allow"},
        "net_guard": {
            "enabled": True,
            "defaults": "allow",
            "urls": {"blocked.test": "deny"},
            "connection": {"allow_http": True, "allow_loopback": True},
        },
    }
    manager.update_config(config, key)
    yield key, config
    manager.remove_config(key)


@pytest.mark.asyncio
async def test_array_checks_each_url_with_key_and_keeps_warnings(policy):
    key, _ = policy
    result = await check_tool_permission(
        "mcp_fetch_webpage", {"url": ["https://example.com/?token=secret", "https://blocked.test/"]}, key
    )
    assert result.permission == PermissionLevel.DENY
    assert "network_secret_query" in result.warnings


def test_aggregation_keeps_paths_separate_from_warnings():
    result = merge_permission_results(
        [
            PermissionResult(PermissionLevel.ALLOW, "allow", "", warnings=["secret"], external_paths=["/outside"]),
            PermissionResult(PermissionLevel.UNDETERMINED, "unknown", ""),
        ]
    )
    assert result.permission == PermissionLevel.UNDETERMINED
    assert result.warnings == ["secret"]
    assert result.external_paths == ["/outside"]


@pytest.mark.asyncio
@pytest.mark.parametrize("addresses", [["8.8.8.8", "10.0.0.1"], ["127.0.0.1"], ["169.254.169.254"]])
async def test_connection_checks_entire_dns_snapshot(policy, monkeypatch, addresses):
    key, config = policy
    config["net_guard"]["connection"] = {}
    get_permission_manager().update_config(config, key)

    async def resolve(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in addresses]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    with bind_outbound_permission_key(key), pytest.raises(outbound_http.OutboundPermissionError):
        await outbound_http.check_connection("https://example.com/")


@pytest.mark.asyncio
async def test_unknown_key_never_registers_or_falls_back(monkeypatch):
    manager = get_permission_manager()
    publish = Mock(side_effect=AssertionError("read side must not publish"))
    monkeypatch.setattr(manager, "update_config", publish)
    with bind_outbound_permission_key("w3-missing-key"), pytest.raises(KeyError):
        await outbound_http.check_connection("https://8.8.8.8/")
    publish.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://blocked.test\n/",
        "https://blocked.test/ok/../secret",
        "https://blocked.test/%2e%2e/secret",
        "https://blocked.test/a%2fb",
        "https://user:secret@blocked.test/",
        "https://blocked.test/%zz",
        "https://2130706433/",
        "https://0x7f000001/",
        "https://blocked.test:0/",
    ],
)
async def test_ambiguous_transport_urls_are_rejected(policy, url):
    with bind_outbound_permission_key(policy[0]), pytest.raises(outbound_http.OutboundPermissionError):
        await outbound_http.check_connection(url)


@pytest.fixture
def local_http():
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            hits.append((self.path, self.headers.get("Host")))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://blocked.test:{self.server.server_port}/forbidden")
            else:
                self.send_response(200)
            self.end_headers()
            self.wfile.write(b"hello world")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_port, hits
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("sync", [False, True])
async def test_real_transport_pins_dns_keeps_host_and_blocks_redirect(policy, monkeypatch, local_http, sync):
    key, _ = policy
    port, hits = local_http
    original = socket.getaddrinfo
    names = []

    def resolve(host, port, *args, **kwargs):
        if host in {"allowed.test", "blocked.test"}:
            names.append(host)
            # A second resolution of allowed.test would return a different IP.
            address = "127.0.0.1" if names.count(host) == 1 else "127.0.0.2"
            return original(address, port, *args, **kwargs)
        return original(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    with bind_outbound_permission_key(key):
        if sync:
            response = await asyncio.to_thread(
                outbound_http.requests_request, "GET", f"http://allowed.test:{port}/", timeout=2
            )
            assert response.content == b"hello world"
            response.close()
        else:
            result = await outbound_http.aiohttp_request(
                "GET", f"http://allowed.test:{port}/", timeout_seconds=2, max_bytes=5
            )
            assert result[2] == b"hello"
        assert names == ["allowed.test"]
        assert hits == [("/", f"allowed.test:{port}")]
        names.clear()
        with pytest.raises(outbound_http.OutboundPermissionError):
            if sync:
                await asyncio.to_thread(
                    outbound_http.requests_request, "GET", f"http://allowed.test:{port}/redirect", timeout=2
                )
            else:
                await outbound_http.aiohttp_request("GET", f"http://allowed.test:{port}/redirect", timeout_seconds=2)
    assert hits[-1][0] == "/redirect"
    assert len(hits) == 2
    assert outbound_permission_key() is None


@pytest.mark.asyncio
async def test_proxy_and_unverified_tls_do_not_attempt_a_connection(policy, monkeypatch):
    key, _ = policy
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(socket, "getaddrinfo", connect)
    for kwargs in ({"proxies": {"https": "http://proxy:3128"}}, {"verify": False}):
        with bind_outbound_permission_key(key), pytest.raises(outbound_http.OutboundPermissionError):
            await asyncio.to_thread(outbound_http.requests_request, "GET", "https://example.com", **kwargs)
    connect.assert_not_called()


@pytest.mark.asyncio
async def test_context_is_task_local_and_restored_on_cancellation():
    started = asyncio.Event()

    async def blocked():
        with bind_outbound_permission_key("child"):
            started.set()
            await asyncio.Event().wait()

    with bind_outbound_permission_key("parent"):
        task = asyncio.create_task(blocked())
        await started.wait()
        assert outbound_permission_key() == "parent"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert outbound_permission_key() == "parent"
    assert outbound_permission_key() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("client_certificate", [False, True])
async def test_real_https_preserves_sni_and_validates_original_hostname(
    policy, monkeypatch, tmp_path, client_certificate
):
    import datetime
    import ssl
    import requests
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key, _ = policy
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "secure.test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("secure.test")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
        )
    )
    sni, hits = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if client_certificate:
                assert self.connection.getpeercert()
            hits.append(self.headers["Host"])
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"verified")

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    client_args = {}
    if client_certificate:
        context.load_verify_locations(cert_path)
        context.verify_mode = ssl.CERT_REQUIRED
        client_args["cert"] = (str(cert_path), str(key_path))
    context.set_servername_callback(lambda sock, server_name, ctx: sni.append(server_name))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        return original("127.0.0.1" if host in {"secure.test", "wrong.test"} else host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    try:
        with bind_outbound_permission_key(key):
            response = await asyncio.to_thread(
                outbound_http.requests_request,
                "GET",
                f"https://secure.test:{server.server_port}/",
                verify=str(cert_path),
                timeout=2,
                **client_args,
            )
            assert response.content == b"verified"
            response.close()
            with pytest.raises(requests.exceptions.SSLError):
                await asyncio.to_thread(
                    outbound_http.requests_request,
                    "GET",
                    f"https://wrong.test:{server.server_port}/",
                    verify=str(cert_path),
                    timeout=2,
                    **client_args,
                )
            if client_certificate:
                # Windows may reset the TCP connection instead of surfacing the
                # TLS certificate-required alert as an SSLError.
                with pytest.raises(requests.exceptions.ConnectionError):
                    await asyncio.to_thread(
                        outbound_http.requests_request,
                        "GET",
                        f"https://secure.test:{server.server_port}/",
                        verify=str(cert_path),
                        timeout=2,
                    )
        assert sni == ["secure.test", "wrong.test"] + (["secure.test"] if client_certificate else [])
        assert hits == [f"secure.test:{server.server_port}"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
