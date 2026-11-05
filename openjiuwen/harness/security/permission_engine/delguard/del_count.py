# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""批量删除计数估算器（Pipeline D，删除数量防护）。

在 shell 类工具执行前，对命令里的删除目标做静态解析 + 文件系统递归计数，
用于把「一次调用删除大量文件」抬升为 ASK。

计数语义（对齐设计文档 §4.2）：

- 普通文件 / 符号链接 / 目录连接点（junction）：计 1（不追踪指向，指向目录也按 1 计）。
  唯一例外是操作数带尾部分隔符（``rm -rf link/``）：POSIX 与 PowerShell 此时强制
  按目录解析并跟随链接，实际删除目标目录的内容，因此对目标目录递归计数。
- 目录：不统计目录节点自身，递归统计其内部的「文件 + 链接 + 子目录内容」。
- 空目录：计 0。
- 同一调用内多条删除命令（``rm a && rm -rf b``、``Remove-Item a; Remove-Item b``）
  的计数累加，并按绝对路径去重。路径只做规范化和拼接，不调用 ``resolve()``：
  解析会把链接替换成目标，导致链接身份丢失、目标目录被展开计数。
"""

from __future__ import annotations

import glob as _glob
import os
import re
import shlex
import stat as _stat
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from openjiuwen.harness.security.permission_engine.toolguard.command_canonicalize import (
    canonicalize_shell_command_for_permission,
)
from openjiuwen.harness.security.permission_engine.toolguard.shell_ast import (
    parse_shell_for_permission,
)

# 删除命令按 shell 归类。
_BASH_DELETE_CMDS = frozenset({"rm", "unlink"})
_CMD_DELETE_CMDS = frozenset({"del", "erase", "rd", "rmdir"})
_PS_DELETE_CMDS = frozenset({"remove-item", "ri", "rm", "del", "erase", "rd", "rmdir"})

# 改变后续命令工作目录的命令。``cd a && rm x`` 的 x 必须按 a 解析。
_BASH_CD_CMDS = frozenset({"cd"})
_CMD_CD_CMDS = frozenset({"cd", "chdir"})
_PS_CD_CMDS = frozenset({"cd", "chdir", "set-location", "sl"})

# 候选工作目录上限。超过后不再接受新的 cd 结果，只按已知目录计数，避免扫描失控。
_MAX_CWD_CANDIDATES = 8

# ReFS/NTFS 的目录连接点（junction）不一定是 S_ISLNK，必须看 reparse 属性。
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(_stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

# 递归 / 强制标志只影响语义，不影响目标抽取；这里保留用于注释说明，不参与过滤。
_RECURSIVE_FLAGS = frozenset({"-r", "-r", "-rf", "-fr", "-R", "--recursive", "-recurse", "/s"})

# 拆分链式命令：`;`、`&&`、`||`、`|`、以及幂等断开用的换行。
# 管道右侧同样是真实执行的命令，不切分会漏掉其中的删除目标。
_SEGMENT_SPLIT_RE = re.compile(r"(?:\r?\n|&&|\|\||;|\|)")

# 未展开变量外壳（例如 ``$x`` / ``${x}`` / ``%VAR%``）。
_UNEXPANDED_VAR_RE = re.compile(r"\$(\w+|\{[^}]+\})|%\w+%")

# ``'a','b'`` / ``"a","b"``：引号之间的逗号才是 PowerShell 列表分隔，引号内逗号不是。
_QUOTED_LIST_GAP_RE = re.compile(r"""['"]\s*,\s*['"]""")


def _basename_lower(cmd: str) -> str:
    base = Path(cmd.replace("\\", "/")).name.lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base


def _delete_cmds_for(shell_type: str) -> frozenset[str]:
    st = (shell_type or "auto").strip().lower()
    if st in {"cmd", "dos"}:
        return _CMD_DELETE_CMDS
    if st in {"powershell", "pwsh", "ps"}:
        return _PS_DELETE_CMDS
    return _BASH_DELETE_CMDS | _CMD_DELETE_CMDS | _PS_DELETE_CMDS


