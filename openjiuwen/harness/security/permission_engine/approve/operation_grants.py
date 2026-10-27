# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Object-scoped approvals, stored in the existing approval_overrides list.

Exact/file/URL records lift ASK (also UNDETERMINED in the opt-in strict host
policy) after all guards have run. Split shell grants apply inside ToolGuard
only, before the other guards are merged. Neither type overrides DENY.
The scope is derived from tool arguments on the server, never supplied as a path
or domain by the client. Legacy command patterns are left unchanged.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from openjiuwen.core.sys_operation.cwd import get_cwd
from openjiuwen.harness.security.permission_engine.fileguard.path_extract import extract_accesses_native
from openjiuwen.harness.security.permission_engine.netguard.net_guard import extract_fetch_url, url_hostname
from openjiuwen.harness.security.permission_engine.toolguard.tool_categories import (
    is_shell_tool,
    shell_tools_from_config,
)


GRANT_MATCH_TYPE = "exact_operation"
SHELL_GRANT_MATCH_TYPE = "shell_subcommand"
_FETCH_TOOLS = {"mcp_fetch_webpage", "fetch_webpage", "web_fetch_webpage"}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def operation_subject(tool: str, args: dict, config: Mapping, workspace: Path | None = None) -> dict:
    """Freeze the actual object(s), including the CWD for raw shell commands."""
    if is_shell_tool(tool, shell_tools_from_config(config)):
        # These tools execute workdir, never the unrecognised model argument cwd.
        known = tool in {"bash", "powershell", "core.powershell", "mcp_exec_command"}
        workdir = args.get("workdir") if known else None
        cwd = Path(workdir or get_cwd())
        if tool == "mcp_exec_command" and not cwd.is_absolute():
            cwd = Path(get_cwd()) / cwd
        return {
            "kind": "command",
            "command": args.get("command") or "",
            "cwd": str(cwd.resolve()),
            "shell_type": "powershell" if tool in {"powershell", "core.powershell"}
                          else args.get("shell_type", "auto"),
            # Keep relative resolution context, and bind all arguments for custom
            # shell tools whose execution parameter schema is unknown to the SDK.
            **({"context_cwd": str(Path(get_cwd()).resolve())}
               if workdir and not Path(workdir).is_absolute() else {}),
            **({"arguments": args} if not known else {}),
        }
    if tool in _FETCH_TOOLS:
        url = extract_fetch_url(args)
        if url:
            return {"kind": "url", "url": url}
    accesses = extract_accesses_native(tool, args, workspace or Path(get_cwd()), config)
    if accesses:
        return {"kind": "file", "accesses": sorted({(str(p.resolve()), action) for p, action, _ in accesses})}
    # Unknown tools must not fall back to a whole-tool approval.
    return {"kind": "arguments", "arguments": args}


def _registrable_domain(url: str) -> str | None:
    # tld ships the public suffix list. No network fetch/update during approval.
    from tld import get_tld

    host = url_hostname(url)
    if not host:
        return None
    result = get_tld("https://" + host, as_object=True, fail_silently=True)
    return result.fld if result is not None and result.fld != result.tld else None


def scope_options(subject: dict) -> list[dict[str, str]]:
    options = [{"value": "exact", "label": "仅当前对象"}]
    if subject["kind"] == "file":
        parents = sorted({str(Path(p).parent) for p, _ in subject["accesses"]})
        options.append({"value": "parent", "label": "上一级目录（含子目录）：" + ", ".join(parents)})
    elif subject["kind"] == "url":
        domain = _registrable_domain(subject["url"])
        if domain:
            options.append({"value": "domain", "label": "域名（含子域名）：" + domain})
    elif subject["kind"] == "command" and "arguments" not in subject:
        from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import split_shell_for_grants

        commands = split_shell_for_grants(subject["command"])
        if commands:
            options.append({"value": "subcommands", "label": "以下子命令可独立或组合复用：" + "；".join(commands)})
    return options


