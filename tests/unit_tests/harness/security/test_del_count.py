# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""批量删除计数估算器（Pipeline D）单元测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openjiuwen.harness.security.permission_engine.delguard.del_count import (
    _entry_cost,
    count_deletion_targets,
    split_shell_operands,
)


def _mk(tmp_path, name: str, content: bytes = b"x") -> str:
    p = tmp_path / name
    p.write_bytes(content)
    return str(p)


def _try_symlink(target: Path, link: Path, *, target_is_directory: bool = False) -> bool:
    try:
        os.symlink(str(target), str(link), target_is_directory=target_is_directory)
    except (OSError, NotImplementedError):
        return False
    return True


def _try_dir_link(target: Path, link: Path) -> bool:
    """目录链接：优先 symlink，Windows 无创建权限时退到免提权的 junction."""
    if _try_symlink(target, link, target_is_directory=True):
        return True
    if os.name == "nt":
        import subprocess

        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True,
        )
        return result.returncode == 0 and os.path.lexists(link)
    return False


def test_entry_cost_file_symlink_dir(tmp_path) -> None:
    # 普通文件 = 1
    f = tmp_path / "a.txt"
    f.write_bytes(b"x")
    assert _entry_cost(f) == 1

    # 符号链接 = 1（指向目录也不递归）
    subdir = tmp_path / "sub"
    subdir.mkdir()
    (subdir / "inner.txt").write_bytes(b"y")
    link = tmp_path / "link"
    if os.name != "nt" or hasattr(os, "symlink"):
        try:
            os.symlink(str(subdir), str(link))
            assert _entry_cost(link) == 1
        except (OSError, NotImplementedError):
            pass

    # 空目录 = 0
    empty = tmp_path / "empty"
    empty.mkdir()
    assert _entry_cost(empty) == 0

    # 非空目录：递归内部文件+符号链接，不计目录节点自身
    d = tmp_path / "tree"
    d.mkdir()
    (d / "f1").write_bytes(b"1")
    (d / "f2").write_bytes(b"2")
    (d / "sub").mkdir()
    (d / "sub" / "f3").write_bytes(b"3")
    assert _entry_cost(d) == 3


def test_count_basic_files(tmp_path) -> None:
    a = _mk(tmp_path, "a")
    b = _mk(tmp_path, "b")
    count, paths = count_deletion_targets(f"rm {a} {b}", "bash", str(tmp_path))
    assert count == 2
    assert len(paths) == 2


def test_count_recursive_dir(tmp_path) -> None:
    d = tmp_path / "tree"
    d.mkdir()
    for i in range(5):
        (d / f"f{i}").write_bytes(b"x")
    count, _ = count_deletion_targets(f"rm -rf {d}", "bash", str(tmp_path))
    assert count == 5


def test_count_empty_dir_zero(tmp_path) -> None:
    d = tmp_path / "empty"
    d.mkdir()
    count, _ = count_deletion_targets(f"rm -r {d}", "bash", str(tmp_path))
    assert count == 0


def test_count_dedup_same_path(tmp_path) -> None:
    a = _mk(tmp_path, "a")
    count, paths = count_deletion_targets(f"rm {a} {a}", "bash", str(tmp_path))
    assert count == 1
    assert len(paths) == 1


def test_count_accumulate_across_commands(tmp_path) -> None:
    a = _mk(tmp_path, "a")
    b = _mk(tmp_path, "b")
    d = tmp_path / "tree"
    d.mkdir()
    (d / "x").write_bytes(b"x")
    count, _ = count_deletion_targets(f"rm {a} && rm -rf {b} {d}", "bash", str(tmp_path))
    assert count == 3


def test_count_powershell_remove_item(tmp_path) -> None:
    a = _mk(tmp_path, "a")
    b = _mk(tmp_path, "b")
    count, paths = count_deletion_targets(
        f"Remove-Item {a},{b} -Force", "powershell", str(tmp_path),
    )
    assert count == 2
    assert len(paths) == 2


def test_count_powershell_quoted_comma_list_auto(tmp_path) -> None:
    a = Path(_mk(tmp_path, "a.txt")).as_posix()
    b = Path(_mk(tmp_path, "b.txt")).as_posix()
    c = Path(_mk(tmp_path, "c.txt")).as_posix()
    count, paths = count_deletion_targets(
        f"Remove-Item '{a}','{b}'", "auto", str(tmp_path),
    )
    assert count == 2
    assert {Path(p).resolve() for p in paths} == {Path(a).resolve(), Path(b).resolve()}

    count3, paths3 = count_deletion_targets(
        f"Remove-Item '{a}','{b}','{c}'", "auto", str(tmp_path),
    )
    assert count3 == 3
    assert len(paths3) == 3


def test_count_quoted_comma_inside_filename_stays_one(tmp_path) -> None:
    weird = tmp_path / "foo,bar.txt"
    weird.write_bytes(b"x")
    count, paths = count_deletion_targets(
        f"Remove-Item '{weird.as_posix()}'", "auto", str(tmp_path),
    )
    assert count == 1
    assert len(paths) == 1


def test_count_cmd_del(tmp_path) -> None:
    a = _mk(tmp_path, "a.txt")
    count, _ = count_deletion_targets(f"del {a}", "cmd", str(tmp_path))
    assert count == 1


def test_count_non_delete_command_zero(tmp_path) -> None:
    a = _mk(tmp_path, "a")
    count, paths = count_deletion_targets(f"cat {a}", "bash", str(tmp_path))
    assert count == 0
    assert paths == []


def test_count_nonexistent_path_zero(tmp_path) -> None:
    count, _ = count_deletion_targets("rm /no/such/path", "bash", str(tmp_path))
    assert count == 0


