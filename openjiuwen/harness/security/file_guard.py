# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""文件路径防护（Pipeline B）：``file_guard`` + ExternalDirectory Legacy 投影.

独立于工具级 tiered_policy（Pipeline A）。关闭 ``file_guard.enabled`` 时整层路径
防护不参与判定（含旧 ``external_directory`` 等价检查）。

配置编译见 :func:`normalize_path_guard_config`（§5.5）；判定入口为
:class:`FileGuardChecker`。
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openjiuwen.harness.security.models import PermissionLevel, PermissionResult
from openjiuwen.harness.security.patterns import contains_path
from openjiuwen.harness.security.tiered_policy import (
    _PATH_TOOLS,
    _iter_path_strings,
    strictest as tiered_strictest,
)

logger = logging.getLogger(__name__)

FileGuardMode = Literal["legacy", "native"]
FileGuardMatch = Literal["prefix", "glob"]
FileGuardAction = Literal["read", "write", "exec"]

_WRITE_PATH_TOOLS = frozenset({
    "write_file", "edit_file", "write_text_file", "write", "search_replace",
})

_PATH_AWARE_COMMANDS = frozenset({
    "cd", "rm", "cp", "mv", "mkdir", "touch", "chmod", "chown", "cat",
    "ls", "dir", "type", "del", "rd", "copy", "move", "md",
    "head", "tail", "more", "less", "vim", "nano", "gedit", "notepad",
})

_LEVEL_ORDER = {
    PermissionLevel.DENY: 0,
    PermissionLevel.ASK: 1,
    PermissionLevel.ALLOW: 2,
}


@dataclass(frozen=True)
class FileGuardAxisDefaults:
    read: PermissionLevel
    write: PermissionLevel
    exec: PermissionLevel


@dataclass(frozen=True)
class FileGuardPathRule:
    path: str
    read: PermissionLevel
    write: PermissionLevel
    exec: PermissionLevel
    match: FileGuardMatch = "prefix"


@dataclass(frozen=True)
class EffectiveFileGuardConfig:
    """加载期编译后的路径防护生效视图（不写回调用方 dict）。"""

    enabled: bool
    mode: FileGuardMode
    defaults: FileGuardAxisDefaults
    paths: tuple[FileGuardPathRule, ...]
    workspace_root: Path | None


def _parse_level(raw: Any, default: PermissionLevel = PermissionLevel.ASK) -> PermissionLevel:
    if isinstance(raw, PermissionLevel):
        return raw
    if isinstance(raw, str):
        v = raw.strip().lower()
        if v in ("allow", "ask", "deny"):
            return PermissionLevel(v)
    return default


def _axis_from_star(action: Any) -> FileGuardAxisDefaults:
    level = _parse_level(action, PermissionLevel.ASK)
    return FileGuardAxisDefaults(read=level, write=level, exec=level)


def _posix_norm(p: str | Path) -> str:
    try:
        return Path(p).expanduser().resolve().as_posix()
    except (OSError, RuntimeError):
        return str(p).replace("\\", "/").rstrip("/") or str(p).replace("\\", "/")


def _expand_raw(raw: str) -> str:
    return os.path.expandvars(os.path.expanduser(raw.strip()))


def _apply_implications(
    read: PermissionLevel,
    write: PermissionLevel,
    exec_: PermissionLevel,
    *,
    path_label: str,
) -> tuple[PermissionLevel, PermissionLevel, PermissionLevel]:
    """Write⇒Read / Exec⇒Read；显式更严的 read（deny）优先于蕴含，并 WARN。"""
    if write == PermissionLevel.ALLOW or exec_ == PermissionLevel.ALLOW:
        if read == PermissionLevel.DENY:
            logger.warning(
                "[file_guard] implication.conflict path=%s write=%s exec=%s read=deny "
                "(explicit deny wins over Write/Exec⇒Read)",
                path_label,
                write.value,
                exec_.value,
            )
            return read, write, exec_
        return PermissionLevel.ALLOW, write, exec_
    return read, write, exec_


