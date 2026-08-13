# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Smoke test for the Yuanrong sandbox SDK flow used by scheduler plugins."""

from __future__ import annotations

import os

from openjiuwen.agent_evolving.agent_rl.online.sandbox import (
    YuanrongSandboxConfig,
    YuanrongSandboxManager,
)

DEFAULT_IMAGE = (
    "swe.cn-east-3.myhuaweicloud.com/openyuanrong/"
    "swe-sweb.eval.x86_64.astropy_1776_astropy-12907:latest"
)


def _sandbox_env() -> dict[str, str]:
    keys = (
        "DEPLOYMENT",
        "AKERNEL_SERVER_ADDRESS",
        "OPENYUANRONG_SERVER_ADDRESS",
        "AKERNEL_TOKEN",
    )
    env = {key: value for key in keys if (value := os.getenv(key))}
    env.setdefault("DEPLOYMENT", "openyuanrong")
    return env


def main() -> None:
    image = os.getenv("ONLINE_RL_SANDBOX_IMAGE", DEFAULT_IMAGE)
    print("===== test1: create sandbox =======")
    print(f"image: {image}")
    manager = YuanrongSandboxManager(
        YuanrongSandboxConfig(
            image=image,
            cpu=int(os.getenv("ONLINE_RL_SANDBOX_CPU", "2000")),
            memory=int(os.getenv("ONLINE_RL_SANDBOX_MEMORY", "4096")),
            port_forwardings=[8000],
            idle_timeout=int(os.getenv("ONLINE_RL_SANDBOX_IDLE_TIMEOUT", "600")),
            env=_sandbox_env(),
            install_swerex=True,
        )
    )
    try:
        manager.create()
        print(f"sandbox create successful, id={manager.sandbox_id}")

        print("===== test2: basic command =======")
        result = manager.run("echo SANDBOX_OK && which python3 && python3 --version", timeout=30)
        print(f"stdout: {result.stdout}")
        print(f"stderr: {result.stderr}")
        print(f"exit_code: {result.exit_code}")

        print("===== test3: check swe-rex installed =======")
        result = manager.run("python3 -c 'import swerex; print(swerex.__version__)' 2>&1", timeout=30)
        print(f"stdout: {result.stdout}")
        print(f"stderr: {result.stderr}")
        print(f"exit_code: {result.exit_code}")

        print("===== test4-6: install and start swerex =======")
        url = manager.ensure_swerex_server()
        print(f"Port Url: {url}")
    finally:
        manager.close()


if __name__ == "__main__":
    main()
