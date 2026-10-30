#!/usr/bin/env python3
"""Select a self-contained SWE test suite and invoke the rollout runner.

The wrapper owns test-suite selection and configuration composition.  The
YuanRong/Docker rollout, eval and upload implementation remains in
run_sft_rollout.py.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import re
import sys
import tempfile
import urllib.parse
import stat
import shutil
import zipfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SUITES_ROOT = (ROOT / "test_suite").resolve()
RUNNER = (ROOT / "scripts" / "run_sft_rollout.py").resolve()
SUITE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

# Prompt-level config overrides are intentionally limited to operational knobs.
# Credentials, endpoints, model profiles and file paths stay in suite/config.json.
OVERRIDE_KEYS = {
    "sandbox.backend",
    "docker.api_version",
    "docker.cpus",
    "docker.memory",
    "docker.pids",
    "docker.workers",
    "docker.keep_container",
    "docker.network",
    "swe.enable_task_loop",
    "swe.max_iterations",
    "swe.timeout_seconds",
    "swe.completion_timeout_seconds",
    "eval.workers",
    "eval.timeout_seconds",
    "eval.wall_timeout_seconds",
}


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid JSON: {path}") from exc


def _zip_member_name(member: zipfile.ZipInfo, prefix: str | None) -> str:
    """Return a safe, POSIX-normalized archive member name.

    ZIP entries are untrusted input.  In particular, reject absolute names,
    ``..`` components and symbolic links before writing anything to disk.
    ``prefix`` is an optional single top-level directory that is stripped for
    archives produced by ``zip -r suite.zip suite/``.
    """
    name = str(member.filename or "").replace("\\", "/")
    if not name or "\x00" in name:
        raise RuntimeError("test suite zip contains an empty or NUL member name")
    if name.startswith("/") or re.match(r"^[A-Za-z]:($|/)", name):
        raise RuntimeError(f"test suite zip contains an absolute member: {member.filename!r}")
    parts = tuple(part for part in name.split("/") if part)
    if any(part in {".", ".."} for part in parts):
        raise RuntimeError(f"test suite zip contains a path traversal member: {member.filename!r}")
    mode = (member.external_attr >> 16) & 0o170000
    if stat.S_ISLNK(mode):
        raise RuntimeError(f"test suite zip contains a symbolic link: {member.filename!r}")
    if prefix and parts and parts[0] == prefix:
        parts = parts[1:]
    if not parts:
        return ""
    return "/".join(parts)


def _extract_suite_zip(archive: Path) -> tuple[Path, str]:
    """Extract a suite archive below this Skill's ``test_suite/`` directory.

    The archive prefix (``foo`` for ``foo.zip``) is the suite name.  Existing
    files are updated in place so repeated Web prompts are idempotent; files
    not present in a newer archive are deliberately left untouched rather than
    recursively deleting user data.
    """
    suite_name = archive.stem
    if not SUITE_NAME_RE.fullmatch(suite_name):
        raise RuntimeError("zip file prefix must contain only letters, digits, '.', '_' or '-'")
    destination = (SUITES_ROOT / suite_name).resolve()
    try:
        destination.relative_to(SUITES_ROOT)
    except ValueError as exc:
        raise RuntimeError("zip extraction target must stay inside test_suite/") from exc
    if destination.exists() and not destination.is_dir():
        raise RuntimeError(f"zip extraction target is not a directory: {destination}")
    if destination.is_symlink():
        raise RuntimeError(f"zip extraction target must not be a symlink: {destination}")

    with zipfile.ZipFile(archive) as zf:
        members = list(zf.infolist())
        if not members:
            raise RuntimeError(f"test suite zip is empty: {archive}")
        # Accept either files at archive root or one conventional top-level
        # directory.  The latter is stripped so the resulting suite is always
        # test_suite/<zip-stem>/{config.json,...}.
        raw_names = [str(m.filename or "").replace("\\", "/") for m in members]
        file_names = [n for n, m in zip(raw_names, members) if not m.is_dir()]
        prefix: str | None = None
        if "config.json" not in file_names:
            roots = {n.split("/", 1)[0] for n in file_names if n}
            if len(roots) == 1:
                candidate = next(iter(roots))
                if all(n == candidate or n.startswith(candidate + "/") for n in raw_names if n):
                    prefix = candidate
        normalized: list[tuple[zipfile.ZipInfo, str]] = []
        for member in members:
            normalized_name = _zip_member_name(member, prefix)
            if normalized_name:
                normalized.append((member, normalized_name))
        required = {"config.json", "supervisor.json", "testcase.json"}
        available = {name for _member, name in normalized}
        missing = sorted(required - available)
        if missing:
            raise RuntimeError("test suite zip is missing: " + ", ".join(missing))
        data_entries = [name for _member, name in normalized if name == "data" or name.startswith("data/")]
        if not data_entries:
            raise RuntimeError("test suite zip must contain a data/ directory")
        destination.mkdir(parents=True, exist_ok=True)
        for member, relative_name in normalized:
            target = (destination / relative_name).resolve()
            try:
                target.relative_to(destination)
            except ValueError as exc:
                raise RuntimeError(f"test suite zip member escapes extraction directory: {member.filename!r}") from exc
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if target.exists() and target.is_symlink():
                raise RuntimeError(f"existing extraction target is a symlink: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member, "r") as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
    return destination, suite_name


def resolve_test_suite(value: str) -> tuple[Path, str, bool]:
    """Resolve a suite name, absolute directory, or ZIP archive.

    Names are looked up only beside this wrapper.  Absolute directories are
    used as-is (their basename becomes the suite name).  ZIP archives, whether
    absolute or relative to the caller's working directory, are extracted
    below this wrapper's sibling ``test_suite/`` directory.
    """
    raw = str(value or "").strip()
    if not raw or raw in {".", ".."} or "\x00" in raw:
        raise RuntimeError("--test-suite must be a suite name, an absolute directory, or a zip archive")
    supplied = Path(raw).expanduser()
    if supplied.is_absolute():
        candidate = supplied.resolve()
        if candidate.is_dir():
            if not candidate.name or not SUITE_NAME_RE.fullmatch(candidate.name):
                raise RuntimeError("absolute test suite directory name must contain only letters, digits, '.', '_' or '-'")
            return candidate, candidate.name, False
        if candidate.is_file() and candidate.suffix.lower() == ".zip":
            suite, name = _extract_suite_zip(candidate)
            return suite, name, True
        raise RuntimeError(f"test suite directory or zip does not exist: {raw}")

    # Relative paths are intentionally accepted only for ZIP archives.  A
    # directory suite is selected by its simple name to prevent arbitrary
    # reads outside the Skill bundle.
    if supplied.suffix.lower() == ".zip" or supplied.exists():
        candidate = supplied.resolve()
        if candidate.is_file() and candidate.suffix.lower() == ".zip":
            suite, name = _extract_suite_zip(candidate)
            return suite, name, True
    if "/" in raw or "\\" in raw or not SUITE_NAME_RE.fullmatch(raw):
        raise RuntimeError("relative directory suites must be a simple name; use an absolute path for external suites")
    candidate = (SUITES_ROOT / raw).resolve()
    try:
        candidate.relative_to(SUITES_ROOT)
    except ValueError as exc:
        raise RuntimeError("named test suite must resolve inside the Skill test_suite/ directory") from exc
    if not candidate.is_dir():
        raise RuntimeError(f"test suite does not exist: {raw}")
    return candidate, candidate.name, False


def suite_path(name: str) -> Path:
    """Backward-compatible path-only resolver used by self-tests/callers."""
    return resolve_test_suite(name)[0]


def suite_file(suite: Path, relative: str, label: str) -> Path:
    raw = str(relative or "").strip()
    path = Path(raw)
    if not raw or "\x00" in raw or path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"{label} must be a relative path inside the suite")
    candidate = (suite / path).resolve()
    try:
        candidate.relative_to(suite.resolve())
    except ValueError as exc:
        raise RuntimeError(f"{label} must stay inside the selected test suite") from exc
    if not candidate.is_file():
        raise RuntimeError(f"{label} does not exist: {relative}")
    return candidate


def data_file(suite: Path, relative: str, label: str) -> Path:
    candidate = suite_file(suite, relative, label)
    data_root = (suite / "data").resolve()
    try:
        candidate.relative_to(data_root)
    except ValueError as exc:
        raise RuntimeError(f"{label} must be inside the suite data/ directory") from exc
    if candidate.suffix.lower() not in {".json", ".jsonl", ".parquet"}:
        raise RuntimeError(f"{label} must be JSON, JSONL or Parquet")
    return candidate


def normalize_suite_paths(suite: Path, config: dict[str, Any]) -> None:
    """Resolve known path-valued settings relative to the suite root.

    The underlying runner historically resolves some fields relative to its
    current working directory and some relative to the config file.  A suite
    must be portable, so normalize all environment/path fields that the
    runner consumes directly.  Absolute host paths remain untouched; relative
    values are confined to the selected suite (and may point to directories
    that do not exist yet, such as workspace/output roots).
    """
    path_fields = (
        ("docker", "host_site_packages", "docker.host_site_packages"),
        ("docker", "runtime_prefix", "docker.runtime_prefix"),
        ("docker", "workspace_root", "docker.workspace_root"),
        ("docker", "output_root", "docker.output_root"),
        ("jiuwenswarm", "python", "jiuwenswarm.python"),
        ("eval", "python", "eval.python"),
    )
    for section_name, field_name, label in path_fields:
        section = config.get(section_name)
        if not isinstance(section, dict) or field_name not in section:
            continue
        raw = str(section.get(field_name) or "").strip()
        if not raw:
            continue
        expanded = Path(os.path.expandvars(os.path.expanduser(raw)))
        if expanded.is_absolute():
            section[field_name] = str(expanded)
            continue
        if ".." in expanded.parts:
            raise RuntimeError(f"{label} relative path must stay inside the selected suite")
        candidate = (suite / expanded).resolve()
        try:
            candidate.relative_to(suite.resolve())
        except ValueError as exc:
            raise RuntimeError(f"{label} relative path must stay inside the selected suite") from exc
        section[field_name] = str(candidate)


def load_supervisor(path: Path, *, resolve_key: bool = True) -> dict[str, str]:
    raw = load_json(path)
    required = {"model_name", "provider", "api_base", "api_key"}
    if not isinstance(raw, dict) or set(raw) != required:
        raise RuntimeError("suite supervisor.json must contain exactly model_name, provider, api_base and api_key")
    values: dict[str, str] = {}
    for field in required:
        value = raw.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError(f"suite supervisor.json field {field} must be a non-empty string")
        value = value.strip()
        if any(char in value for char in ("\x00", "\r", "\n")):
            raise RuntimeError(f"suite supervisor.json field {field} contains a control character")
        values[field] = value
    parsed = urllib.parse.urlsplit(values["api_base"])
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("suite supervisor.json api_base must be a credential-free HTTPS URL")
    key = values["api_key"]
    if key.startswith("${") and key.endswith("}") and resolve_key:
        env_name = key[2:-1]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
            raise RuntimeError("suite supervisor.json api_key environment reference is invalid")
        key = str(os.environ.get(env_name) or "").strip()
        if not key:
            raise RuntimeError(f"suite supervisor.json api_key environment variable is empty: {env_name}")
        values["api_key"] = key
    return values


def set_nested(config: dict[str, Any], key: str, value: Any) -> None:
    if key not in OVERRIDE_KEYS:
        allowed = ", ".join(sorted(OVERRIDE_KEYS))
        raise RuntimeError(f"--set key is not an operational override: {key!r}; allowed: {allowed}")
    section, field = key.split(".", 1)
    target = config.get(section)
    if not isinstance(target, dict):
        raise RuntimeError(f"config section is missing or not an object: {section}")
    target[field] = value


def parse_override(raw: str) -> tuple[str, Any]:
    if "=" not in raw:
        raise RuntimeError("--set must use section.key=value")
    key, encoded = raw.split("=", 1)
    key = key.strip()
    if not key:
        raise RuntimeError("--set key is empty")
    try:
        value = json.loads(encoded)
    except json.JSONDecodeError:
        value = encoded
    if isinstance(value, (dict, list)):
        raise RuntimeError("--set accepts scalar JSON values only")
    return key, value


def prepare_suite(
    name: str,
    overrides: list[str],
    *,
    resolve_key: bool = True,
) -> tuple[Path, dict[str, Any], Path, Path | None, Path, dict[str, str]]:
    suite, _suite_name, _was_extracted = resolve_test_suite(name)
    config_path = suite_file(suite, "config.json", "suite config.json")
    testcase_path = suite_file(suite, "testcase.json", "suite testcase.json")
    supervisor_path = suite_file(suite, "supervisor.json", "suite supervisor.json")
    supervisor = load_supervisor(supervisor_path, resolve_key=resolve_key)
    data_root = (suite / "data").resolve()
    if not data_root.is_dir():
        raise RuntimeError("selected test suite must contain a data/ directory")
    config = load_json(config_path)
    if not isinstance(config, dict):
        raise RuntimeError("suite config.json must contain one JSON object")
    cases = load_json(testcase_path)
    cases = cases.get("instances", cases.get("cases", cases)) if isinstance(cases, dict) else cases
    if not isinstance(cases, list) or not cases or not all(isinstance(row, dict) for row in cases):
        raise RuntimeError("suite testcase.json must contain a non-empty JSON array of objects")
    instance_ids = [str(row.get("instance_id") or "") for row in cases]
    if any(not value for value in instance_ids) or len(set(instance_ids)) != len(instance_ids):
        raise RuntimeError("suite testcase.json must contain unique non-empty instance_id values")
    swe = config.setdefault("swe", {})
    evaluation = config.setdefault("eval", {})
    gold = config.setdefault("gold", {})
    if not isinstance(swe, dict) or not isinstance(evaluation, dict) or not isinstance(gold, dict):
        raise RuntimeError("config sections swe, eval and gold must be JSON objects")
    # testcase.json is part of the suite contract, regardless of the old
    # config's case_file value.  Data files remain relative in the user's file
    # but are normalized in the temporary runner config.
    swe["case_file"] = str(testcase_path)
    eval_value = str(evaluation.get("dataset") or "").strip()
    if not eval_value:
        raise RuntimeError("suite config.json must set eval.dataset under data/")
    eval_path = data_file(suite, eval_value, "eval.dataset")
    evaluation["dataset"] = str(eval_path)
    gold_path: Path | None = None
    gold_value = str(gold.get("source") or "").strip()
    if bool(gold.get("enabled", False)):
        if not gold_value:
            raise RuntimeError("gold.enabled is true but gold.source is empty")
        gold_path = data_file(suite, gold_value, "gold.source")
        gold["source"] = str(gold_path)
    normalize_suite_paths(suite, config)
    for raw in overrides:
        key, value = parse_override(raw)
        set_nested(config, key, value)
    # Explicit wrapper flags are applied after --set in main().
    return suite, config, testcase_path, gold_path, supervisor_path, supervisor


def write_temp_config(suite: Path, config: dict[str, Any]) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=suite, prefix=".run-swe-wrap-", suffix=".json", delete=False
    )
    path = Path(handle.name)
    try:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    finally:
        handle.close()
    return path


def write_temp_profile(suite: Path, profile: dict[str, str]) -> Path:
    """Materialize the resolved suite profile with runner-required 0600 mode."""
    handle = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=suite, prefix=".supervisor-wrap-", suffix=".json", delete=False
    )
    path = Path(handle.name)
    try:
        json.dump(profile, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    finally:
        handle.close()
    path.chmod(0o600)
    return path


def self_test() -> int:
    previous_key = os.environ.get("SUPERVISOR_API_KEY")
    os.environ.setdefault("SUPERVISOR_API_KEY", "wrapper-self-test-key")
    checked = []
    try:
        for name in ("mini5", "mini50"):
            suite, config, testcase, gold, supervisor_path, _supervisor = prepare_suite(name, [])
            assert config["model"]["profile"] == "supervisor.json"
            checked.append({
                "name": name,
                "cases": len(load_json(testcase)),
                "eval_dataset": config["eval"]["dataset"],
                "gold_source": str(gold) if gold else None,
                "supervisor": str(supervisor_path),
            })
    finally:
        if previous_key is None:
            os.environ.pop("SUPERVISOR_API_KEY", None)
        else:
            os.environ["SUPERVISOR_API_KEY"] = previous_key
    for invalid in ("", ".", "..", "mini5/../mini50", "/tmp/mini5"):
        try:
            suite_path(invalid)
        except RuntimeError:
            continue
        raise AssertionError(f"invalid suite name was accepted: {invalid!r}")
    absolute_suite, absolute_name, extracted = resolve_test_suite(str((SUITES_ROOT / "mini5").resolve()))
    assert absolute_suite == (SUITES_ROOT / "mini5").resolve()
    assert absolute_name == "mini5" and extracted is False
    print(json.dumps({"self_test": True, "checked": checked}, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Select a bundled SWE test_suite and run SFT rollout")
    p.add_argument(
        "--test-suite", "--suite", dest="test_suite",
        help="suite name under test_suite/, an absolute suite directory, or a .zip archive",
    )
    p.add_argument("--limit", type=int, help="override runner case limit")
    p.add_argument("--workers", type=int, help="override docker.workers")
    p.add_argument("--case-id", help="run only one instance_id")
    p.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE", help="override one allowlisted operational config value")
    p.add_argument("--keep-container", action="store_true", help="pass through for sandbox diagnostics")
    p.add_argument("--smoke", action="store_true", help="pass through without calling the model")
    p.add_argument("--dry-run", action="store_true", help="validate suite files and print the composed config without running")
    p.add_argument("--self-test", action="store_true", help="validate the default suites without running YuanRong")
    return p


def main() -> int:
    args = parser().parse_args()
    if args.self_test:
        return self_test()
    if not args.test_suite:
        raise SystemExit("--test-suite is required unless --self-test is used")
    suite, config, testcase_path, gold_path, supervisor_path, supervisor = prepare_suite(
        args.test_suite,
        args.overrides,
        resolve_key=not (args.smoke or args.dry_run),
    )
    config = copy.deepcopy(config)
    # The suite contract names the profile file explicitly.  The temporary
    # materialized profile is registered under that same safe basename so the
    # runner never needs a generated suite-mini alias.
    profile_alias = supervisor_path.name
    config.setdefault("model", {})["profile"] = profile_alias
    if args.workers is not None:
        if args.workers < 1:
            raise SystemExit("--workers must be at least 1")
        config.setdefault("docker", {})["workers"] = args.workers
    print(json.dumps({
        "test_suite": suite.name,
        "case_file": str(testcase_path),
        "case_count": len(load_json(testcase_path)),
        "eval_dataset": config.get("eval", {}).get("dataset"),
        "gold_source": str(gold_path) if gold_path else None,
        "supervisor": str(supervisor_path),
        "overrides": args.overrides,
    }, ensure_ascii=False), flush=True)
    if args.dry_run:
        print(json.dumps({"dry_run": True, "config": config, "supervisor": {
            "model_name": supervisor["model_name"],
            "provider": supervisor["provider"],
            "api_base": supervisor["api_base"],
            "api_key": "[redacted]",
        }}, ensure_ascii=False, indent=2))
        return 0
    temp_config = write_temp_config(suite, config)
    temp_profile = write_temp_profile(suite, supervisor)
    command = [str(RUNNER), "--config", str(temp_config)]
    if args.limit is not None:
        if args.limit < 1:
            raise SystemExit("--limit must be at least 1")
        command.extend(["--limit", str(args.limit)])
    if args.workers is not None:
        command.extend(["--workers", str(args.workers)])
    if args.case_id:
        command.extend(["--case-id", args.case_id])
    if args.keep_container:
        command.append("--keep-container")
    if args.smoke:
        command.append("--smoke")
    # Import the runner in-process and add only this suite's temporary
    # supervisor.json profile.  This avoids replacing the global profile and
    # keeps model selection suite-local.
    spec = importlib.util.spec_from_file_location("sft_rollout_runner_for_suite", RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load runner: {RUNNER}")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.SUPERVISOR_PROFILES[profile_alias] = (temp_profile,)
    previous_argv = sys.argv
    sys.argv = command
    try:
        return int(runner.main())
    finally:
        sys.argv = previous_argv
        try:
            temp_config.unlink()
        except OSError:
            pass
        try:
            temp_profile.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"run_sft_testsuite: error: {exc}", file=sys.stderr)
        raise SystemExit(2)