def _compile_path_entry(
    path_raw: str,
    *,
    read: Any = None,
    write: Any = None,
    exec_: Any = None,
    match: FileGuardMatch = "prefix",
    default_level: PermissionLevel = PermissionLevel.ASK,
) -> FileGuardPathRule | None:
    path_s = str(path_raw).strip()
    if not path_s or path_s == "*":
        return None
    r = _parse_level(read, default_level) if read is not None else default_level
    w = _parse_level(write, default_level) if write is not None else default_level
    e = _parse_level(exec_, default_level) if exec_ is not None else default_level
    r, w, e = _apply_implications(r, w, e, path_label=path_s)
    if match == "prefix":
        # 过短前缀（无 /）跳过，避免 "C:" 误匹配整盘（对齐 ExternalDirectory）
        norm = path_s.replace("\\", "/").rstrip("/")
        if "/" not in norm:
            return None
        try:
            path_s = _posix_norm(_expand_raw(path_s))
        except (OSError, RuntimeError, ValueError):
            path_s = norm
        if "/" not in path_s.rstrip("/"):
            return None
    return FileGuardPathRule(path=path_s, read=r, write=w, exec=e, match=match)


def _has_axis_dict(raw: Any) -> bool:
    return isinstance(raw, dict) and any(k in raw for k in ("read", "write", "exec"))


def _has_native_file_guard(fg: Mapping[str, Any], permissions: Mapping[str, Any]) -> bool:
    """判定是否进入 Native 语义。

    - 显式 ``defaults.read/write/exec`` → Native
    - 显式 ``workspace.read/write/exec`` → Native（路径由运行时 workspace_root 绑定）
    - ``paths`` 含 ``match: glob`` → Native
    - 仅有 ``paths`` 且**没有**旧 ``external_directory`` → Native
    - 仅有 ``paths``（如 /add-dir 写入）且仍有 ``external_directory`` → 保持 Legacy，
      把 paths 编译进 Legacy 规则，避免误取消 workspace 隐式放行
    """
    if _has_axis_dict(fg.get("defaults")) or _has_axis_dict(fg.get("workspace")):
        return True
    paths = fg.get("paths")
    if not isinstance(paths, list) or not paths:
        return False
    if any(isinstance(p, dict) and p.get("match") == "glob" for p in paths):
        return True
    ext = permissions.get("external_directory")
    if isinstance(ext, (dict, str)) and bool(ext):
        return False
    return True


def _compile_workspace_axis_rule(
    workspace_cfg: Any,
    *,
    workspace_root: Path | None,
) -> FileGuardPathRule | None:
    """把 ``file_guard.workspace`` 编译为绑定 ``workspace_root`` 的前缀规则。

    路径不写在 YAML 里，由 ``resolve_workspace_dir()`` / 引擎构造时的
    ``workspace_root`` 提供。拿不到 root 时 WARN 并跳过（不应用该规则）。
    """
    if not _has_axis_dict(workspace_cfg):
        return None
    if workspace_root is None:
        logger.warning(
            "[file_guard] workspace.rule_skipped reason=no_workspace_root "
            "(file_guard.workspace is set but workspace_root was not resolved)",
        )
        return None
    if not isinstance(workspace_cfg, dict):
        return None
    return _compile_path_entry(
        str(workspace_root),
        read=workspace_cfg.get("read"),
        write=workspace_cfg.get("write"),
        exec_=workspace_cfg.get("exec"),
        match="prefix",
        default_level=PermissionLevel.ASK,
    )


def _trusted_dir_axes(fg: Mapping[str, Any]) -> tuple[str, str, str]:
    """trusted_dirs 跟随 ``file_guard.workspace`` 轴；缺省保持历史 read/write allow。"""
    workspace_cfg = fg.get("workspace") if isinstance(fg.get("workspace"), dict) else {}
    read = str(workspace_cfg.get("read") or "allow").strip().lower() or "allow"
    write = str(workspace_cfg.get("write") or "allow").strip().lower() or "allow"
    exec_ = str(workspace_cfg.get("exec") or "ask").strip().lower() or "ask"
    return read, write, exec_


