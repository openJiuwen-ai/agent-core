# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Pinned HTTP transport for permission-scoped host web/search tools.

DNS snapshots come from the host, never tool arguments. Each HTTP hop is checked
with the published key; neither configs nor engines are created on this path.
Proxies require a separate pinned CONNECT implementation and fail closed here.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit

from yarl import URL

from agent_shield.tool_permission import PermissionLevel, get_permission_manager

from openjiuwen.core.common.security.outbound import outbound_permission_key

MAX_REDIRECTS = 10
_REDIRECTS = {301, 302, 303, 307, 308}


class OutboundPermissionError(PermissionError):
    """The current policy or transport cannot safely authorize a connection."""


@dataclass(frozen=True)
class ConnectionTarget:
    host: str
    port: int
    address: str
    url: str


async def check_connection(url: str) -> ConnectionTarget:
    """Resolve once and validate all returned addresses plus the selected endpoint."""
    key = outbound_permission_key()
    if key is None:
        raise OutboundPermissionError("Outbound permission context is required")
    try:
        if (
            len(url) > 8192
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)
            or "\\" in url
            or re.search(r"%(?![0-9a-f]{2})|%(?:0[0-9a-f]|1[0-9a-f]|7f)", url, re.I)
        ):
            raise ValueError("ambiguous URL text")
        parts = urlsplit(url)
        host = parts.hostname
        if (
            len(url) > 8192
            or parts.scheme not in {"http", "https"}
            or not host
            or parts.username is not None
            or parts.password is not None
            or "%" in host
        ):
            raise ValueError("invalid target")
        if re.search(r"%(?:2f|5c|25)", parts.path, re.I) or any(
            segment in {".", ".."} for segment in unquote(parts.path).split("/")
        ):
            raise ValueError("ambiguous URL path")
        # Both clients execute this same ASCII URL. In particular, requests and
        # aiohttp must not resolve different IDNA spellings after authorization.
        url = str(URL(url).with_fragment(None))
        parts = urlsplit(url)
        host = parts.hostname
        if parts.port is not None and parts.port == 0:
            raise ValueError("invalid port")
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except (ValueError, UnicodeError) as exc:
        raise OutboundPermissionError("Invalid outbound HTTP URL") from exc
    try:
        addresses = [str(ipaddress.ip_address(host))]
    except ValueError:
        labels = host.removesuffix(".").split(".")
        if len(host) > 254 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
            raise OutboundPermissionError("Invalid outbound DNS host") from None
        try:
            socket.inet_aton(host)
        except OSError:
            pass
        else:
            raise OutboundPermissionError("Non-canonical numeric outbound host") from None
        records = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses or len(addresses) > 64:
        raise OutboundPermissionError("Invalid outbound DNS snapshot")
    result = await get_permission_manager().check_permission(
        "network_connect",
        {"url": url, "resolved_addresses": addresses, "connect_address": addresses[0]},
        key,
    )
    # ASK is not a completed authorization; the transport has no approval UI.
    if result.permission != PermissionLevel.ALLOW:
        raise OutboundPermissionError(f"Outbound connection requires ALLOW ({result.matched_rule})")
    return ConnectionTarget(host, port, addresses[0], url)


def _redirect_headers(headers: dict, previous: str, target: str) -> dict:
    def origin(url):
        parts = urlsplit(url)
        return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)

    if origin(previous) == origin(target):
        return headers
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in {"authorization", "cookie", "proxy-authorization"}
    }


