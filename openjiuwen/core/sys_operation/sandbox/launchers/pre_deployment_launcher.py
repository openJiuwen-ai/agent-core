from typing import Optional

from openjiuwen.core.sys_operation.config import PreDeployLauncherConfig, SandboxLauncherConfig
from openjiuwen.core.sys_operation.sandbox.launchers.base import SandboxLauncher, LaunchedSandbox


class PreDeploymentLauncher(SandboxLauncher):
    async def launch(self, config: SandboxLauncherConfig, timeout_seconds: int,
                     isolation_key: Optional[str] = None) -> LaunchedSandbox:
        if not isinstance(config, PreDeployLauncherConfig):
            raise ValueError("PreDeploymentLauncher requires PreDeployLauncherConfig")
        return LaunchedSandbox(base_url=config.base_url)

    async def delete(self, sandbox_id: str) -> None:
        from openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox import (
            delete_jiuwenbox_sandbox,
        )
        await delete_jiuwenbox_sandbox(reason="teardown")
        return None