def _cd_cmds_for(shell_type: str) -> frozenset[str]:
    st = (shell_type or "auto").strip().lower()
    if st in {"cmd", "dos"}:
        return _CMD_CD_CMDS
    if st in {"powershell", "pwsh", "ps"}:
        return _PS_CD_CMDS
    return _BASH_CD_CMDS | _CMD_CD_CMDS | _PS_CD_CMDS


def _segments(command: str) -> list[str]:
    """把复合命令拆成独立命令段，优先树 sitter，退化为分隔符启发式。"""
    text = canonicalize_shell_command_for_permission((command or "").strip())
    if not text:
        return []
    parsed = parse_shell_for_permission(text)
    subs = [sc.text.strip() for sc in (parsed.subcommands or ()) if sc.text and sc.text.strip()]
    if len(subs) >= 2:
        return subs
    heuristic = [p.strip() for p in _SEGMENT_SPLIT_RE.split(text) if p.strip()]
    if len(heuristic) >= 2:
        return heuristic
    if subs:
        return subs
    return [text]


def _split_tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment.strip(), posix=False)
    except ValueError:
        return segment.strip().split()


def _is_flag(token: str, *, shell_type: str = "") -> bool:
    """``-r`` / ``--force`` 始终是旗标。

    ``/f``、``/s``、``/q`` 这种单词开关只在 cmd.exe 下丢弃。``/tmp``、``/etc``、
    ``/opt``、``/data`` 是单层 POSIX 路径，不能用同一条正则静默吞掉。
    """
    t = token.strip().strip('"').strip("'")
    if not t:
        return True
    if t.startswith("-"):
        return True
    if (shell_type or "").strip().lower() in {"cmd", "dos"} and re.match(
        r"^/[A-Za-z][A-Za-z0-9]*$", t
    ):
        return True
    return False


def _peel_shlex_comma(token: str) -> str:
    """去掉 ``shlex(posix=False)`` 粘在下一参数上的前导逗号（``,'b'``）。"""
    raw = token.strip()
    while raw.startswith(","):
        nxt = raw[1:].lstrip()
        if not nxt:
            return ""
        if (
            nxt[0] in "\"'"
            or re.match(r"^[A-Za-z]:[\\/]", nxt)
            or nxt.startswith(("\\\\", "./", "../", "/", "\\"))
        ):
            raw = nxt
            continue
        break
    return raw


def _split_unquoted_commas(token: str) -> list[str]:
    """按引号外的逗号拆分；引号内逗号保留（``'foo,bar.txt'`` 仍是一个路径）。"""
    parts: list[str] = []
    buf: list[str] = []
    quote = ""
    i = 0
    while i < len(token):
        ch = token[i]
        if quote:
            if quote == "'" and token.startswith("''", i):
                buf.append("'")
                i += 2
                continue
            buf.append(ch)
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == ",":
            part = "".join(buf).strip()
            if part:
                parts.append(part)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def split_shell_operands(token: str, *, shell_type: str, cmd0: str) -> list[str]:
    """把一个 shell 词展开成删除/路径操作数。

    PowerShell 的 ``Remove-Item a,b`` 和 ``'a','b'`` 是多个目标。``shlex(posix=False)``
    会把逗号粘到后一个带引号参数上（``,'b'``），这里先剥掉再按引号外逗号拆开。
    bash 的普通 ``rm`` 不把文件名里的逗号当成分隔符。
    """
    if _is_flag(token, shell_type=shell_type):
        return []
    peeled = _peel_shlex_comma(token)
    if not peeled or _is_flag(peeled, shell_type=shell_type):
        return []
    st = (shell_type or "auto").strip().lower()
    split = (
        st in {"powershell", "pwsh", "ps"}
        or cmd0 in {"remove-item", "ri"}
        or bool(_QUOTED_LIST_GAP_RE.search(peeled))
    )
    if split and "," in peeled:
        return [
            part for part in _split_unquoted_commas(peeled)
            if part and not _is_flag(part, shell_type=shell_type)
        ]
    return [peeled]


