from typing import Optional

from openjiuwen.core.sys_operation.config import PreDeployLauncherConfig, SandboxLauncherConfig
from openjiuwen.core.sys_operation.sandbox.launchers.base import SandboxLauncher, LaunchedSandbox


class PreDeploymentLauncher(SandboxLauncher):
    async def launch(self, config: SandboxLauncherConfig, timeout_seconds: int,
                     isolation_key: Optional[str] = None) -> LaunchedSandbox:
        if not isinstance(config, PreDeployLauncherConfig):
            raise ValueError("PreDeploymentLauncher requires PreDeployLauncherConfig")
        return LaunchedSandbox(base_url=config.base_url)

    async def delete(self, sandbox_id: str, **kwargs) -> None:
        sandbox_type = kwargs.get("sandbox_type")
        isolation_key = kwargs.get("isolation_key")
        base_url = kwargs.get("base_url")

        if sandbox_type == "yuanrong":
            from openjiuwen.extensions.sys_operation.sandbox.providers.yuanrong import (
                build_yuanrong_shared_scope_key,
                delete_yuanrong_sandbox,
            )
            shared_key = (
                build_yuanrong_shared_scope_key(str(base_url), str(isolation_key))
                if base_url and isolation_key
                else None
            )
            await delete_yuanrong_sandbox(
                shared_key=shared_key,
                base_url=str(base_url) if base_url and not shared_key else None,
                reason="teardown",
            )

        elif sandbox_type in ("jiuwenbox", "jiuwenbox-conch"):
            from openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox import (
                build_jiuwenbox_shared_scope_key,
                delete_jiuwenbox_sandbox,
            )
            shared_key = (
                build_jiuwenbox_shared_scope_key(str(base_url), str(isolation_key))
                if base_url and isolation_key
                else None
            )
            await delete_jiuwenbox_sandbox(
                sandbox_id=sandbox_id or None,
                shared_key=shared_key,
                reason="teardown",
            )

        return None
