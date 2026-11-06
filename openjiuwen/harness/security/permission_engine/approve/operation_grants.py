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
import os
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


def merge_operation_grants(existing: list[dict] | None, additions: list[dict] | None) -> list[dict]:
    """Upsert approval IDs in place; append new IDs in their original order.

    Shared by the SDK and host persistence so an unchanged grant set has the
    same snapshot before and after saving. Inputs remain independently mutable.
    """
    from copy import deepcopy

    merged, positions = [], {}
    for item in [*(existing or []), *(additions or [])]:
        if not isinstance(item, dict):
            continue
        key = item.get("id")
        if key and str(key) in positions:
            merged[positions[str(key)]] = deepcopy(item)
        else:
            if key:
                positions[str(key)] = len(merged)
            merged.append(deepcopy(item))
    return merged
_FETCH_TOOLS = {"mcp_fetch_webpage", "fetch_webpage", "web_fetch_webpage"}


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _exact_subject(subject: dict) -> dict:  # pylint: disable=too-many-return-statements
    """Canonicalize typed identities; retain literal matching for other commands."""
    if subject.get("kind") == "command" and subject.get("command_identity", {}).get("version") == 2:
        identity = subject["command_identity"]
        canonical = {key: identity[key] for key in ("version", "dialect", "name", "schema")}
        canonical["exe"] = os.path.normcase(identity["exe"])
        canonical["argv"] = [(kind, os.path.normcase(value) if kind.startswith("path") else value)
                             for kind, value in identity["argv"]]
        return {**{key: value for key, value in subject.items()
                   if key not in {"command", "command_identity", "file_command", "shell_type", "cwd"}},
                "cwd": os.path.normcase(subject["cwd"]), "shell_type": identity["dialect"],
                "command_identity": canonical}
    if subject.get("kind") == "command":
        return {key: value for key, value in subject.items() if key not in {"file_command", "command_identity"}}
    if subject.get("kind") == "program" and subject.get("command_identity", {}).get("version") == 2:
        command = _exact_subject({"kind": "command", "cwd": subject["cwd"],
                                  "command_identity": subject["command_identity"]})
        return {"kind": "program", "cwd": command["cwd"], "command_identity": command["command_identity"]}
    if subject.get("kind") == "file":
        return {**subject, "accesses": sorted((os.path.normcase(path), action) for path, action in subject["accesses"])}
    if subject.get("kind") != "url":
        return subject
    url = subject["url"]
    if not isinstance(url, str):
        return subject
    scheme, separator, _tail = url.partition("://")
    if not separator or scheme.lower() not in {"http", "https"} or any(c in url for c in "\r\n\t"):
        return subject
    parsed = urlsplit(url)
    if parsed.hostname and not parsed.path:
        end = len(scheme) + len(separator) + len(parsed.netloc)
        return {**subject, "url": url[:end] + "/" + url[end:]}
    return subject


def operation_subject(tool: str, args: dict, config: Mapping, workspace: Path | None = None) -> dict:
    """Freeze the actual object(s), including the CWD for raw shell commands."""
    if is_shell_tool(tool, shell_tools_from_config(config)):
        # These tools execute workdir, never the unrecognised model argument cwd.
        known = tool in {"bash", "powershell", "pwsh", "core.powershell", "mcp_exec_command"}
        workdir = args.get("workdir") if known else None
        cwd = Path(workdir or get_cwd())
        if tool == "mcp_exec_command" and not cwd.is_absolute():
            cwd = Path(get_cwd()) / cwd
        subject = {
            "kind": "command",
            "command": args.get("command") or "",
            "cwd": str(cwd.resolve()),
            "shell_type": "powershell" if tool in {"powershell", "pwsh", "core.powershell"}
                          else args.get("shell_type", "auto"),
            # Keep relative resolution context, and bind all arguments for custom
            # shell tools whose execution parameter schema is unknown to the SDK.
            **({"context_cwd": str(Path(get_cwd()).resolve())}
               if workdir and not Path(workdir).is_absolute() else {}),
            **({"arguments": args} if not known else {}),
        }
        if known:
            from openjiuwen.harness.security.permission_engine.approve.command_identity import (
                bound_native_identity,
                literal_command_identity,
            )

            bound, identity = bound_native_identity(tool, args)
            if not bound:
                identity = literal_command_identity(subject["command"], subject["cwd"], subject["shell_type"])
            if identity:
                subject["command_identity"] = identity
        return subject
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
    if subject["kind"] == "file" and not any(action == "delete" for _, action in subject["accesses"]):
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


