# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Any, AsyncIterator, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.sys_operation.config import GatewayInvokeRequest, SandboxCreateRequest, SandboxGatewayConfig
from openjiuwen.core.sys_operation.result.base_result import BaseResult
from openjiuwen.core.sys_operation.sandbox.gateway.gateway import SandboxGateway, GatewayResponse, SandboxEndpoint


class SandboxGatewayClient:
    """Client wrapper for sandbox gateway — supports both endpoint resolution and full-chain invoke."""

    def __init__(
            self,
            *,
            config: SandboxGatewayConfig,
            isolation_key: Optional[str],
            gateway: Optional[SandboxGateway] = None
    ):
        self._config = config
        self._isolation_key = isolation_key
        self._gateway = gateway or SandboxGateway.get_instance()
        self._local_operation = None

    async def prepare(self, op_type: str, *, create: bool = True) -> Any:
        """Check provider lifecycle barriers before optionally preparing an instance.

        A trusted ``runtime_guard`` launcher callback may use this before dispatch.
        Preparation never executes the requested tool operation.
        """
        provider = await self._gateway._get_or_create_provider(self._config, self._isolation_key, op_type)
        check = getattr(provider, "_assert_policy_ready", None)
        if check is not None:
            check()
        prepare = getattr(provider, "prepare_for_execution", None)
        if create and prepare is not None:
            await prepare()
        return provider

    async def _local_target(self, op_type: str, method: str, params: dict):
        extra = self._config.launcher_config.extra_params or {}
        guard = extra.get("runtime_guard")
        if guard is None:
            return None
        work_config = await guard(self, op_type, method=method, params=params)
        if work_config is None:
            return None
        from openjiuwen.core.sys_operation import LocalWorkConfig, OperationMode, SysOperation, SysOperationCard

        if not isinstance(work_config, LocalWorkConfig):
            raise TypeError("runtime_guard must return LocalWorkConfig or None")
        if self._local_operation is None:
            self._local_operation = SysOperation(SysOperationCard(mode=OperationMode.LOCAL, work_config=work_config))
        return getattr(getattr(self._local_operation, op_type)(), method)

    def _record_error(self, error: Exception) -> None:
        observer = (self._config.launcher_config.extra_params or {}).get("runtime_error_handler")
        if observer is not None:
            try:
                observer(error)
            except Exception:
                # Diagnostics must not replace the actual execution error.
                pass

    # ── Full-chain routing API ──

    async def invoke(self, op_type: str, method: str, **params) -> Any:
        """Send an invoke request through the gateway full-chain routing."""
        local = await self._local_target(op_type, method, params)
        if local is not None:
            return await local(**params)
        request = GatewayInvokeRequest(
            op_type=op_type, method=method, params=params, isolation_key=self._isolation_key,
        )
        try:
            response = await self._gateway.handle_request(self._config, request)
            self._raise_if_failed(response)
            self._record_result_error(response.data)
            return response.data
        except Exception as exc:
            self._record_error(exc)
            raise

    async def invoke_stream(self, op_type: str, method: str, **params) -> AsyncIterator:
        """Send a streaming invoke request through the gateway full-chain routing."""
        local = await self._local_target(op_type, method, params)
        if local is not None:
            async for item in local(**params):
                yield item
            return
        request = GatewayInvokeRequest(
            op_type=op_type, method=method, params=params, isolation_key=self._isolation_key,
        )
        try:
            stream = await self._gateway.handle_stream_request(self._config, request)
            async for item in stream:
                self._record_result_error(item)
                yield item
        except Exception as exc:
            self._record_error(exc)
            raise

    def _record_result_error(self, result: Any) -> None:
        if isinstance(result, BaseResult) and result.code != 0:
            self._record_error(RuntimeError(result.message))

    # ── Legacy endpoint-only API (kept for backward compatibility) ──

    async def get_endpoint(self) -> SandboxEndpoint:
        request = SandboxCreateRequest(isolation_key=self._isolation_key, config=self._config)
        response = await self._gateway.get_sandbox(request=request)
        self._raise_if_failed(response)

        endpoint = response.data
        if isinstance(endpoint, SandboxEndpoint):
            return endpoint
        if isinstance(endpoint, dict):
            return SandboxEndpoint(**endpoint)
        raise TypeError(f"Invalid endpoint payload: {type(endpoint).__name__}")

    @staticmethod
    async def release(isolation_key: str, on_stop: str = "delete") -> None:
        """Static release method: Only the key is required to notify the gateway to reclaim the resources."""
        gateway = SandboxGateway.get_instance()
        response = await gateway.release_sandbox(isolation_key, on_stop=on_stop)
        SandboxGatewayClient._raise_if_failed(response)

    @staticmethod
    def _raise_if_failed(response: GatewayResponse) -> None:
        legacy_success = getattr(response, "success", None)
        if legacy_success is not None:
            success = bool(legacy_success)
        else:
            success = getattr(response, "code", 1) == 0

        if success:
            return

        error_msg = getattr(response, "error", None) or getattr(response, "message", "unknown error")

        raise build_error(
            status=StatusCode.SYS_OPERATION_SANDBOX_GATEWAY_ERROR,
            operation=f"gateway_{getattr(response, 'op_type', 'unknown')}",
            error_msg=error_msg
        )