def _explicit_enabled(fg: Mapping[str, Any] | None) -> bool | None:
    if not isinstance(fg, dict) or "enabled" not in fg:
        return None
    return bool(fg.get("enabled"))


def normalize_path_guard_config(
    permissions: Mapping[str, Any] | None,
    *,
    workspace_root: Path | None = None,
    trusted_dirs: Sequence[Path] | None = None,
) -> EffectiveFileGuardConfig:
    """把 ``permissions`` + 运行时 ``trusted_dirs`` 编译为生效配置（只读副本语义）。"""
    perms = permissions if isinstance(permissions, Mapping) else {}
    fg_raw = perms.get("file_guard")
    fg: dict[str, Any] = dict(fg_raw) if isinstance(fg_raw, dict) else {}
    trusted = list(trusted_dirs or [])
    ext = perms.get("external_directory")
    has_ext = isinstance(ext, (dict, str)) and bool(ext)
    has_trusted = bool(trusted)

    explicit = _explicit_enabled(fg)
    if explicit is not None:
        enabled = explicit
    elif has_ext or has_trusted:
        enabled = True
    else:
        enabled = False

    ws = Path(workspace_root).resolve() if workspace_root is not None else None
    native = _has_native_file_guard(fg, perms)

    if not enabled:
        return EffectiveFileGuardConfig(
            enabled=False,
            mode="native" if native else "legacy",
            defaults=FileGuardAxisDefaults(
                read=PermissionLevel.ASK,
                write=PermissionLevel.ASK,
                exec=PermissionLevel.ASK,
            ),
            paths=(),
            workspace_root=ws,
        )

    if native:
        return _normalize_native(fg, ext=ext, workspace_root=ws, trusted_dirs=trusted)

    return _normalize_legacy(
        ext=ext,
        workspace_root=ws,
        trusted_dirs=trusted,
        file_guard_paths=fg.get("paths") if isinstance(fg.get("paths"), list) else None,
    )


def _normalize_legacy(
    *,
    ext: Any,
    workspace_root: Path | None,
    trusted_dirs: Sequence[Path],
    file_guard_paths: list[Any] | None = None,
) -> EffectiveFileGuardConfig:
    star_action: Any = "ask"
    allow_prefixes: list[tuple[str, str]] = []  # (path, action)
    if isinstance(ext, str):
        star_action = ext
    elif isinstance(ext, dict):
        star_action = ext.get("*", "ask")
        for key, action in ext.items():
            if key == "*" or not isinstance(key, str):
                continue
            if action not in ("allow", "ask", "deny"):
                continue
            allow_prefixes.append((key, str(action)))

    defaults = _axis_from_star(star_action)
    rules: list[FileGuardPathRule] = []

    # 隐式 workspace 全放行（仅 Legacy）
    if workspace_root is not None:
        ws_rule = _compile_path_entry(
            str(workspace_root),
            read="allow",
            write="allow",
            exec_="allow",
            match="prefix",
        )
        if ws_rule is not None:
            rules.append(ws_rule)

    for td in trusted_dirs:
        rule = _compile_path_entry(
            str(td),
            read="allow",
            write="allow",
            exec_="ask",
            match="prefix",
        )
        if rule is not None:
            rules.append(rule)

    for key, action in allow_prefixes:
        if action == "allow":
            rule = _compile_path_entry(
                key, read="allow", write="allow", exec_="ask", match="prefix",
            )
        else:
            rule = _compile_path_entry(
                key, read=action, write=action, exec_=action, match="prefix",
            )
        if rule is not None:
            rules.append(rule)

    # /add-dir 等写入的 file_guard.paths（无 glob）并入 Legacy
    if file_guard_paths:
        for item in file_guard_paths:
            if not isinstance(item, dict):
                continue
            if item.get("match") == "glob":
                continue
            path_v = item.get("path")
            if not isinstance(path_v, str) or not path_v.strip():
                continue
            rule = _compile_path_entry(
                path_v,
                read=item.get("read", "allow"),
                write=item.get("write", "allow"),
                exec_=item.get("exec", "ask"),
                match="prefix",
            )
            if rule is not None:
                rules.append(rule)

    return EffectiveFileGuardConfig(
        enabled=True,
        mode="legacy",
        defaults=defaults,
        paths=tuple(rules),
        workspace_root=workspace_root,
    )


