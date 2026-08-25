# coding: utf-8
"""Run the WorkBuddy Office CompositeVerifier against an ACH Docker container.

This file is executed by the WorkBuddy Bench Python environment, not by the
agent-core interpreter. WorkBuddy currently requires Python 3.12+ while
agent-core also supports Python 3.11, so the verifier boundary is deliberately
process based.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any, Mapping

from workbuddy_bench.judge import EvaluationContext
from workbuddy_bench.judge.registry import (
    RegistryBuildContext,
    build_default_context,
    load_verifier_contract,
    load_verifier_registry,
    maybe_await,
)


class DockerExecEnvironment:
    """Minimal Harbor-compatible command surface backed by ``docker exec``."""

    def __init__(self, container_name: str) -> None:
        self.container_name = container_name

    async def exec(
        self,
        *,
        command: str,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout_sec: int | None = None,
    ) -> Any:
        docker_command = ["docker", "exec"]
        for key, value in sorted((env or {}).items()):
            docker_command.extend(["-e", f"{key}={value}"])
        if cwd:
            docker_command.extend(["-w", str(cwd)])
        docker_command.extend([self.container_name, "bash", "-lc", command])
        try:
            completed = await asyncio.to_thread(
                subprocess.run,
                docker_command,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_sec,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"WorkBuddy verifier command timed out after {timeout_sec} seconds") from exc
        return SimpleNamespace(
            return_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


class DockerAttemptRuntime:
    """Runtime contract consumed by the Office dataset verifier plugin."""

    def __init__(
        self,
        *,
        container_name: str,
        task_id: str,
        host_verifier_dir: Path,
    ) -> None:
        self.environment = DockerExecEnvironment(container_name)
        self.container_name = container_name
        self.task_id = task_id
        self.tests_dir = "/tests"
        self.workspace = "/workspace"
        self.container_verifier_dir = "/logs/verifier"
        self.host_verifier_dir = host_verifier_dir

    def env(
        self,
        base: Mapping[str, str] | None = None,
        *,
        prepend_pythonpath: str | None = None,
    ) -> dict[str, str]:
        env = {str(key): str(value) for key, value in (base or {}).items()}
        env.update({key: value for key, value in os.environ.items() if key.startswith("WORKBUDDY_VERIFIER_")})
        if prepend_pythonpath:
            current = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{prepend_pythonpath}:{current}" if current else prepend_pythonpath
        return env

    def context(
        self,
        *,
        dataset_id: str,
        task_id: str | None = None,
        container_paths: Mapping[str, str] | None = None,
        host_paths: Mapping[str, str] | None = None,
        env: Mapping[str, str] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> EvaluationContext:
        return EvaluationContext(
            dataset_id=dataset_id,
            task_id=task_id or self.task_id,
            workspace=self.workspace,
            tests_dir=self.tests_dir,
            verifier_dir=self.container_verifier_dir,
            container_paths={str(key): str(value) for key, value in (container_paths or {}).items()},
            host_paths={str(key): str(value) for key, value in (host_paths or {}).items()},
            env={str(key): str(value) for key, value in (env or {}).items()},
            metadata=dict(metadata or {}),
        )

    async def download_verifier_dir(
        self,
        *,
        source_dir: str | None = None,
        target_dir: Path | None = None,
    ) -> None:
        target = target_dir or self.host_verifier_dir
        target.mkdir(parents=True, exist_ok=True)
        command = [
            "docker",
            "cp",
            f"{self.container_name}:{source_dir or self.container_verifier_dir}/.",
            str(target),
        ]
        completed = await asyncio.to_thread(
            subprocess.run,
            command,
            text=True,
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "failed to download WorkBuddy verifier artifacts: " + (completed.stderr or completed.stdout).strip()
            )


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    task_dir = Path(args.task_dir).expanduser().resolve()
    output_dir = Path(args.output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    contract = load_verifier_contract(task_dir)
    runtime = DockerAttemptRuntime(
        container_name=args.container_name,
        task_id=task_dir.name,
        host_verifier_dir=output_dir,
    )
    registry = load_verifier_registry(RegistryBuildContext(contract=contract, runtime=runtime, verifier=None))
    if registry.custom_verify is not None:
        raise RuntimeError(
            "WorkBuddy Office adapter requires the registry engine contract; custom_verify is not supported"
        )

    context = build_default_context(contract, runtime)
    if registry.prepare is not None:
        await maybe_await(registry.prepare(context))
    plan = await maybe_await(registry.plan_builder(context))
    score = await registry.engine().run(context, plan)
    if registry.finalize_score is not None:
        score = await maybe_await(registry.finalize_score(score, context, plan))

    payload = score.to_dict()
    (output_dir / "reward.json").write_text(
        json.dumps(payload["reward"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "score.json").write_text(
        json.dumps(payload["score"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "reward": score.reward,
        "test_status": score.test_status,
        "tests_passed": score.tests_passed,
        "tests_total": score.tests_total,
        "reward_json": str(output_dir / "reward.json"),
        "score_json": str(output_dir / "score.json"),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-dir", required=True)
    parser.add_argument("--container-name", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> int:
    result = asyncio.run(_run(_parse_args()))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
