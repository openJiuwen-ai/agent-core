# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build the wheel of a generated provider package, optionally with its vendor CLI bundled.

Bundling is the packager's choice. With ``--binary`` the wheel follows the
layout ``claude-agent-sdk`` and ``openai-codex-cli-bin`` use: the CLI ships
inside the package (``<package>/_bundled/bin/<cli>``) and the wheel carries a
platform tag; the provider still prefers a CLI on ``PATH`` and only falls
back to the bundled copy. Without ``--binary`` the wheel is pure Python
(``py3-none-any``) and serves every ``--platform`` given. Next to the wheel
the script maintains
``runtime-manifest.json``: per provider and runtime platform, the artifacts a
host downloads, verifies by sha256, extracts into a provider-private
``site-packages`` and appends to ``sys.path`` -- the managed-runtime flow
jiuwenswarm's frozen desktop app uses for Claude Code and Codex, one level up
(the provider package instead of the vendor SDK).

Usage (one invocation per bundled platform, or one for all platforms without a CLI;
the manifest accumulates)::

    python build_runtime.py --project ./openjiuwen-harness-opencode \\
        --platform macos-arm64 --platform windows-x86_64 --base-url https://downloads.example.com/harness/
    python build_runtime.py --project ./openjiuwen-harness-opencode --platform macos-arm64 \\
        --binary ~/.opencode/bin/opencode --base-url https://downloads.example.com/harness/
    python build_runtime.py --project ./openjiuwen-harness-kimi --platform macos-arm64 \\
        --binary ~/.kimi-code/bin/kimi --helper ~/.kimi-code/bin/fd
    python build_runtime.py --project ./openjiuwen-harness-acme --platform macos-arm64 \\
        --extra-wheel ./wheels/acme_agent_sdk-1.2.0-py3-none-macosx_11_0_arm64.whl     # SDK mode

Run it with a Python that has ``openjiuwen`` and ``pip`` installed.
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ENTRY_POINT_GROUP = "openjiuwen.harness_providers"
MANIFEST_NAME = "runtime-manifest.json"
LAYOUT_VERSION = 1
# Runtime platform keys (as jiuwenswarm's managed runtime names them) -> wheel platform tags.
PLATFORM_TAGS = {
    "macos-arm64": "macosx_11_0_arm64",
    "macos-x86_64": "macosx_11_0_x86_64",
    "windows-x86_64": "win_amd64",
    "windows-arm64": "win_arm64",
    "linux-x86_64": "manylinux_2_17_x86_64",
    "linux-arm64": "manylinux_2_17_aarch64",
    "linux-musl-x86_64": "musllinux_1_2_x86_64",
    "linux-musl-arm64": "musllinux_1_2_aarch64",
}
_IGNORED = shutil.ignore_patterns("dist", "build", "*.egg-info", "_bundled", ".git", "__pycache__", ".pytest_cache")


@dataclass(frozen=True)
class Project:
    """What the build needs from the provider package's ``pyproject.toml``."""

    root: Path
    distribution: str
    version: str
    provider: str
    package: str


def load_project(root: Path) -> Project:
    """Read distribution, version, entry point and package from ``pyproject.toml``."""
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    entries = project.get("entry-points", {}).get(ENTRY_POINT_GROUP, {})
    if len(entries) != 1:
        raise SystemExit(f"{root} must declare exactly one {ENTRY_POINT_GROUP!r} entry point")
    (provider, target), = entries.items()
    return Project(
        root=root,
        distribution=project["name"],
        version=project["version"],
        provider=provider,
        package=target.split(":", 1)[0].split(".", 1)[0],
    )


def host_platform_key() -> str:
    """Return this machine's runtime platform key."""
    machine = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(
        platform.machine().lower()
    )
    system = {"windows": "windows", "darwin": "macos", "linux": "linux"}.get(platform.system().lower())
    if machine is None or system is None:
        return "unknown"
    if system == "linux" and platform.libc_ver()[0].lower() == "musl":
        system = "linux-musl"
    return f"{system}-{machine}"


# ----------------------------------------------------------------------
# Executable format checks
# ----------------------------------------------------------------------