def _absolute_path(raw: str, base: Path) -> Path:
    """把操作数拼成绝对路径。只做规范化，不解析链接（``resolve()`` 会跟随链接）。"""
    expanded = os.path.expandvars(os.path.expanduser(raw))
    p = Path(expanded)
    candidate = p if p.is_absolute() else base / p
    return Path(os.path.normpath(os.path.abspath(str(candidate))))


def _is_link_like(path: str | os.PathLike[str], info: os.stat_result | None = None) -> bool:
    """符号链接 / 目录连接点判定。不跟随目标；悬空链接也返回 True。"""
    try:
        info = info if info is not None else os.lstat(path)
    except OSError:
        return False
    if _stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT)


def _expand_target(raw: str, base: Path) -> list[Path]:
    """展开操作数：绝对/相对路径 + glob。返回实际存在的候选绝对路径。

    叶子路径一般保持原样（链接就是链接），不做 ``resolve()``：删除链接只删链接
    本体，把链接替换成目标会让计数对象和审批列表都变成另一个路径。带尾部分隔符
    的链接是例外，见下方分支注释。
    """
    raw = raw.strip().strip('"').strip("'")
    if not raw or _UNEXPANDED_VAR_RE.search(raw):
        return []
    # 尾部分隔符会被 _absolute_path 的 normpath 抹掉，先记下语义：POSIX rm 与
    # PowerShell Remove-Item 对 ``link/`` 强制按目录解析并跟随链接，删除的是
    # 目标目录的内容，不能与 ``rm -rf link``（只删链接本体）同路径同计数。
    trailing_sep = raw.endswith(("/", "\\"))
    expanded = os.path.expandvars(os.path.expanduser(raw))
    has_glob = any(c in expanded for c in ("*", "?", "["))
    try:
        candidate = _absolute_path(expanded, base)
    except (OSError, ValueError):
        return []

    if not has_glob:
        # ``lstat`` 能捕捉到悬空链接，``exists`` 不能。
        try:
            info = os.lstat(candidate)
        except OSError:
            return []
        if trailing_sep and _is_link_like(candidate, info):
            # 计数对象换成链接的目标目录：_entry_cost 对目录递归计内容数，
            # 与实际删除范围一致；否则只计 1 会让达阈值的批量删除静默放行。
            try:
                return [Path(os.path.realpath(candidate))]
            except OSError:
                return [candidate]
        return [candidate]

    # glob 特殊字符（* ? [）在 Windows 文件名中非法，出现即视为通配意图；
    # 直接以候选为 pattern 展开（不 escape/replace，避免破坏通配符）。
    try:
        matches = _glob.glob(str(candidate), recursive=True)
    except Exception:  # noqa: BLE001 - 任何 glob 异常都不应抬升误判
        return []
    out: list[Path] = []
    for m in matches:
        try:
            out.append(Path(os.path.normpath(os.path.abspath(m))))
        except (OSError, ValueError):
            continue
    return out


def _entry_cost(path: Path) -> int:
    """递归计数：文件/链接 = 1，目录不自身计，递归内部文件 + 链接 + 子目录。"""
    try:
        info = os.lstat(path)
    except OSError:
        return 0
    # 链接/连接点删的是本体，目标目录内容不会被删除，因此只计 1 且不展开。
    if _is_link_like(path, info):
        return 1
    if _stat.S_ISREG(info.st_mode):
        return 1
    if not _stat.S_ISDIR(info.st_mode):
        return 0
    cost = 0
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                try:
                    child_info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if _is_link_like(entry.path, child_info) or _stat.S_ISREG(child_info.st_mode):
                    cost += 1
                elif _stat.S_ISDIR(child_info.st_mode):
                    cost += _entry_cost(Path(entry.path))
                # 其它（套接字/FIFO/设备）不计
    except OSError:
        # 目录读了一部分就算已知部分，不要把已经数到的结果清零。
        return cost
    return cost