def _normalize_native(
    fg: Mapping[str, Any],
    *,
    ext: Any,
    workspace_root: Path | None,
    trusted_dirs: Sequence[Path],
) -> EffectiveFileGuardConfig:
    raw_defaults = fg.get("defaults") if isinstance(fg.get("defaults"), dict) else {}
    # 若未写 defaults，可从 external_directory["*"] 迁移
    if not any(k in raw_defaults for k in ("read", "write", "exec")) and isinstance(ext, dict):
        defaults = _axis_from_star(ext.get("*", "ask"))
    elif not any(k in raw_defaults for k in ("read", "write", "exec")) and isinstance(ext, str):
        defaults = _axis_from_star(ext)
    else:
        defaults = FileGuardAxisDefaults(
            read=_parse_level(raw_defaults.get("read"), PermissionLevel.ASK),
            write=_parse_level(raw_defaults.get("write"), PermissionLevel.ASK),
            exec=_parse_level(raw_defaults.get("exec"), PermissionLevel.ASK),
        )

    rules: list[FileGuardPathRule] = []
    raw_paths = fg.get("paths")
    if isinstance(raw_paths, list):
        for item in raw_paths:
            if not isinstance(item, dict):
                continue
            path_v = item.get("path")
            if not isinstance(path_v, str) or not path_v.strip():
                continue
            match_v = item.get("match", "prefix")
            match: FileGuardMatch = "glob" if match_v == "glob" else "prefix"
            rule = _compile_path_entry(
                path_v,
                read=item.get("read"),
                write=item.get("write"),
                exec_=item.get("exec"),
                match=match,
                default_level=PermissionLevel.ASK,
            )
            if rule is not None:
                rules.append(rule)

    # file_guard.workspace → 绑定运行时 workspace_root 的前缀规则
    ws_rule = _compile_workspace_axis_rule(fg.get("workspace"), workspace_root=workspace_root)
    if ws_rule is not None:
        rules.append(ws_rule)

    # 运行时 trusted_dirs：与当前 mode 的 workspace 轴一致（选中工作目录）
    td_read, td_write, td_exec = _trusted_dir_axes(fg)
    for td in trusted_dirs:
        rule = _compile_path_entry(
            str(td), read=td_read, write=td_write, exec_=td_exec, match="prefix",
        )
        if rule is not None:
            rules.append(rule)

    # 旧 external_directory 具名键作迁移源（不覆盖已有 paths）
    if isinstance(ext, dict):
        existing = {r.path for r in rules if r.match == "prefix"}
        for key, action in ext.items():
            if key == "*" or not isinstance(key, str) or action not in ("allow", "ask", "deny"):
                continue
            try:
                key_norm = _posix_norm(_expand_raw(key))
            except (OSError, RuntimeError, ValueError):
                key_norm = key.replace("\\", "/")
            if key_norm in existing:
                continue
            if action == "allow":
                rule = _compile_path_entry(
                    key, read="allow", write="allow", exec_="ask", match="prefix",
                )
            else:
                rule = _compile_path_entry(
                    key, read=action, write=action, exec_=action, match="prefix",
                )
            if rule is not None:
                rules.append(rule)
                logger.warning(
                    "[file_guard] migrate.external_directory key=%s action=%s → file_guard.paths",
                    key,
                    action,
                )

    return EffectiveFileGuardConfig(
        enabled=True,
        mode="native",
        defaults=defaults,
        paths=tuple(rules),
        workspace_root=workspace_root,
    )