def build_operation_grant(
    tool: str, args: dict, config: Mapping, workspace: Path | None = None, *, mode: str = "allow", scope: str = "exact"
) -> dict:
    subject = operation_subject(tool, args, config, workspace)
    if mode not in {"allow", "allow_with_scope"}:
        raise ValueError("Unknown authorization mode")
    if scope != "exact" and mode != "allow_with_scope":
        raise ValueError("allow cannot widen the authorization scope")
    if scope != "exact" and scope not in {option["value"] for option in scope_options(subject)}:
        raise ValueError("Invalid authorization scope for this operation")
    if scope == "subcommands":
        raise ValueError("Use build_operation_grants for subcommand approvals")
    if scope == "parent":
        subject["accesses"] = sorted({(str(Path(p).parent), action) for p, action in subject["accesses"]})
    elif scope == "domain":
        url = subject.get("url", "")
        parsed = urlsplit(url)
        subject = {
            "kind": "url",
            "domain": _registrable_domain(url),
            "scheme": parsed.scheme,
            "port": parsed.port,
        }
    pattern = _json({"scope": scope, "subject": subject})
    return {
        "id": "operation_" + hashlib.sha256((tool + pattern).encode()).hexdigest()[:24],
        "tools": [tool],
        "match_type": GRANT_MATCH_TYPE,
        "pattern": pattern,
        "action": "allow",
    }


def build_operation_grants(
    tool: str, args: dict, config: Mapping, workspace: Path | None = None, *, mode: str = "allow", scope: str = "exact"
) -> list[dict]:
    """Build a batch; every shell child keeps the original invocation context."""
    if scope != "subcommands":
        return [build_operation_grant(tool, args, config, workspace, mode=mode, scope=scope)]
    subject = operation_subject(tool, args, config, workspace)
    if mode != "allow_with_scope" or scope not in {option["value"] for option in scope_options(subject)}:
        raise ValueError("Invalid authorization scope for this operation")
    from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import split_shell_for_grants

    grants = []
    for command in split_shell_for_grants(subject["command"]):
        pattern = _json({"scope": "exact", "subject": {**subject, "command": command}})
        grants.append({
            "id": "shell_" + hashlib.sha256((tool + pattern).encode()).hexdigest()[:24],
            "tools": [tool], "match_type": SHELL_GRANT_MATCH_TYPE, "pattern": pattern, "action": "allow",
        })
    return grants


def has_shell_subcommand_grant(config: Mapping, tool: str, args: dict) -> bool:
    """Match one literal child; never expose these grants to the final override."""
    grants = config.get("approval_overrides") or []
    if not any(isinstance(g, dict) and g.get("match_type") == SHELL_GRANT_MATCH_TYPE for g in grants):
        return False
    subject = operation_subject(tool, args, config)
    return any(
        isinstance(g, dict) and g.get("match_type") == SHELL_GRANT_MATCH_TYPE
        and matches_operation_grant({**g, "match_type": GRANT_MATCH_TYPE}, tool, subject)
        for g in grants
    )


def matches_operation_grant(grant: dict, tool: str, subject: dict) -> bool:
    if grant.get("match_type") != GRANT_MATCH_TYPE or grant.get("action") != "allow" or grant.get("tools") != [tool]:
        return False
    try:
        payload = json.loads(grant["pattern"])
        approved, scope = payload["subject"], payload["scope"]
        if approved["kind"] != subject["kind"]:
            return False
        if scope == "exact":
            return _json(approved) == _json(subject)
        if scope == "parent" and subject["kind"] == "file":
            return bool(subject["accesses"]) and all(
                any(
                    action == allowed_action and Path(path).is_relative_to(Path(parent))
                    for parent, allowed_action in approved["accesses"]
                )
                for path, action in subject["accesses"]
            )
        if scope == "domain" and subject["kind"] == "url":
            url = subject["url"]
            parsed, host, domain = urlsplit(url), url_hostname(url), approved["domain"]
            return bool(
                host
                and domain
                and (host == domain or host.endswith("." + domain))
                and parsed.scheme == approved["scheme"]
                and parsed.port == approved["port"]
            )
    except (KeyError, TypeError, ValueError):
        return False
    return False


def has_operation_grant(config: Mapping, tool: str, args: dict, workspace: Path | None = None) -> bool:
    grants = config.get("approval_overrides") or []
    if not any(isinstance(g, dict) and g.get("match_type") == GRANT_MATCH_TYPE for g in grants):
        return False
    subject = operation_subject(tool, args, config, workspace)
    return any(isinstance(g, dict) and matches_operation_grant(g, tool, subject) for g in grants)
