# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Generate a pip-installable openjiuwen harness provider package for a third-party agent.

Writes ``<out>/openjiuwen-harness-<name>/``: a src-layout package that
declares an ``openjiuwen.harness_providers`` entry point, its unit tests
(with a scripted fake runtime), a registration test and an e2e suite. The
generated code imports cleanly and its unit tests pass before any
vendor-specific work; every place that still needs vendor knowledge carries
a ``SCAFFOLD(<topic>)`` marker.

Run it with the Python environment that has ``openjiuwen`` installed (the
package is generated against that version). openjiuwen may come from PyPI or
from a source checkout (``pip install -e <agent-core clone>``); a source
install makes the generated package depend on the openjiuwen git repository
instead of a released version::

    python scaffold.py --name opencode --mode acp --display OpenCode --binary opencode --command "opencode acp"
    python scaffold.py --name kimi --mode oneshot --binary kimi --command "kimi --output-format stream-json"
    python scaffold.py --name acme --mode sdk --sdk-module acme_agent --sdk-dist acme-agent-sdk
    python scaffold.py --check ./openjiuwen-harness-opencode     # list the SCAFFOLD markers still open

The script never overwrites an existing directory.
"""

from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import re
import shlex
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution, version
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"
MARKER = re.compile(r"SCAFFOLD\(([a-z0-9-]+)\)")
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
# Built-in openjiuwen provider names; a third-party entry point cannot shadow them.
RESERVED = {"native", "native_v2", "claudecode", "codex", "dsh"}
# The openjiuwen repository a source-installed openjiuwen is assumed to track.
OPENJIUWEN_GIT = "git+https://gitcode.com/openJiuwen/agent-core.git@develop"

# template (relative to templates/) -> destination (relative to the project root)
_COMMON = {
    "package/pyproject.toml.tmpl": "pyproject.toml",
    "package/README.md.tmpl": "README.md",
    "package/AGENTS.md.tmpl": "AGENTS.md",
    "package/conftest.py.tmpl": "tests/conftest.py",
    "package/contract.py.tmpl": "tests/contract.py",
    "package/test_registration.py.tmpl": "tests/test_registration.py",
    "package/gitignore.tmpl": ".gitignore",
    "e2e/test_e2e.py.tmpl": "tests/test_e2e.py",
    "common/__init__.py.tmpl": "{src}/__init__.py",
    "common/provider.py.tmpl": "{src}/provider.py",
}
_MODE_FILES = {
    "sdk": {
        "common/failure_classifier.py.tmpl": "{src}/failure_classifier.py",
        "common/mapping.py.tmpl": "{src}/mapping.py",
        "sdk/config.py.tmpl": "{src}/config.py",
        "sdk/harness.py.tmpl": "{src}/harness.py",
        "sdk/test_harness.py.tmpl": "tests/test_harness.py",
    },
    "acp": {
        "acp/failure_classifier.py.tmpl": "{src}/failure_classifier.py",
        "acp/mapping.py.tmpl": "{src}/mapping.py",
        "acp/config.py.tmpl": "{src}/config.py",
        "acp/harness.py.tmpl": "{src}/harness.py",
        "acp/rpc.py.tmpl": "{src}/rpc.py",
        "cli/process.py.tmpl": "{src}/process.py",
        "cli/bundled.py.tmpl": "{src}/bundled.py",
        "acp/test_harness.py.tmpl": "tests/test_harness.py",
        "acp/fake_agent.py.tmpl": "tests/fake_agent.py",
    },
    "oneshot": {
        "common/failure_classifier.py.tmpl": "{src}/failure_classifier.py",
        "common/mapping.py.tmpl": "{src}/mapping.py",
        "oneshot/config.py.tmpl": "{src}/config.py",
        "oneshot/harness.py.tmpl": "{src}/harness.py",
        "cli/process.py.tmpl": "{src}/process.py",
        "cli/bundled.py.tmpl": "{src}/bundled.py",
        "oneshot/test_harness.py.tmpl": "tests/test_harness.py",
        "oneshot/fake_cli.py.tmpl": "tests/fake_cli.py",
    },
}
_MODE_LABELS = {
    "sdk": "Python SDK（厂商 SDK 驱动其运行时）",
    "acp": "CLI 常驻进程，Agent Client Protocol（JSON-RPC 2.0 over stdio）",
    "oneshot": "CLI 每轮启动一次，结构化 stdout，靠厂商会话 id 延续对话",
}
_MODULE_TABLES = {
    "sdk": (
        "- `config.py`：provider 自有配置，校验时不 import 厂商 SDK。\n"
        "- `harness.py`：会话生命周期与 Turn 驱动；所有 SDK 调用集中在 `_sdk_*` 方法。\n"
        "- `mapping.py`：厂商记录到协议事件与 `TurnResult` 的纯映射与累积。\n"
        "- `failure_classifier.py`：厂商错误记录与异常到共享失败类别。"
    ),
    "acp": (
        "- `config.py`：provider 自有配置，校验时不解析、不启动 CLI。\n"
        "- `process.py`：子进程管理（启动、行读写、stderr 丢弃、关闭/终止）。\n"
        "- `bundled.py`：定位平台 wheel 内置的 CLI（`_bundled/runtime.json` + `bin/`）。\n"
        "- `rpc.py`：stdio 上的 JSON-RPC 2.0（单 reader、通知按序、服务端请求独立任务）。\n"
        "- `harness.py`：ACP 握手、会话新建/加载、`session/prompt` Turn、取消、权限请求。\n"
        "- `mapping.py`：`session/update` 到协议事件；`stopReason` 到终态。\n"
        "- `failure_classifier.py`：JSON-RPC 错误、进程退出、启动失败到共享失败类别。"
    ),
    "oneshot": (
        "- `config.py`：provider 自有配置，校验时不解析、不启动 CLI。\n"
        "- `process.py`：子进程管理（启动、行读写、stderr 丢弃、关闭/终止）。\n"
        "- `bundled.py`：定位平台 wheel 内置的 CLI（`_bundled/runtime.json` + `bin/`）。\n"
        "- `harness.py`：每个 Turn 启动一次 CLI，读 stdout 记录到 EOF，跟随厂商会话 id。\n"
        "- `mapping.py`：厂商记录到协议事件与 `TurnResult` 的纯映射与累积。\n"
        "- `failure_classifier.py`：厂商错误记录与异常到共享失败类别。"
    ),
}
_RUNTIME_NOTES = {
    "sdk": "Requires the vendor SDK `{sdk_dist}` (installed as a dependency).",
    "acp": (
        "The `{binary}` CLI is started as `{command}`: `cli_path` when configured, else the `{binary}` on "
        "`PATH`, else the copy bundled in a platform wheel (bundling is optional). The CLI must be logged in."
    ),
    "oneshot": (
        "Each turn runs `{command} ...` with `cli_path` when configured, else the `{binary}` on `PATH`, else "
        "the copy bundled in a platform wheel (bundling is optional). The CLI must be logged in."
    ),
}


@dataclass(frozen=True)
class Spec:
    """Everything substituted into the templates."""

    name: str
    mode: str
    card: str
    display: str
    class_prefix: str
    binary: str
    command: tuple[str, ...]
    sdk_module: str
    sdk_dist: str
    openjiuwen_requirement: str

    @property
    def dist(self) -> str:
        return "openjiuwen-harness-" + self.name.replace("_", "-")

    @property
    def package(self) -> str:
        return "openjiuwen_harness_" + self.name

    def tokens(self) -> dict[str, str]:
        note = _RUNTIME_NOTES[self.mode].format(
            sdk_dist=self.sdk_dist,
            binary=self.binary,
            command=shlex.join(self.command),
        )
        return {
            "__PROVIDER__": self.name,
            "__PKG__": self.package,
            "__DIST__": self.dist,
            "__CLASS__": self.class_prefix,
            "__CARD__": self.card,
            "__DISPLAY__": self.display,
            "__BINARY__": self.binary,
            "__COMMAND__": repr(self.command),
            "__SDK_MODULE__": self.sdk_module,
            "__SDK_DIST__": self.sdk_dist,
            "__SDK_DEPENDENCY__": f'    "{self.sdk_dist}",\n' if self.mode == "sdk" else "",
            "__OPENJIUWEN_REQUIREMENT__": self.openjiuwen_requirement,
            "__RUNTIME_NOTE__": note,
            "__UPPER__": self.name.upper(),
            "__MODE__": self.mode,
            "__MODE_LABEL__": _MODE_LABELS[self.mode],
            "__MODULE_TABLE__": _MODULE_TABLES[self.mode],
            "__YEAR__": str(datetime.date.today().year),
        }


def detect_openjiuwen() -> str:
    """Return the openjiuwen requirement for the generated package, after checking it can load plugins.

    A release from an index pins ``openjiuwen>=<installed>``. An install from
    a source checkout or a git URL (``pip install -e <clone>``) has no
    released version to pin, so the package depends on the git repository:
    the VCS URL and commit pip recorded, else :data:`OPENJIUWEN_GIT`.
    """
    try:
        installed = version("openjiuwen")
    except PackageNotFoundError:
        raise SystemExit(
            "openjiuwen is not installed in this interpreter; run `pip install openjiuwen`, or "
            "`pip install -e <agent-core clone>` for a source checkout, and invoke this script with that Python"
        ) from None
    # Read the source instead of importing it: importing openjiuwen initializes
    # its logging, which writes a logs/ directory into the current directory.
    spec = importlib.util.find_spec("openjiuwen.harness_providers.factory")
    source = Path(spec.origin).read_text(encoding="utf-8") if spec is not None and spec.origin else ""
    if "HARNESS_PROVIDER_ENTRY_POINT_GROUP" not in source:
        origin = Path(spec.origin).parent if spec is not None and spec.origin else "openjiuwen"
        raise SystemExit(
            f"openjiuwen {installed} ({origin}) cannot discover third-party harness providers: its "
            "harness_providers/factory.py has no HARNESS_PROVIDER_ENTRY_POINT_GROUP. Upgrade the release, or "
            "check out a source revision that includes entry point discovery"
        )
    source_requirement = _source_requirement()
    return source_requirement or f"openjiuwen>={installed}"


def _source_requirement() -> str | None:
    """Return a git requirement when openjiuwen was installed from source, else ``None``."""
    try:
        raw = distribution("openjiuwen").read_text("direct_url.json")
    except PackageNotFoundError:
        return None
    if not raw:
        return None
    direct = json.loads(raw)
    vcs = direct.get("vcs_info")
    if isinstance(vcs, dict) and vcs.get("vcs") == "git":
        revision = vcs.get("commit_id") or vcs.get("requested_revision")
        url = direct.get("url", "")
        return f"openjiuwen @ git+{url}@{revision}" if revision else f"openjiuwen @ git+{url}"
    if isinstance(direct.get("dir_info"), dict):
        return f"openjiuwen @ {OPENJIUWEN_GIT}"
    return None


def render(text: str, tokens: dict[str, str]) -> str:
    """Substitute every ``__TOKEN__`` placeholder and refuse leftovers."""
    for key, value in tokens.items():
        text = text.replace(key, value)
    leftover = sorted(set(re.findall(r"__[A-Z][A-Z_]*__", text)))
    if leftover:
        raise SystemExit(f"unsubstituted template tokens: {leftover}")
    return text


def write(spec: Spec, project: Path, *, dry_run: bool) -> list[Path]:
    """Render every template into ``project``; refuse an existing directory."""
    if project.exists():
        raise SystemExit(f"refusing to overwrite existing directory {project}")
    tokens = spec.tokens()
    src = f"src/{spec.package}"
    written: list[Path] = []
    for template, destination in {**_COMMON, **_MODE_FILES[spec.mode]}.items():
        target = project / destination.format(src=src)
        written.append(target)
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render((TEMPLATES / template).read_text(encoding="utf-8"), tokens), encoding="utf-8")
    return written


def open_markers(project: Path) -> list[tuple[Path, int, str]]:
    """Return every ``SCAFFOLD(...)`` marker left in a generated project."""
    found: list[tuple[Path, int, str]] = []
    for path in sorted(project.rglob("*")):
        if not path.is_file() or path.suffix not in {".py", ".md", ".toml"}:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            match = MARKER.search(line)
            if match:
                found.append((path, number, match.group(1)))
    return found


def build_spec(args: argparse.Namespace) -> Spec:
    name = args.name
    if not NAME_PATTERN.match(name) or name in RESERVED:
        raise SystemExit(f"invalid or reserved provider name: {name!r}")
    mode = args.mode
    binary = args.binary or name
    if args.command:
        command = tuple(shlex.split(args.command))
    elif mode == "acp":
        command = (binary, "acp")
    else:
        command = (binary,)
    if mode == "sdk" and not args.sdk_module:
        raise SystemExit("--sdk-module is required for --mode sdk")
    class_prefix = args.class_prefix or "".join(part.capitalize() for part in name.split("_"))
    return Spec(
        name=name,
        mode=mode,
        card=args.card or name.replace("_", "-"),
        display=args.display or class_prefix,
        class_prefix=class_prefix,
        binary=binary,
        command=command,
        sdk_module=args.sdk_module or "",
        sdk_dist=args.sdk_dist or (args.sdk_module or "").replace("_", "-"),
        openjiuwen_requirement=args.openjiuwen_requirement or detect_openjiuwen(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--name", help="provider name used with create_harness, snake_case (e.g. opencode)")
    parser.add_argument("--mode", choices=sorted(_MODE_FILES), help="integration mode")
    parser.add_argument("--out", default=".", help="directory the project is created in (default: cwd)")
    parser.add_argument("--card", help="HarnessCard name (default: name with '-')")
    parser.add_argument("--display", help="human name used in messages (default: class prefix)")
    parser.add_argument("--class-prefix", help="class name prefix (default: CamelCase name)")
    parser.add_argument("--binary", help="CLI executable (cli modes; default: name)")
    parser.add_argument("--command", help="default argv for the CLI, shell-quoted")
    parser.add_argument("--sdk-module", help="vendor SDK import name (sdk mode)")
    parser.add_argument("--sdk-dist", help="vendor SDK distribution name (sdk mode)")
    parser.add_argument(
        "--openjiuwen-requirement",
        help="override the detected openjiuwen requirement (e.g. 'openjiuwen>=0.2.0' before publishing to PyPI)",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the plan without writing")
    parser.add_argument("--check", metavar="PROJECT", help="list the SCAFFOLD markers still open in PROJECT")
    args = parser.parse_args()

    if args.check:
        project = Path(args.check).resolve()
        markers = open_markers(project)
        for path, number, topic in markers:
            print(f"{path.relative_to(project)}:{number}: SCAFFOLD({topic})")
        print(f"{len(markers)} open SCAFFOLD marker(s)")
        sys.exit(1 if markers else 0)

    if not args.name or not args.mode:
        parser.error("--name and --mode are required unless --check is given")
    spec = build_spec(args)
    project = Path(args.out).resolve() / spec.dist
    written = write(spec, project, dry_run=args.dry_run)
    verb = "would write" if args.dry_run else "wrote"
    for path in written:
        print(f"{verb} {path}")
    if args.dry_run:
        return
    topics = sorted({topic for _, _, topic in open_markers(project)})
    print(f"\nproject: {project}  ({spec.openjiuwen_requirement}, entry point {spec.name!r})")
    print(f"open SCAFFOLD topics: {', '.join(topics)}")
    print("\nnext:")
    print(f"  cd {shlex.quote(str(project))}")
    print('  python -m pip install -e ".[test]"')
    print("  python -m pytest tests/test_harness.py tests/test_registration.py -q")


if __name__ == "__main__":
    main()
