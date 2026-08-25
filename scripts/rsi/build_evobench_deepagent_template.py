"""Build the versioned APEX template used by the DeepAgent Evo-Bench adapter."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import subprocess


WORKSPACE_ROOT = Path(r"D:\code\code1\agent-core")
UPSTREAM_ROOT = Path(r"D:\code\code1\agent-core-upstream-latest")
EVOBENCH_ROOT = Path(r"D:\code\code1\Evo-Bench-official\Evo-Bench-main")
EXPECTED_REVISION = "da021f994908e6459177f408bbbfbd71e9f43d83"
DEFAULT_ALIAS = "evobench-apex-openjiuwen-da021f994"


def _revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=UPSTREAM_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _build_wheel() -> Path:
    revision = _revision()
    if revision != EXPECTED_REVISION:
        raise RuntimeError(f"latest-source revision changed: expected {EXPECTED_REVISION}, found {revision}")
    output_dir = WORKSPACE_ROOT / ".local" / "rsi_runtime" / "dist"
    output_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(output_dir)],
        cwd=UPSTREAM_ROOT,
        check=True,
    )
    wheels = sorted(output_dir.glob("openjiuwen-*.whl"), key=lambda item: item.stat().st_mtime)
    if not wheels:
        raise RuntimeError("openJiuwen wheel build produced no artifact")
    return wheels[-1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--alias", default=DEFAULT_ALIAS)
    args = parser.parse_args()

    try:
        from e2b import Template, default_build_logger
    except ImportError as exc:
        raise RuntimeError("run this script with the Evo-Bench virtual environment") from exc

    wheel = _build_wheel()
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    remote_wheel = f"/tmp/{wheel.name}"
    # Extend the already-published official APEX image. Rebuilding the whole
    # split archive is both wasteful and susceptible to E2B COPY cache races.
    definition = (
        Template(file_context_path=str(wheel.parent))
        .from_template("evobench-apex-spec")
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
        .set_envs({"OPENJIUWEN_SOURCE_REVISION": EXPECTED_REVISION})
    )
    print(f"BUILD_ALIAS={args.alias}")
    print(f"OPENJIUWEN_REVISION={EXPECTED_REVISION}")
    print(f"OPENJIUWEN_WHEEL_SHA256={digest}")
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
    raise SystemExit(main())