def executable_arch(path: Path) -> tuple[str, set[str]]:
    """Return ``(os family, architectures)`` read from the executable header."""
    header = path.read_bytes()[:4096]
    if header[:4] == b"\x7fELF":
        order = "<" if header[5] == 1 else ">"
        machine = struct.unpack_from(order + "H", header, 18)[0]
        return "linux", {{0x3E: "x86_64", 0xB7: "arm64"}.get(machine, f"elf-{machine:#x}")}
    if header[:4] == b"\xcf\xfa\xed\xfe":
        cputype = struct.unpack_from("<I", header, 4)[0]
        return "macos", {_macho_arch(cputype)}
    if header[:4] == b"\xca\xfe\xba\xbe":
        count = struct.unpack_from(">I", header, 4)[0]
        archs = {_macho_arch(struct.unpack_from(">I", header, 8 + 20 * index)[0]) for index in range(min(count, 16))}
        return "macos", archs
    if header[:2] == b"MZ":
        offset = struct.unpack_from("<I", header, 0x3C)[0]
        if header[offset : offset + 4] == b"PE\x00\x00":
            machine = struct.unpack_from("<H", header, offset + 4)[0]
            return "windows", {{0x8664: "x86_64", 0xAA64: "arm64"}.get(machine, f"pe-{machine:#x}")}
    return "unknown", set()


def _macho_arch(cputype: int) -> str:
    return {0x01000007: "x86_64", 0x0100000C: "arm64"}.get(cputype, f"macho-{cputype:#x}")


def check_executable(path: Path, platform_key: str) -> None:
    """Refuse an executable built for another operating system or architecture."""
    family, archs = executable_arch(path)
    want_family = platform_key.split("-")[0]
    want_arch = platform_key.rsplit("-", 1)[1]
    if family != want_family or want_arch not in archs:
        found = f"{family}/{','.join(sorted(archs)) or '?'}"
        raise SystemExit(f"{path} is a {found} executable, not {platform_key}")


# ----------------------------------------------------------------------
# Wheel build
# ----------------------------------------------------------------------


