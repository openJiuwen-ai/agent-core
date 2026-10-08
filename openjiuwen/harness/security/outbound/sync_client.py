# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""``requests``-based host exit: every hop (initial URL and each 3xx target) is checked."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from openjiuwen.harness.security.outbound.policy import check_outbound_url

DEFAULT_MAX_REDIRECTS = 10
_BODY_KWARGS = ("data", "json", "files")

Response = requests.Response
RequestException = requests.exceptions.RequestException


def _next_method(status: int, method: str) -> str:
    if status == 303 and method != "HEAD":
        return "GET"
    if status in (301, 302) and method == "POST":
        return "GET"
    return method


def strip_auth_on_host_change(kwargs: dict[str, Any], old_url: str, new_url: str) -> None:
    """Drop ``Authorization`` and ``auth`` before following a redirect to another host."""
    if urlparse(old_url).hostname == urlparse(new_url).hostname:
        return
    headers = kwargs.get("headers")
    if isinstance(headers, dict):
        kwargs["headers"] = {k: v for k, v in headers.items() if str(k).lower() != "authorization"}
    kwargs.pop("auth", None)


def request(
    method: str,
    url: str,
    *,
    session: requests.Session | None = None,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    **kwargs: Any,
) -> requests.Response:
    """Send a request through the host exit.

    Redirects are followed manually so each target passes
    :func:`check_outbound_url`. There is deliberately no ``trust_env=False``
    retry on proxy errors: a direct-connect fallback would bypass egress proxies.
    """
    kwargs.pop("allow_redirects", None)
    # A streamed body is read after we return, so its session must stay open.
    own_session = session is None and not kwargs.get("stream")
    sess = session or requests.Session()
    current_method = method.upper()
    current_url = url
    try:
        for _hop in range(max_redirects + 1):
            check_outbound_url(current_url)
            resp = sess.request(current_method, current_url, allow_redirects=False, **kwargs)
            if not resp.is_redirect:
                return resp
            location = resp.headers.get("Location") or ""
            next_url = urljoin(current_url, location)
            new_method = _next_method(resp.status_code, current_method)
            if new_method != current_method:
                for key in _BODY_KWARGS:
                    kwargs.pop(key, None)
            strip_auth_on_host_change(kwargs, current_url, next_url)
            resp.close()
            current_method = new_method
            current_url = next_url
        raise requests.exceptions.TooManyRedirects(f"exceeded {max_redirects} redirects: {url}")
    finally:
        if own_session:
            sess.close()


def get(url: str, **kwargs: Any) -> requests.Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs: Any) -> requests.Response:
    return request("POST", url, **kwargs)


__all__ = [
    "DEFAULT_MAX_REDIRECTS",
    "RequestException",
    "Response",
    "get",
    "post",
    "request",
]