def _extend_cwds(
    cwds: set[Path],
    tokens: list[str],
    shell_type: str,
    cd_cmds: frozenset[str],
) -> None:
    """``cd <目录>`` 后把新目录加入候选集合。

    无法判断 ``cd`` 实际成功与否（``&&`` 失败即中止，``;``/``||`` 则会继续），
    因此只做并集，保留旧目录。这样只会多算，不会漏算删除目标。
    """
    cmd0 = _basename_lower(tokens[0].strip('"').strip("'"))
    if cmd0 not in cd_cmds:
        return
    operands = [tok for tok in tokens[1:] if not _is_flag(tok, shell_type=shell_type)]
    if len(operands) != 1:
        return
    raw = operands[0].strip().strip('"').strip("'")
    if not raw or _UNEXPANDED_VAR_RE.search(raw):
        return
    for cwd in list(cwds):
        if len(cwds) >= _MAX_CWD_CANDIDATES:
            return
        try:
            target = _absolute_path(raw, cwd)
        except (OSError, ValueError):
            continue
        if target.is_dir():
            cwds.add(target)


def count_deletion_targets(
    command: str,
    shell_type: str,
    workdir: str | Path | None,
) -> tuple[int, list[str]]:
    """估算一次 shell 调用删除的文件/符号链接数。

    Returns:
        ``(count, hit_paths)``：``count`` 为去重后的累计删除计数；``hit_paths``
        为命中的顶层目标绝对路径（供审批展示）。无法可靠估算时返回 ``(0, [])``，
        由调用方按 fail-open 处理。
    """
    if not command or not isinstance(command, str):
        return 0, []
    try:
        base = Path(workdir).resolve() if workdir else Path.cwd().resolve()
    except (OSError, ValueError):
        base = Path.cwd().resolve()

    delete_cmds = _delete_cmds_for(shell_type)
    cd_cmds = _cd_cmds_for(shell_type)
    seen: set[str] = set()
    hit_paths: list[str] = []
    # 每个命令段可能在不同目录下执行（``cd a && rm x``），按候选目录逐个展开；
    # 同一绝对路径只计一次，所以并集不会重复计数。
    cwds: set[Path] = {base}

    for segment in _segments(command):
        tokens = _split_tokens(segment)
        if not tokens:
            continue
        cmd0 = _basename_lower(tokens[0].strip('"').strip("'"))
        if cmd0 in cd_cmds:
            _extend_cwds(cwds, tokens, shell_type, cd_cmds)
            continue
        if cmd0 not in delete_cmds:
            continue
        operands: list[str] = []
        for tok in tokens[1:]:
            operands.extend(split_shell_operands(tok, shell_type=shell_type, cmd0=cmd0))
        for tok in operands:
            for cwd in sorted(cwds, key=str):
                for candidate in _expand_target(tok, cwd):
                    key = os.path.normcase(os.path.normpath(str(candidate)))
                    if key in seen:
                        continue
                    seen.add(key)
                    hit_paths.append(str(candidate))

    count = 0
    for key in hit_paths:
        try:
            count += _entry_cost(Path(key))
        except (OSError, ValueError):
            continue
    return count, hit_paths


__all__ = ["count_deletion_targets", "_entry_cost"]


def _iter_flags_for_tests() -> Iterable[Any]:
    """测试辅助：暴露内部常量供单测断言覆盖。"""
    return (_BASH_DELETE_CMDS, _CMD_DELETE_CMDS, _PS_DELETE_CMDS)