async def aiohttp_request(
    method, url, *, headers=None, json_body=None, timeout_seconds, max_bytes=None, proxy_resolver=None
):
    """Return the web tools' existing five-field response using pinned DNS per hop."""
    import aiohttp

    class PinnedResolver(aiohttp.abc.AbstractResolver):
        def __init__(self, target):
            self.target = target

        async def resolve(self, host, port=0, family=socket.AF_INET):
            if host != self.target.host or port != self.target.port:
                raise OutboundPermissionError("Unexpected outbound resolver target")
            address = self.target.address
            return [
                {
                    "hostname": host,
                    "host": address,
                    "port": port,
                    "family": socket.AF_INET6 if ":" in address else socket.AF_INET,
                    "proto": 0,
                    "flags": 0,
                }
            ]

        async def close(self):
            pass

    headers = dict(headers or {})
    if any(name.lower() in {"host", "proxy-authorization"} for name in headers):
        raise OutboundPermissionError("Outbound Host/proxy headers cannot be overridden")
    async with asyncio.timeout(timeout_seconds):
        for hop in range(MAX_REDIRECTS + 1):
            if proxy_resolver is not None and proxy_resolver(url):
                raise OutboundPermissionError("Pinned outbound transport does not support proxies")
            target = await check_connection(url)
            url = target.url
            if proxy_resolver is not None and proxy_resolver(url):
                raise OutboundPermissionError("Pinned outbound transport does not support proxies")
            connector = aiohttp.TCPConnector(resolver=PinnedResolver(target), use_dns_cache=False, ssl=True)
            async with aiohttp.ClientSession(connector=connector, trust_env=False) as session:
                async with session.request(
                    method, url, headers=headers, json=json_body, allow_redirects=False
                ) as response:
                    if response.status in _REDIRECTS and response.headers.get("Location"):
                        if hop == MAX_REDIRECTS:
                            raise OutboundPermissionError("Too many outbound redirects")
                        next_url = urljoin(url, response.headers["Location"])
                        headers = _redirect_headers(headers, url, next_url)
                        if response.status == 303 or response.status in {301, 302} and method == "POST":
                            method, json_body = "GET", None
                            headers = {
                                k: v for k, v in headers.items() if k.lower() not in {"content-type", "content-length"}
                            }
                        url = next_url
                        continue
                    body = bytearray()
                    truncated = False
                    async for chunk in response.content.iter_chunked(65536):
                        remaining = None if max_bytes is None else max(0, max_bytes - len(body))
                        body.extend(chunk if remaining is None else chunk[:remaining])
                        if max_bytes is not None and len(body) >= max_bytes:
                            truncated = True
                            break
                    return response.status, dict(response.headers), bytes(body), str(response.url), truncated


def requests_request(method: str, url: str, **kwargs):
    """Synchronous counterpart for host tools already running in worker threads."""
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3 import HTTPConnectionPool, HTTPSConnectionPool

    if kwargs.get("proxies") or kwargs.get("verify") is False:
        raise OutboundPermissionError("Pinned outbound transport requires direct verified TLS")
    kwargs.pop("proxies", None)
    proxy_resolver = kwargs.pop("proxy_resolver", None)
    if kwargs.get("params"):
        url = requests.Request(method, url, params=kwargs.pop("params")).prepare().url
    follow = kwargs.pop("allow_redirects", True)
    headers = dict(kwargs.pop("headers", {}) or {})
    if any(name.lower() in {"host", "proxy-authorization"} for name in headers):
        raise OutboundPermissionError("Outbound Host/proxy headers cannot be overridden")
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise OutboundPermissionError("Synchronous outbound HTTP must run in a worker thread")

    for hop in range(MAX_REDIRECTS + 1):
        if proxy_resolver is not None and proxy_resolver(url):
            raise OutboundPermissionError("Pinned outbound transport does not support proxies")
        timeout = kwargs.get("timeout") or 10
        dns_timeout = timeout[0] or 10 if isinstance(timeout, tuple) else timeout
        target = asyncio.run(asyncio.wait_for(check_connection(url), dns_timeout))
        url = target.url
        if proxy_resolver is not None and proxy_resolver(url):
            raise OutboundPermissionError("Pinned outbound transport does not support proxies")
        secure = urlsplit(url).scheme == "https"
        pool = (
            HTTPSConnectionPool(
                target.address,
                target.port,
                assert_hostname=target.host,
                server_hostname=target.host,
                maxsize=1,
                block=True,
            )
            if secure
            else HTTPConnectionPool(target.address, target.port, maxsize=1, block=True)
        )

        class PinnedAdapter(HTTPAdapter):
            def get_connection(self, url, proxies=None):
                return pool

            def get_connection_with_tls_context(self, request, verify, proxies=None, cert=None):
                return pool

            def close(self):
                pool.close()
                super().close()

        host_header = f"[{target.host}]" if ":" in target.host else target.host
        if target.port != (443 if secure else 80):
            host_header += f":{target.port}"
        headers["Host"] = host_header
        with requests.Session() as session:
            session.trust_env = False
            session.mount("https://" if secure else "http://", PinnedAdapter(max_retries=0))
            response = session.request(method, url, headers=headers, allow_redirects=False, **kwargs)
        if not follow or response.status_code not in _REDIRECTS or not response.headers.get("Location"):
            return response
        response.close()
        if hop == MAX_REDIRECTS:
            raise OutboundPermissionError("Too many outbound redirects")
        next_url = urljoin(url, response.headers["Location"])
        headers.pop("Host", None)
        headers = _redirect_headers(headers, url, next_url)
        if urlsplit(url).netloc != urlsplit(next_url).netloc:
            kwargs.pop("auth", None)
            kwargs.pop("cookies", None)
        if response.status_code == 303 or response.status_code in {301, 302} and method == "POST":
            method = "GET"
            for name in ("data", "json", "files"):
                kwargs.pop(name, None)
            headers = {k: v for k, v in headers.items() if k.lower() not in {"content-type", "content-length"}}
        url = next_url
