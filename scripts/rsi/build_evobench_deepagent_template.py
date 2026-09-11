"""Build the versioned APEX template used by the DeepAgent Evo-Bench adapter."""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALIAS = "evobench-apex-openjiuwen"
LOGGER = logging.getLogger(__name__)


def _revision(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _build_wheel(
    repo_root: Path,
    output_dir: Path,
    *,
    expected_revision: str = "",
) -> tuple[Path, str]:
    revision = _revision(repo_root)
    if expected_revision and revision != expected_revision:
        raise RuntimeError(f"source revision changed: expected {expected_revision}, found {revision}")
    output_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(output_dir)],
        cwd=repo_root,
        check=True,
    )
    wheels = sorted(output_dir.glob("openjiuwen-*.whl"), key=lambda item: item.stat().st_mtime)
    if not wheels:
        raise RuntimeError("openJiuwen wheel build produced no artifact")
    return wheels[-1], revision


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=os.environ.get("RSI_REPO_ROOT", str(REPO_ROOT)))
    parser.add_argument("--output-dir", default=os.environ.get("RSI_TEMPLATE_DIST_DIR", ""))
    parser.add_argument("--base-template", default=os.environ.get("EVOBENCH_APEX_BASE_TEMPLATE", "evobench-apex-spec"))
    parser.add_argument("--alias", default=os.environ.get("EVOBENCH_DEEPAGENT_TEMPLATE", DEFAULT_ALIAS))
    parser.add_argument("--expected-revision", default=os.environ.get("OPENJIUWEN_EXPECTED_REVISION", ""))
    args = parser.parse_args()

    try:
        from e2b import Template, default_build_logger
    except ImportError as exc:
        raise RuntimeError("run this script with the Evo-Bench virtual environment") from exc

    repo_root = Path(args.repo_root).expanduser().resolve()
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else repo_root / ".local" / "rsi_runtime" / "dist"
    )
    wheel, revision = _build_wheel(
        repo_root,
        output_dir,
        expected_revision=args.expected_revision,
    )
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    remote_wheel = f"/tmp/{wheel.name}"
    # Extend the already-published official APEX image. Rebuilding the whole
    # split archive is both wasteful and susceptible to E2B COPY cache races.
    definition = (
        Template(file_context_path=str(wheel.parent))
        .from_template(args.base_template)
        .copy(wheel.name, remote_wheel, force_upload=True)
        .pip_install("opentelemetry-sdk>=1.25.0", g=True)
        .run_cmd(
            f"python -m pip install --no-cache-dir {remote_wheel} "
            '&& python -c "from openjiuwen.harness import create_deep_agent; '
            "from openjiuwen.harness.rails.context_engineer import ContextProcessorRail; "
            "print('openjiuwen deepagent ready')\" "
            f"&& rm -f {remote_wheel}",
            user="root",
        )
        .set_envs({"OPENJIUWEN_SOURCE_REVISION": revision})
    )
    LOGGER.info("BUILD_ALIAS=%s", args.alias)
    LOGGER.info("OPENJIUWEN_REVISION=%s", revision)
    LOGGER.info("OPENJIUWEN_WHEEL_SHA256=%s", digest)
    Template.build(
        definition,
        args.alias,
        cpu_count=4,
        memory_mb=8192,
        on_build_logs=default_build_logger(),
        request_timeout=float(os.environ.get("E2B_BUILD_REQUEST_TIMEOUT_SECONDS", "7200")),
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