def _build_operation_grants(
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
    stable = _stable_shell_context(subject)
    for command in split_shell_for_grants(subject["command"]):
        child = {**subject, "command": command}
        child.pop("command_identity", None)
        if stable:
            from openjiuwen.harness.security.permission_engine.approve.command_identity import literal_command_identity

            identity = literal_command_identity(command, subject["cwd"], subject["shell_type"])
            if identity:
                child["command_identity"] = identity
        pattern = _json({"scope": "exact", "subject": child})
        grants.append({
            "id": "shell_" + hashlib.sha256((tool + pattern).encode()).hexdigest()[:24],
            "tools": [tool], "match_type": SHELL_GRANT_MATCH_TYPE, "pattern": pattern, "action": "allow",
        })
    return grants


def _stable_shell_context(subject: dict) -> bool:
    """Do not derive paths after a command that may alter shell state."""
    from openjiuwen.harness.security.permission_engine.approve.command_identity import static_shell_context

    return static_shell_context(subject["command"], subject["cwd"], subject["shell_type"])


def build_operation_grants(tool: str, args: dict, config: Mapping, workspace: Path | None = None,
                           *, mode: str = "allow", scope: str = "exact") -> list[dict]:
    """Persist resource receipts separately from tool-bound command approvals."""
    grants = _build_operation_grants(tool, args, config, workspace, mode=mode, scope=scope)
    subject = operation_subject(tool, args, config, workspace)
    resources = []
    if subject["kind"] == "file":
        resources.append((subject, scope))
    elif subject.get("command_identity") and scope == "exact":
        if subject["command_identity"]["accesses"]:
            resources.append(({"kind": "file", "accesses": subject["command_identity"]["accesses"]}, "exact"))
    elif subject["kind"] == "command" and scope == "subcommands" and _stable_shell_context(subject):
        from openjiuwen.harness.security.permission_engine.approve.command_identity import literal_command_identity
        from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import split_shell_for_grants

        for command in split_shell_for_grants(subject["command"]):
            identity = literal_command_identity(command, subject["cwd"], subject["shell_type"])
            if identity and identity["accesses"]:
                resources.append(({"kind": "file", "accesses": identity["accesses"]}, "exact"))
    for resource, resource_scope in resources:
        grants.append(_file_resource_receipt(resource, resource_scope))
    return grants


def _file_resource_receipt(resource: dict, scope: str) -> dict:
    if scope == "parent":
        resource = {**resource, "accesses": sorted({(str(Path(p).parent), a) for p, a in resource["accesses"]})}
    pattern = _json({"scope": scope, "subject": resource})
    return {"id": "resource_" + hashlib.sha256(pattern.encode()).hexdigest()[:24],
            "tools": ["file_resource"], "match_type": GRANT_MATCH_TYPE,
            "pattern": pattern, "action": "allow"}


def has_file_resource_grant(config: Mapping, subject: dict) -> bool:
    """Only new resource receipts are shared across tools; no legacy widening."""
    if subject.get("kind") != "file" or not subject.get("accesses"):
        return False
    return all(any(isinstance(grant, dict) and grant.get("tools") == ["file_resource"]
                   and _resource_atom_matches(grant, path, action)
                   for grant in config.get("approval_overrides", []) or [])
               for path, action in subject["accesses"])


def _resource_atom_matches(grant: dict, path: str, action: str) -> bool:
    atom = {"kind": "file", "accesses": [(path, action)]}
    if matches_subject(grant, atom):
        return True
    try:
        payload = json.loads(grant["pattern"])
        return (grant.get("action") == "allow" and grant.get("match_type") == GRANT_MATCH_TYPE
                and payload["scope"] == "exact" and payload["subject"]["kind"] == "file"
                and (os.path.normcase(path), action) in {
                    (os.path.normcase(p), a) for p, a in payload["subject"]["accesses"]})
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def has_shared_file_operation(config: Mapping, tool: str, args: dict, workspace: Path | None = None) -> bool:
    """Match only fully identified file effects, independently of the tool."""
    subject = operation_subject(tool, args, config, workspace)
    # A resource receipt cannot waive a command-level ASK for changed flags,
    # content or executable. Runtime file effects are checked independently.
    return has_file_resource_grant(config, subject)


def has_shell_subcommand_grant(config: Mapping, tool: str, args: dict) -> bool:
    """Match one literal child; never expose these grants to the final override."""
    grants = config.get("approval_overrides") or []
    subject = operation_subject(tool, args, config)
    return any(
        isinstance(g, dict) and (
            (g.get("match_type") == SHELL_GRANT_MATCH_TYPE
             and matches_operation_grant({**g, "match_type": GRANT_MATCH_TYPE}, tool, subject))
            or _runtime_command_matches(g, subject))
        for g in grants
    )


def _runtime_command_matches(grant: dict, subject: dict) -> bool:
    """A new native program receipt lifts only the command layer's ASK."""
    if grant.get("tools") != ["dll_runtime_program"] or grant.get("match_type") != GRANT_MATCH_TYPE:
        return False
    if grant.get("action") != "allow" or not subject.get("command_identity"):
        return False
    try:
        payload = json.loads(grant["pattern"])
        approved = payload["subject"]
        if (payload["scope"] != "exact" or approved["kind"] != "program"
                or approved.get("command_identity", {}).get("version") != 2):
            return False
        command = {"kind": "command", "cwd": approved["cwd"], "command_identity": approved["command_identity"]}
        return _json(_exact_subject(command)) == _json(_exact_subject(subject))
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def matches_operation_grant(grant: dict, tool: str, subject: dict) -> bool:
    """Match an operation grant bound to the invoking tool."""
    if grant.get("tools") != [tool]:
        return False
    return matches_subject(grant, subject)


def matches_subject(grant: dict, subject: dict) -> bool:
    """Match resource scope only; the caller validates the grant's source."""
    if grant.get("match_type") != GRANT_MATCH_TYPE or grant.get("action") != "allow":
        return False
    try:
        payload = json.loads(grant["pattern"])
        approved, scope = payload["subject"], payload["scope"]
        if approved["kind"] != subject["kind"]:
            return False
        if scope == "exact":
            if approved["kind"] == "program" and approved.get("command_identity", {}).get("version") == 2:
                from openjiuwen.harness.security.permission_engine.approve.command_identity import (
                    native_command_identity,
                )

                identity = native_command_identity(subject["exe"], subject["argv"], subject["cwd"],
                                                   approved["command_identity"]["dialect"])
                if identity is None:
                    return False
                subject = {**subject, "command_identity": identity}
            if approved["kind"] == "command" and approved.get("command_identity", {}).get("version") != 2:
                # Existing receipts retain literal reuse, without silently
                # acquiring the new semantic scope or shared file effects.
                subject = {key: value for key, value in subject.items()
                           if key not in {"file_command", "command_identity"}}
            return _json(_exact_subject(approved)) == _json(_exact_subject(subject))
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
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
    return False


def has_operation_grant(config: Mapping, tool: str, args: dict, workspace: Path | None = None,
                        *, allow_semantic: bool = True) -> bool:
    """Match tool-bound receipts, optionally excluding semantic command reuse."""
    grants = config.get("approval_overrides") or []
    if not any(isinstance(g, dict) and g.get("match_type") == GRANT_MATCH_TYPE for g in grants):
        return False
    subject = operation_subject(tool, args, config, workspace)
    for grant in grants:
        if not isinstance(grant, dict) or not matches_operation_grant(grant, tool, subject):
            continue
        if not allow_semantic and subject.get("command_identity"):
            try:
                approved = json.loads(grant["pattern"])["subject"]
                if approved.get("command_identity") or approved.get("command") != subject["command"]:
                    continue
            except (KeyError, TypeError, ValueError):
                continue
        return True
    return False