# ---------- Legacy 路径抽取（develop；禁止与 Native L1 混用） ----------


def _looks_like_path(token: str) -> bool:
    if token.startswith(("\\\\", "./", "../")):
        return True
    if re.match(r"^[A-Za-z]:[\\/]", token):
        return True
    return "\\" in token or "/" in token


def _extract_paths_from_command(command: str, workdir: str | Path) -> list[Path]:
    if not command or not isinstance(command, str):
        return []
    try:
        tokens = shlex.split(command.strip(), posix=False)
    except ValueError:
        tokens = command.strip().split()
    if not tokens:
        return []
    cmd = tokens[0].lower()
    if cmd not in _PATH_AWARE_COMMANDS:
        return []
    base = Path(workdir).resolve()
    paths: list[Path] = []
    for tok in tokens[1:]:
        tok = tok.strip().strip('"').strip("'")
        if not tok or tok.startswith("-"):
            continue
        if not _looks_like_path(tok):
            continue
        p = Path(tok)
        if not p.is_absolute():
            p = base / tok
        try:
            paths.append(p.resolve())
        except (OSError, RuntimeError):
            continue
    return paths


def extract_paths_legacy(
    tool_name: str,
    tool_args: Mapping[str, Any],
    workspace: Path,
) -> list[Path]:
    """develop 抽取：仅路径字符串，无 R/W/X（供 Legacy 投影锁定现网行为）。"""
    paths: list[Path] = []
    if tool_name in ("mcp_exec_command", "bash", "create_terminal", "powershell"):
        workdir = tool_args.get("workdir", "")
        try:
            workdir_resolved = (workspace / str(workdir)).resolve()
        except (OSError, RuntimeError):
            workdir_resolved = workspace
        cmd = str(tool_args.get("command", "") or tool_args.get("cmd", ""))
        paths = _extract_paths_from_command(cmd, workdir_resolved)
    elif tool_name in _PATH_TOOLS:
        for s in _iter_path_strings(tool_name, tool_args):
            raw = s.strip().strip('"').strip("'")
            if not raw:
                continue
            try:
                p = Path(raw)
                if not p.is_absolute():
                    p = (workspace / p).resolve()
                else:
                    p = p.resolve()
                paths.append(p)
            except (OSError, RuntimeError):
                continue
    return paths


def _tool_default_action(tool_name: str) -> FileGuardAction:
    if tool_name in _WRITE_PATH_TOOLS:
        return "write"
    if tool_name in ("mcp_exec_command", "bash", "create_terminal", "powershell"):
        # Legacy 不区分 exec；路径访问按 read 轴（与 ExternalDirectory 无轴一致，用同 defaults）
        return "read"
    return "read"


def _match_glob(pattern: str, path_posix: str) -> bool:
    """简单 glob：支持 ``**`` / ``*`` / ``?``（路径用 posix）。"""
    # 转成正则
    i = 0
    out: list[str] = []
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    try:
        flags = re.IGNORECASE if sys.platform == "win32" else 0
        return bool(re.fullmatch("".join(out), path_posix, flags=flags))
    except re.error:
        return False


def _level_for_action(rule: FileGuardPathRule, action: FileGuardAction) -> PermissionLevel:
    if action == "write":
        return rule.write
    if action == "exec":
        return rule.exec
    return rule.read


