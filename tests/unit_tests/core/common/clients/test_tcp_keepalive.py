# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the tcp_keepalive socket_options builder.

Covers the contract required by IR-2026-0813-001:

* ``build_tcp_keepalive_socket_options()`` always returns a list containing
  ``(SOL_SOCKET, SO_KEEPALIVE, 1)`` on platforms that expose ``SO_KEEPALIVE``.
* Custom ``idle_seconds`` / ``interval_seconds`` / ``probe_count`` propagate
  to the corresponding ``TCP_KEEPIDLE`` / ``TCP_KEEPINTVL`` / ``TCP_KEEPCNT``
  tuples when the platform exposes those constants.
* ``get_default_tcp_keepalive_socket_options()`` returns a fresh copy on every
  call so callers cannot mutate the module-level cache.

Platform variance (e.g. Windows lacking ``TCP_KEEPIDLE``) is exercised via
``monkeypatch`` so silent regressions on a different platform are caught.
"""

import socket

import pytest

from openjiuwen.core.common.clients.tcp_keepalive import (
    build_tcp_keepalive_socket_options,
    get_default_tcp_keepalive_socket_options,
)

_SOL_SOCKET = socket.SOL_SOCKET
_SO_KEEPALIVE = socket.SO_KEEPALIVE
_IPPROTO_TCP = socket.IPPROTO_TCP
_TCP_KEEPIDLE = getattr(socket, "TCP_KEEPIDLE", None)
_TCP_KEEPINTVL = getattr(socket, "TCP_KEEPINTVL", None)
_TCP_KEEPCNT = getattr(socket, "TCP_KEEPCNT", None)


def _platform_has_tcp_keep_constants() -> bool:
    return (
        _TCP_KEEPIDLE is not None
        and _TCP_KEEPINTVL is not None
        and _TCP_KEEPCNT is not None
    )


class TestBuildTcpKeepaliveSocketOptions:
    def test_returns_list_with_sol_socket_so_keepalive_enabled(self):
        opts = build_tcp_keepalive_socket_options()

        assert isinstance(opts, list)
        assert (_SOL_SOCKET, _SO_KEEPALIVE, 1) in opts

    def test_so_keepalive_is_first_option_regardless_of_platform(self):
        opts = build_tcp_keepalive_socket_options()

        assert opts[0] == (_SOL_SOCKET, _SO_KEEPALIVE, 1)

    def test_default_values_applied_to_tcp_options(self):
        opts = build_tcp_keepalive_socket_options()

        if _TCP_KEEPIDLE is not None:
            assert (_IPPROTO_TCP, _TCP_KEEPIDLE, 30) in opts
        if _TCP_KEEPINTVL is not None:
            assert (_IPPROTO_TCP, _TCP_KEEPINTVL, 10) in opts
        if _TCP_KEEPCNT is not None:
            assert (_IPPROTO_TCP, _TCP_KEEPCNT, 3) in opts

    def test_custom_idle_interval_probe_values_propagated(self):
        opts = build_tcp_keepalive_socket_options(
            idle_seconds=120,
            interval_seconds=15,
            probe_count=5,
        )

        if _TCP_KEEPIDLE is not None:
            assert (_IPPROTO_TCP, _TCP_KEEPIDLE, 120) in opts
        if _TCP_KEEPINTVL is not None:
            assert (_IPPROTO_TCP, _TCP_KEEPINTVL, 15) in opts
        if _TCP_KEEPCNT is not None:
            assert (_IPPROTO_TCP, _TCP_KEEPCNT, 5) in opts

    def test_float_inputs_converted_to_int(self):
        opts = build_tcp_keepalive_socket_options(
            idle_seconds=45.7,
            interval_seconds=12.3,
            probe_count=2.9,
        )

        if _TCP_KEEPIDLE is not None:
            assert (_IPPROTO_TCP, _TCP_KEEPIDLE, 45) in opts
            assert (_IPPROTO_TCP, _TCP_KEEPIDLE, 45.7) not in opts
        if _TCP_KEEPINTVL is not None:
            assert (_IPPROTO_TCP, _TCP_KEEPINTVL, 12) in opts
        if _TCP_KEEPCNT is not None:
            assert (_IPPROTO_TCP, _TCP_KEEPCNT, 2) in opts

    def test_options_use_correct_socket_levels(self):
        opts = build_tcp_keepalive_socket_options()

        for level, _, _ in opts:
            assert level in (_SOL_SOCKET, _IPPROTO_TCP)

    def test_missing_tcp_keep_constants_skips_options_gracefully(self, monkeypatch):
        monkeypatch.delattr(socket, "TCP_KEEPIDLE", raising=False)
        monkeypatch.delattr(socket, "TCP_KEEPINTVL", raising=False)
        monkeypatch.delattr(socket, "TCP_KEEPCNT", raising=False)

        opts = build_tcp_keepalive_socket_options()

        assert (_SOL_SOCKET, _SO_KEEPALIVE, 1) in opts
        assert all(level != _IPPROTO_TCP for level, _, _ in opts)

    def test_partial_tcp_constants_present_only_those_available(self, monkeypatch):
        if _TCP_KEEPIDLE is None:
            pytest.skip("Platform lacks TCP_KEEPIDLE; cannot seed partial scenario")
        monkeypatch.delattr(socket, "TCP_KEEPINTVL", raising=False)
        monkeypatch.delattr(socket, "TCP_KEEPCNT", raising=False)

        opts = build_tcp_keepalive_socket_options()

        assert (_SOL_SOCKET, _SO_KEEPALIVE, 1) in opts
        assert (_IPPROTO_TCP, _TCP_KEEPIDLE, 30) in opts
        assert all(optname != _TCP_KEEPINTVL for _, optname, _ in opts)
        assert all(optname != _TCP_KEEPCNT for _, optname, _ in opts)

    def test_missing_so_keepalive_constant_returns_empty_list(self, monkeypatch):
        monkeypatch.delattr(socket, "SO_KEEPALIVE", raising=False)

        opts = build_tcp_keepalive_socket_options()

        assert opts == []


class TestGetDefaultTcpKeepaliveSocketOptions:
    def test_returns_non_empty_list_with_so_keepalive(self):
        opts = get_default_tcp_keepalive_socket_options()

        assert isinstance(opts, list)
        assert (_SOL_SOCKET, _SO_KEEPALIVE, 1) in opts

    def test_returns_copy_independent_of_cache(self):
        first = get_default_tcp_keepalive_socket_options()
        first.append((-1, -1, -1))
        first[0] = (-1, -1, -1)

        second = get_default_tcp_keepalive_socket_options()

        assert first != second
        assert (_SOL_SOCKET, _SO_KEEPALIVE, 1) in second
        assert (-1, -1, -1) not in second

    def test_repeated_calls_return_equivalent_but_independent_lists(self):
        a = get_default_tcp_keepalive_socket_options()
        b = get_default_tcp_keepalive_socket_options()

        assert a == b
        assert a is not b

    def test_default_options_match_built_options_with_default_args(self):
        assert get_default_tcp_keepalive_socket_options() == build_tcp_keepalive_socket_options()


class TestPlatformLandscapeIntegration:
    def test_full_constants_platform_builds_four_options(self):
        if not _platform_has_tcp_keep_constants():
            pytest.skip("Platform lacks TCP_KEEP*; not a Linux/macOS-like env")

        opts = build_tcp_keepalive_socket_options()

        assert len(opts) == 4
        assert opts[0] == (_SOL_SOCKET, _SO_KEEPALIVE, 1)
        assert (_IPPROTO_TCP, _TCP_KEEPIDLE, 30) in opts
        assert (_IPPROTO_TCP, _TCP_KEEPINTVL, 10) in opts
        assert (_IPPROTO_TCP, _TCP_KEEPCNT, 3) in opts