def stage(project: Project, staging: Path, platform_key: str, binary: Path | None, helpers: list[Path],
          cli_version: str) -> str | None:
    """Copy the project and lay out ``_bundled``; return the entrypoint's path inside the wheel."""
    shutil.copytree(project.root, staging, ignore=_IGNORED)
    if binary is None:
        return None
    windows = platform_key.startswith("windows")
    bundled = staging / "src" / project.package / "_bundled"
    bin_dir = bundled / "bin"
    bin_dir.mkdir(parents=True)
    name = binary.name
    stem = name[:-4] if name.lower().endswith(".exe") else name
    target_name = stem + ".exe" if windows else stem
    shutil.copy2(binary, bin_dir / target_name)
    for helper in helpers:
        shutil.copy2(helper, bin_dir / helper.name)
    metadata = {
        "layoutVersion": LAYOUT_VERSION,
        "cli": stem,
        "cliVersion": cli_version,
        "platform": platform_key,
        "entrypoint": f"bin/{stem}",
    }
    (bundled / "runtime.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return f"{project.package}/_bundled/bin/{target_name}"


def build_wheel(staging: Path, out: Path) -> Path:
    """Build the pure wheel of the staged project with pip."""
    out.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--quiet", "-w", str(out), str(staging)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"pip wheel failed:\n{result.stdout}\n{result.stderr}")
    wheels = sorted(out.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"expected one wheel in {out}, found {[wheel.name for wheel in wheels]}")
    return wheels[0]


def retag(wheel: Path, tag: str, out: Path, executable_prefix: str) -> Path:
    """Rewrite ``wheel`` with platform ``tag``; bundled executables get mode 0755.

    The ``WHEEL`` metadata gains the platform tag and ``Root-Is-Purelib:
    false`` (binary content), and ``RECORD`` is recomputed for it.
    """
    distribution, version = wheel.name.split("-")[:2]
    target = out / f"{distribution}-{version}-py3-none-{tag}.whl"
    with zipfile.ZipFile(wheel) as source:
        infos = source.infolist()
        contents = {info.filename: source.read(info) for info in infos}
    dist_info = next(name.split("/")[0] for name in contents if name.endswith(".dist-info/WHEEL"))
    wheel_meta = contents[f"{dist_info}/WHEEL"].decode("utf-8").splitlines()
    # WHEEL is an RFC 822 header block: a blank line would end it early.
    kept = [line for line in wheel_meta if line.strip() and not line.startswith(("Tag:", "Root-Is-Purelib:"))]
    contents[f"{dist_info}/WHEEL"] = ("\n".join([*kept, "Root-Is-Purelib: false", f"Tag: py3-none-{tag}"]) + "\n").encode()
    record_name = f"{dist_info}/RECORD"
    rows = []
    for row in csv.reader(io.StringIO(contents[record_name].decode("utf-8"))):
        if not row:
            continue
        name = row[0]
        if name == record_name:
            rows.append([name, "", ""])
        else:
            data = contents[name]
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            rows.append([name, f"sha256={digest}", str(len(data))])
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\n").writerows(rows)
    contents[record_name] = buffer.getvalue().encode("utf-8")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as sink:
        for info in infos:
            mode = (info.external_attr >> 16) & 0o777
            if executable_prefix and info.filename.startswith(executable_prefix):
                mode = 0o755
            entry = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = ((mode or 0o644) | 0o100000) << 16
            sink.writestr(entry, contents[info.filename])
    return target


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ----------------------------------------------------------------------
# Manifest and smoke check
# ----------------------------------------------------------------------


def artifact(name: str, version: str, wheel: Path, base_url: str, mirror_base_url: str) -> dict[str, str]:
    return {
        "name": name,
        "version": version,
        "filename": wheel.name,
        "url": base_url + wheel.name if base_url else "",
        "mirror_url": mirror_base_url + wheel.name if mirror_base_url else "",
        "sha256": sha256_of(wheel),
    }


def update_manifest(path: Path, project: Project, platform_key: str, artifacts: list[dict[str, str]],
                    executable: str | None) -> None:
    """Record ``artifacts`` for ``platform_key`` in the accumulated manifest."""
    manifest = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"schema_version": 1}
    providers = manifest.setdefault("providers", {})
    entry = providers.get(project.provider)
    if entry is None or entry.get("version") != project.version:
        entry = {
            "distribution": project.distribution,
            "version": project.version,
            "entry_point": project.provider,
            "required_modules": [project.package],
            "executables": {},
            "platforms": {},
        }
        providers[project.provider] = entry
    entry["platforms"][platform_key] = artifacts
    if executable:
        entry["executables"][platform_key] = executable
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def smoke(wheel: Path, extra_wheels: list[Path], project: Project, run_version: bool) -> None:
    """Install like a frozen host: extract into a private site-packages, append it to sys.path, resolve."""
    with tempfile.TemporaryDirectory(prefix="provider-runtime-") as directory:
        site_packages = Path(directory) / "site-packages"
        site_packages.mkdir()
        for archive in (wheel, *extra_wheels):
            safe_extract(archive, site_packages)
        code = (
            "import importlib, json, subprocess, sys\n"
            # First on sys.path so a development install of the same package
            # cannot shadow the wheel under test (a frozen host has none and
            # simply appends).
            f"sys.path.insert(0, {str(site_packages)!r}); importlib.invalidate_caches()\n"
            "from openjiuwen.harness_providers import available_providers, resolve_provider\n"
            f"assert {project.provider!r} in available_providers(), available_providers()\n"
            f"provider = resolve_provider({project.provider!r})\n"
            "module = sys.modules[type(provider).__module__].__file__\n"
            f"assert module.startswith({str(site_packages.resolve())!r}) or module.startswith({str(site_packages)!r}), module\n"
            "report = {'card': provider.card.name, 'module': module}\n"
            "try:\n"
            f"    from {project.package}.bundled import bundled_runtime\n"
            "except ImportError:\n"
            "    bundled_runtime = None\n"
            "runtime = bundled_runtime() if bundled_runtime else None\n"
            "if runtime is not None:\n"
            "    report['executable'] = runtime.executable\n"
            f"    if {run_version}:\n"
            "        done = subprocess.run([runtime.executable, '--version'], capture_output=True, text=True, timeout=60)\n"
            "        report['version_exit'] = done.returncode\n"
            "        report['version'] = (done.stdout or done.stderr).strip().splitlines()[:1]\n"
            "print(json.dumps(report))\n"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
        lines = [line for line in result.stdout.splitlines() if line.startswith("{")]
        if result.returncode != 0 or not lines:
            raise SystemExit(f"smoke check failed:\n{result.stdout[-2000:]}\n{result.stderr[-2000:]}")
        report = json.loads(lines[-1])
        if run_version and "executable" in report and report.get("version_exit") != 0:
            raise SystemExit(f"bundled CLI did not run: {report}")
        print(f"smoke: {report}")


def safe_extract(archive: Path, destination: Path) -> None:
    """Extract a wheel the way a frozen host does: no absolute paths, no escapes, keep modes."""
    root = destination.resolve()
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            relative = PurePosixPath(member.filename)
            if relative.is_absolute() or ".." in relative.parts:
                raise SystemExit(f"unsafe wheel entry: {member.filename}")
            target = destination.joinpath(*relative.parts).resolve()
            if os.path.commonpath((root, target)) != str(root):
                raise SystemExit(f"unsafe wheel entry: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer)
            mode = member.external_attr >> 16 & 0o777
            if mode:
                target.chmod(mode)


def cli_version_of(binary: Path) -> str:
    try:
        done = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    lines = (done.stdout or done.stderr).strip().splitlines()
    return lines[0].strip() if done.returncode == 0 and lines else ""


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--project", required=True, help="generated provider package directory")
    parser.add_argument(
        "--platform",
        action="append",
        required=True,
        choices=sorted(PLATFORM_TAGS),
        help="runtime platform key; repeatable when no --binary is bundled",
    )
    parser.add_argument("--binary", help="vendor CLI executable for that platform (CLI modes)")
    parser.add_argument("--helper", action="append", default=[], help="helper executable shipped next to the CLI")
    parser.add_argument("--cli-version", help="vendor CLI version (default: `<binary> --version` on the host platform)")
    parser.add_argument("--extra-wheel", action="append", default=[], help="dependency wheel for that platform")
    parser.add_argument("--base-url", default="", help="download URL prefix the wheels will be published under")
    parser.add_argument("--mirror-base-url", default="", help="mirror URL prefix")
    parser.add_argument("--out", help="output directory (default: <project>/dist)")
    parser.add_argument("--no-smoke", action="store_true", help="skip the frozen-host install check")
    args = parser.parse_args()

    project = load_project(Path(args.project).expanduser().resolve())
    platforms = list(dict.fromkeys(args.platform))
    if args.binary and len(platforms) != 1:
        raise SystemExit("a bundled CLI belongs to exactly one --platform; run once per platform")
    if args.helper and not args.binary:
        raise SystemExit("--helper needs --binary")
    platform_key = platforms[0]
    native = host_platform_key() in platforms
    out = Path(args.out).expanduser().resolve() if args.out else project.root / "dist"
    out.mkdir(parents=True, exist_ok=True)
    binary = Path(args.binary).expanduser().resolve() if args.binary else None
    helpers = [Path(item).expanduser().resolve() for item in args.helper]
    extra_wheels = [Path(item).expanduser().resolve() for item in args.extra_wheel]
    for executable in [*([binary] if binary else []), *helpers]:
        if not executable.is_file():
            raise SystemExit(f"not a file: {executable}")
        check_executable(executable, platform_key)
    cli_version = args.cli_version or (cli_version_of(binary) if binary and native else "")

    with tempfile.TemporaryDirectory(prefix="provider-build-") as directory:
        staging = Path(directory) / "project"
        entrypoint = stage(project, staging, platform_key, binary, helpers, cli_version)
        pure = build_wheel(staging, Path(directory) / "wheel")
        if binary is None:
            wheel = out / pure.name
            shutil.copy2(pure, wheel)
        else:
            stale = out / f"{pure.name.split('-')[0]}-{project.version}-py3-none-{PLATFORM_TAGS[platform_key]}.whl"
            stale.unlink(missing_ok=True)
            wheel = retag(pure, PLATFORM_TAGS[platform_key], out, f"{project.package}/_bundled/bin/")

    artifacts = [artifact(project.distribution, project.version, wheel, args.base_url, args.mirror_base_url)]
    for extra in extra_wheels:
        name, version = extra.name.split("-")[:2]
        copied = out / extra.name
        if copied != extra:
            shutil.copy2(extra, copied)
        artifacts.append(artifact(name.replace("_", "-"), version, copied, args.base_url, args.mirror_base_url))
    for key in platforms:
        update_manifest(out / MANIFEST_NAME, project, key, artifacts, entrypoint)
    print(f"wheel: {wheel}")
    print(f"manifest: {out / MANIFEST_NAME}  ({project.provider} / {', '.join(platforms)})")
    if not args.no_smoke:
        smoke(wheel, extra_wheels, project, run_version=native)
    if not args.base_url:
        print("note: no --base-url given; fill url/mirror_url in the manifest once the wheels are published")


if __name__ == "__main__":
    main()