class FileGuardChecker:
    """路径防护判定器（Legacy / Native 共用入口）。"""

    def __init__(self, effective: EffectiveFileGuardConfig):
        self._effective = effective

    @property
    def enabled(self) -> bool:
        return self._effective.enabled

    @property
    def mode(self) -> FileGuardMode:
        return self._effective.mode

    def evaluate(
        self,
        tool_name: str,
        tool_args: Mapping[str, Any],
    ) -> PermissionResult | None:
        """评估路径层；无意见或全部 ALLOW 时返回 ``None``（不抬升 Pipeline A）。"""
        if not self._effective.enabled:
            return None
        workspace = self._effective.workspace_root
        if workspace is None:
            logger.debug("[file_guard] skip reason=no_workspace")
            return None

        accesses: list[tuple[Path, FileGuardAction]]
        if self._effective.mode == "native":
            from openjiuwen.harness.security.files.extract import extract_accesses_native

            raw = extract_accesses_native(tool_name, dict(tool_args), workspace)
            accesses = [(p, act) for p, act, _src in raw]
        else:
            paths = extract_paths_legacy(tool_name, dict(tool_args), workspace)
            action = _tool_default_action(tool_name)
            accesses = [(p, action) for p in paths]

        if not accesses:
            return None

        overall = PermissionLevel.ALLOW
        hit_external: list[str] = []
        matched_bits: list[str] = []

        for p, action in accesses:
            level, rule_id = self._resolve_one(p, action)
            overall = tiered_strictest(overall, level)
            if level != PermissionLevel.ALLOW:
                hit_external.append(p.as_posix())
                if rule_id:
                    matched_bits.append(rule_id)

        if overall == PermissionLevel.ALLOW:
            return None

        if self._effective.mode == "legacy":
            sample = accesses[0][0]
            if overall == PermissionLevel.DENY:
                reason = f"Access to paths outside workspace is denied: {sample}"
            else:
                reason = f"Access to paths outside workspace requires approval: {sample}"
            matched = "external_directory.*"
        else:
            hint = hit_external[0] if hit_external else accesses[0][0].as_posix()
            reason = (
                f"file_guard denied: {hint}"
                if overall == PermissionLevel.DENY
                else f"file_guard requires approval: {hint}"
            )
            matched = "|".join(matched_bits) if matched_bits else "file_guard"

        return PermissionResult(
            permission=overall,
            reason=reason,
            matched_rule=matched,
            external_paths=hit_external or None,
        )

    def collect_ask_accesses(
        self,
        tool_name: str,
        tool_args: Mapping[str, Any],
    ) -> list[tuple[str, FileGuardAction]]:
        """返回本次判定为 ASK 的 ``(path_posix, action)``，供 HITL「总是允许」按轴落盘。

        路径为实际触达路径（不再上卷父目录）；仅包含 ASK，不含 DENY/ALLOW。
        """
        if not self._effective.enabled:
            return []
        workspace = self._effective.workspace_root
        if workspace is None:
            return []

        if self._effective.mode == "native":
            from openjiuwen.harness.security.files.extract import extract_accesses_native

            raw = extract_accesses_native(tool_name, dict(tool_args), workspace)
            accesses = [(p, act) for p, act, _src in raw]
        else:
            paths = extract_paths_legacy(tool_name, dict(tool_args), workspace)
            action = _tool_default_action(tool_name)
            accesses = [(p, action) for p in paths]

        out: list[tuple[str, FileGuardAction]] = []
        seen: set[tuple[str, FileGuardAction]] = set()
        for p, action in accesses:
            level, _rule_id = self._resolve_one(p, action)
            if level != PermissionLevel.ASK:
                continue
            key = (p.as_posix(), action)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
        return out

    def _resolve_one(
        self,
        path: Path,
        action: FileGuardAction,
    ) -> tuple[PermissionLevel, str | None]:
        path_posix = path.as_posix()
        best_prefix: tuple[int, FileGuardPathRule] | None = None
        scored: list[tuple[PermissionLevel, str]] = []

        for rule in self._effective.paths:
            if rule.match == "glob":
                if _match_glob(rule.path.replace("\\", "/"), path_posix):
                    scored.append(
                        (
                            _level_for_action(rule, action),
                            f"file_guard:glob:{rule.path}",
                        )
                    )
                continue
            # prefix
            prefix = rule.path.rstrip("/")
            if path_posix == prefix or path_posix.startswith(prefix + "/"):
                # also accept contains_path for Windows drive quirks
                if contains_path(prefix, path) or path_posix == prefix or path_posix.startswith(prefix + "/"):
                    ln = len(prefix)
                    if best_prefix is None or ln > best_prefix[0]:
                        best_prefix = (ln, rule)

        if best_prefix is not None:
            scored.append(
                (
                    _level_for_action(best_prefix[1], action),
                    f"file_guard:prefix:{best_prefix[1].path}",
                )
            )

        if scored:
            # deny > ask > allow；同级时优先 glob（敏感路径），避免误报 workspace 前缀
            level, rule_id = min(
                scored,
                key=lambda item: (
                    _LEVEL_ORDER[item[0]],
                    0 if item[1].startswith("file_guard:glob:") else 1,
                ),
            )
            return level, rule_id

        # 未命中 paths → defaults
        defaults = self._effective.defaults
        if action == "write":
            return defaults.write, "file_guard:defaults"
        if action == "exec":
            return defaults.exec, "file_guard:defaults"
        return defaults.read, "file_guard:defaults"


