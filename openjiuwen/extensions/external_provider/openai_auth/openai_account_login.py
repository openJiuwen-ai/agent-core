# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Script entry point for testing OpenAI account login without the harness CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from openjiuwen.core.common.logging import llm_logger as logger
from openjiuwen.extensions.external_provider.openai_auth.openai_account_auth import (
    OpenAIAccountAuthError,
    OpenAIAccountAuthManager,
    OpenAIAccountAuthStatus,
    OpenAIAccountDeviceCode,
)


def _status_payload(status: OpenAIAccountAuthStatus) -> dict[str, Any]:
    return {
        "provider": "OpenAIAccount",
        "authenticated": status.authenticated,
        "auth_path": str(status.auth_path),
        "has_refresh_token": status.has_refresh_token,
        "expires_at": status.expires_at,
        "needs_refresh": status.needs_refresh,
        "error": status.error,
    }


def _log_info(message: str = "") -> None:
    logger.info(message)


def _log_error(message: str) -> None:
    logger.error(message)


def _print_auth_error(exc: OpenAIAccountAuthError) -> None:
    suffix = f" [{exc.code}]" if exc.code else ""
    _log_error(f"Error: {exc}{suffix}")


def _print_os_error(exc: OSError) -> None:
    _log_error(f"Error: failed to access OpenAIAccount auth store: {exc}")


def _print_device_code(device_code: OpenAIAccountDeviceCode) -> None:
    _log_info()
    _log_info("Open this URL in your browser:")
    _log_info(f"  {device_code.verification_uri}")
    _log_info()
    _log_info("Then enter this code:")
    _log_info(f"  {device_code.user_code}")
    _log_info()
    _log_info("Waiting for sign-in... Press Ctrl+C to cancel.")


def _cmd_status(args: argparse.Namespace) -> int:
    manager = OpenAIAccountAuthManager(auth_path=args.auth_path)
    status = manager.status()
    payload = _status_payload(status)

    if args.json_output:
        _log_info(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    state = "authenticated" if status.authenticated else "not authenticated"
    _log_info(f"OpenAIAccount: {state}")
    _log_info(f"Auth file: {status.auth_path}")
    if status.authenticated:
        refresh = "present" if status.has_refresh_token else "missing"
        _log_info(f"Refresh token: {refresh}")
        _log_info(f"Needs refresh: {'yes' if status.needs_refresh else 'no'}")
        if status.expires_at is not None:
            _log_info(f"Expires at: {status.expires_at:.0f}")
    elif status.error:
        _log_info(f"Reason: {status.error}")
    return 0


def _cmd_logout(args: argparse.Namespace) -> int:
    manager = OpenAIAccountAuthManager(auth_path=args.auth_path)
    removed = manager.logout()
    if removed:
        _log_info("OpenAIAccount credentials removed.")
    else:
        _log_info("No OpenAIAccount credentials found.")
    return 0


def _cmd_login(args: argparse.Namespace) -> int:
    manager = OpenAIAccountAuthManager(auth_path=args.auth_path)
    status = manager.status()
    if status.authenticated and not status.needs_refresh and not args.force:
        _log_info("OpenAIAccount credentials already available.")
        _log_info(f"Auth file: {status.auth_path}")
        return 0

    manager.login_with_device_code(
        on_device_code=_print_device_code,
        timeout_seconds=args.timeout_seconds,
        max_wait_seconds=args.max_wait_seconds,
    )
    _log_info()
    _log_info("Login successful.")
    _log_info(f"Auth file: {manager.auth_path}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openai_account_login",
        description="Test OpenAIAccount OAuth login without the harness CLI.",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.required = True

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--auth-path",
        type=Path,
        default=None,
        help="Override auth store path.",
    )

    login_parser = subparsers.add_parser("login", parents=[common], help="Sign in with device login.")
    login_parser.add_argument(
        "--force",
        action="store_true",
        help="Start a new login even when existing credentials are present.",
    )
    login_parser.add_argument(
        "--timeout",
        dest="timeout_seconds",
        default=15.0,
        type=float,
        help="HTTP timeout in seconds.",
    )
    login_parser.add_argument(
        "--max-wait",
        dest="max_wait_seconds",
        default=15 * 60,
        type=int,
        help="Maximum time to wait for browser sign-in.",
    )
    login_parser.set_defaults(handler=_cmd_login)

    status_parser = subparsers.add_parser("status", parents=[common], help="Show auth status.")
    status_parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Print machine-readable auth status.",
    )
    status_parser.set_defaults(handler=_cmd_status)

    logout_parser = subparsers.add_parser("logout", parents=[common], help="Remove stored credentials.")
    logout_parser.set_defaults(handler=_cmd_logout)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(argv) if argv is not None else sys.argv[1:]
    if not args_list:
        args_list = ["login"]
    parser = _build_parser()
    args = parser.parse_args(args_list)

    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        _log_error("Login cancelled.")
        return 130
    except OpenAIAccountAuthError as exc:
        _print_auth_error(exc)
        return 1
    except OSError as exc:
        _print_os_error(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