def test_count_glob_expand(tmp_path) -> None:
    for i in range(3):
        (tmp_path / f"log{i}.txt").write_bytes(b"x")
    count, _ = count_deletion_targets("rm *.txt", "bash", str(tmp_path))
    assert count == 3


def test_posix_single_segment_paths_are_not_cmd_switches() -> None:
    for token in ("/tmp", "/etc", "/var", "/opt", "/home", "/root", "/usr", "/data", "/srv"):
        assert split_shell_operands(token, shell_type="auto", cmd0="rm") == [token]
        assert split_shell_operands(token, shell_type="bash", cmd0="mkdir") == [token]
        assert split_shell_operands(token, shell_type="powershell", cmd0="remove-item") == [token]
    assert split_shell_operands("/f", shell_type="cmd", cmd0="del") == []
    assert split_shell_operands("/q", shell_type="dos", cmd0="erase") == []
    assert split_shell_operands("-rf", shell_type="bash", cmd0="rm") == []


def test_count_unexpanded_var_skip(tmp_path) -> None:
    count, _ = count_deletion_targets("rm $TARGET", "bash", str(tmp_path))
    assert count == 0


def test_count_symlink_to_dir_counts_one_and_keeps_link_identity(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    for i in range(3):
        (target / f"f{i}").write_bytes(b"x")
    link = tmp_path / "link"
    if not _try_dir_link(target, link):
        pytest.skip("symlink creation is not permitted on this host")

    count, paths = count_deletion_targets(f"rm -rf {link}", "bash", str(tmp_path))
    assert count == 1
    # 审批列表必须展示链接本体，而不是它指向的目录。
    assert [os.path.normcase(p) for p in paths] == [
        os.path.normcase(os.path.normpath(str(link))),
    ]


def test_count_trailing_slash_link_counts_target_contents(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    for i in range(3):
        (target / f"f{i}").write_bytes(b"x")
    link = tmp_path / "link"
    if not _try_dir_link(target, link):
        pytest.skip("symlink creation is not permitted on this host")

    # 尾分隔符强制按目录解析并跟随链接（GNU rm FTS_COMFOLLOW），删除的是目标目录内容。
    count, paths = count_deletion_targets(f"rm -rf {link}/", "bash", str(tmp_path))
    assert count == 3
    assert [os.path.normcase(p) for p in paths] == [
        os.path.normcase(os.path.realpath(str(link))),
    ]

    # 无尾分隔符仍按链接本体计 1，两种写法语义必须区分开。
    count_link, _ = count_deletion_targets(f"rm -rf {link}", "bash", str(tmp_path))
    assert count_link == 1


def test_count_powershell_trailing_sep_junction_counts_target_contents(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "f0").write_bytes(b"x")
    (target / "f1").write_bytes(b"x")
    link = tmp_path / "junction"
    if not _try_dir_link(target, link):
        pytest.skip("symlink creation is not permitted on this host")

    # PowerShell 接受正斜杠路径分隔，尾分隔符语义与 bash 一致：进入目标目录。
    count, _ = count_deletion_targets(
        f"Remove-Item {link.as_posix()}/ -Recurse -Force", "powershell", str(tmp_path),
    )
    assert count == 2

    if os.name == "nt":
        count_bs, _ = count_deletion_targets(
            f"Remove-Item {link}\\ -Recurse -Force", "powershell", str(tmp_path),
        )
        assert count_bs == 2


def test_count_dangling_symlink_counts_one(tmp_path) -> None:
    link = tmp_path / "dangling"
    if not _try_symlink(tmp_path / "missing", link):
        pytest.skip("symlink creation is not permitted on this host")

    count, paths = count_deletion_targets(f"rm {link}", "bash", str(tmp_path))
    assert count == 1
    assert len(paths) == 1


def test_count_link_and_target_are_separate_targets(tmp_path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "inner").write_bytes(b"x")
    link = tmp_path / "link"
    if not _try_dir_link(target, link):
        pytest.skip("symlink creation is not permitted on this host")
    plain = tmp_path / "plain.txt"
    plain.write_bytes(b"x")

    # plain.txt = 1, link = 1, target 目录内容 = 1（链接不展开，目录本身不计）。
    count, paths = count_deletion_targets(f"rm -rf {plain} {link} {target}", "bash", str(tmp_path))
    assert count == 3
    assert len(paths) == 3


def test_count_dir_containing_symlink_does_not_follow_it(tmp_path) -> None:
    outer = tmp_path / "outer"
    nested_target = outer / "nested_target"
    nested_target.mkdir(parents=True)
    (nested_target / "deep1").write_bytes(b"x")
    (nested_target / "deep2").write_bytes(b"x")
    link = outer / "nested_link"
    if not _try_dir_link(nested_target, link):
        pytest.skip("symlink creation is not permitted on this host")
    (outer / "plain").write_bytes(b"x")

    # plain = 1, nested_link = 1, nested_target 不被链接展开，但目标目录本身在树里，递归得 2。
    count, _ = count_deletion_targets(f"rm -rf {outer}", "bash", str(tmp_path))
    assert count == 4


def test_count_cd_changes_relative_base(tmp_path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    victim = sub / "x.txt"
    victim.write_bytes(b"x")

    count, paths = count_deletion_targets("cd sub && rm -f x.txt", "bash", str(tmp_path))
    assert count == 1
    assert [os.path.normcase(p) for p in paths] == [os.path.normcase(str(victim))]


def test_count_pipeline_accumulates(tmp_path) -> None:
    a = _mk(tmp_path, "a")
    b = _mk(tmp_path, "b")
    count, _ = count_deletion_targets(f"rm {a} | rm {b}", "bash", str(tmp_path))
    assert count == 2