def build_file_guard_checker(
    permissions: Mapping[str, Any] | None,
    *,
    workspace_root: Path | None = None,
    trusted_dirs: Sequence[Path] | None = None,
) -> FileGuardChecker | None:
    """编译配置；若路径层关闭则返回 ``None``。"""
    perms = permissions if isinstance(permissions, Mapping) else {}
    report_legacy_path_rules_at_load(perms)
    effective = normalize_path_guard_config(
        perms,
        workspace_root=workspace_root,
        trusted_dirs=trusted_dirs,
    )
    if not effective.enabled:
        return None
    return FileGuardChecker(effective)


_PATH_CLASS_TOOLS = frozenset({
    "read_file", "write_file", "edit_file",
    "read_text_file", "write_text_file",
    "write", "read",
    "glob_file_search", "glob", "list_dir", "list_files",
    "grep", "search_replace",
    "send_file_to_user",
})

_LOAD_WARNED_RULE_IDS: set[str] = set()


def _looks_like_path_pattern(pattern: str) -> bool:
    if not pattern or not pattern.strip():
        return False
    s = pattern.strip()
    if "/" in s or "\\" in s or "**" in s:
        return True
    if s.startswith(("~", "$", "%")):
        return True
    if len(s) > 1 and s[1] == ":":
        return True
    return False


def report_legacy_path_rules_at_load(permissions: Mapping[str, Any]) -> list[str]:
    """加载期 WARN：tools 全为 path 类且 pattern 像路径的 ``rules`` 宜迁到 ``file_guard.paths``。"""
    rules = permissions.get("rules")
    if not isinstance(rules, list):
        return []
    flagged: list[str] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rid = str(rule.get("id") or "").strip()
        tools = rule.get("tools")
        pattern = rule.get("pattern")
        if not isinstance(tools, list) or not isinstance(pattern, str):
            continue
        if not tools or not all(isinstance(t, str) and t in _PATH_CLASS_TOOLS for t in tools):
            continue
        if not _looks_like_path_pattern(pattern):
            continue
        flagged.append(rid)
        cache_key = rid or f"__anon__:{pattern}"
        if cache_key in _LOAD_WARNED_RULE_IDS:
            continue
        _LOAD_WARNED_RULE_IDS.add(cache_key)
        logger.warning(
            "[file_guard] permissions.rules.path_class_deprecated id=%s tools=%s pattern=%r "
            "hint=migrate to permissions.file_guard.paths (match=glob|prefix)",
            rid or "<no-id>",
            tools,
            pattern,
        )
    return flagged


__all__ = [
    "EffectiveFileGuardConfig",
    "FileGuardAxisDefaults",
    "FileGuardChecker",
    "FileGuardPathRule",
    "build_file_guard_checker",
    "extract_paths_legacy",
    "normalize_path_guard_config",
    "report_legacy_path_rules_at_load",
]
