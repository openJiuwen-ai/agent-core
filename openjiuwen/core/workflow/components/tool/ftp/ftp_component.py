# coding=utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import re
from typing import Dict, Any, AsyncIterator, Optional

from openjiuwen.core.common.logging import workflow_logger, LogEventType
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.components.component import ComponentExecutable, ComponentComposable
from openjiuwen.core.workflow.components.tool.ftp.ftp_config import (
    FTPComponentConfig,
    FTPOperationConfig,
    FTPCredentials,
    FTPConnectionConfig,
    FTPSSLConfig,
)
from openjiuwen.core.workflow.components.tool.ftp.ftp_service import FTPService


class FTPExecutable(ComponentExecutable):
    """Executable component for FTP operations."""

    # Singleton FTP service instance
    _ftp_service: Optional[FTPService] = None

    def __init__(self, config: FTPComponentConfig):
        super().__init__()
        self.config = config

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """
        Execute all FTP operations sequentially and return aggregated result.

        Args:
            inputs: Input data with potential variable overrides
            session: Current execution session
            context: Context engine for variable resolution

        Returns:
            Output: Dict with ok, operations[], connection, and error fields
        """
        # Ensure FTP service is initialized
        service = await self._ensure_ftp_service()

        # Resolve credentials and config from inputs/context
        credentials = self._resolve_credentials(inputs, context)
        connection_config = self._resolve_connection_config(inputs)
        ssl_config = self._resolve_ssl_config(inputs)
        operations = self._resolve_operations(inputs, context)

        # Execute all operations
        operation_results = []
        for operation in operations:
            try:
                result = await service.execute_operation(
                    credentials,
                    connection_config,
                    ssl_config,
                    operation,
                )
                operation_results.append(result)
            except Exception as e:
                workflow_logger.error(f"FTP operation failed: {e}", event_type=LogEventType.WORKFLOW_COMPONENT_ERROR)
                operation_results.append({
                    "ok": False,
                    "operation": operation.operation_type.value,
                    "data": None,
                    "error": str(e),
                })

        # Format output
        return self._format_output(credentials, operation_results)

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        """
        Execute FTP operations and yield results one by one.

        Args:
            inputs: Input data
            session: Current execution session
            context: Context engine

        Yields:
            Output: Individual operation results
        """
        # Ensure FTP service is initialized
        service = await self._ensure_ftp_service()

        # Resolve credentials and config
        credentials = self._resolve_credentials(inputs, context)
        connection_config = self._resolve_connection_config(inputs)
        ssl_config = self._resolve_ssl_config(inputs)
        operations = self._resolve_operations(inputs, context)

        # Execute operations and yield each result
        for operation in operations:
            try:
                result = await service.execute_operation(
                    credentials,
                    connection_config,
                    ssl_config,
                    operation,
                )
                yield {
                    "ok": result["ok"],
                    "operation": result["operation"],
                    "data": result["data"],
                    "error": result.get("error"),
                }
            except Exception as e:
                workflow_logger.error(f"FTP operation failed: {e}", event_type=LogEventType.WORKFLOW_COMPONENT_ERROR)
                yield {
                    "ok": False,
                    "operation": operation.operation_type.value,
                    "data": None,
                    "error": str(e),
                }

    async def collect(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        """
        Collect streaming inputs and execute FTP operations.

        For FTP operations, this delegates to invoke as operations are batch-oriented.

        Args:
            inputs: Input data (may be streaming)
            session: Current execution session
            context: Context engine

        Returns:
            Output: Aggregated operation results
        """
        return await self.invoke(inputs, session, context)

    async def transform(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        """
        Transform streaming input to streaming output.

        For FTP operations, this delegates to stream.

        Args:
            inputs: Streaming input data
            session: Current execution session
            context: Context engine

        Yields:
            Output: Individual operation results
        """
        async for result in self.stream(inputs, session, context):
            yield result

    @classmethod
    async def _ensure_ftp_service(cls) -> FTPService:
        """Lazy initialization of FTP service singleton."""
        if cls._ftp_service is None:
            cls._ftp_service = FTPService()
        return cls._ftp_service

    def _resolve_credentials(self, inputs: Dict[str, Any], context: ModelContext) -> FTPCredentials:
        """Resolve credentials from config and inputs."""
        # Start with config credentials
        creds = self.config.credentials

        # Allow inputs to override credentials
        if inputs and "credentials" in inputs:
            input_creds = inputs["credentials"]
            if isinstance(input_creds, dict):
                creds = FTPCredentials(
                    host=input_creds.get("host", creds.host),
                    port=input_creds.get("port", creds.port),
                    username=input_creds.get("username", creds.username),
                    password=input_creds.get("password", creds.password),
                    private_key=input_creds.get("private_key", creds.private_key),
                    private_key_passphrase=input_creds.get("private_key_passphrase", creds.private_key_passphrase),
                    protocol=input_creds.get("protocol", creds.protocol),
                )

        # Resolve ${variable} placeholders in credential fields
        if context:
            creds = FTPCredentials(
                host=self._resolve_template(creds.host, context),
                port=creds.port,
                username=self._resolve_template(creds.username, context),
                password=self._resolve_template(creds.password, context) if creds.password else None,
                private_key=self._resolve_template(creds.private_key, context) if creds.private_key else None,
                private_key_passphrase=self._resolve_template(
                    creds.private_key_passphrase, context
                ) if creds.private_key_passphrase else None,
                protocol=creds.protocol,
            )

        return creds

    def _resolve_connection_config(self, inputs: Dict[str, Any]) -> FTPConnectionConfig:
        """Resolve connection config from config and inputs."""
        conn_config = self.config.connection

        # Allow inputs to override connection config
        if inputs and "connection" in inputs:
            input_conn = inputs["connection"]
            if isinstance(input_conn, dict):
                conn_config = FTPConnectionConfig(
                    timeout=input_conn.get("timeout", conn_config.timeout),
                    passive_mode=input_conn.get("passive_mode", conn_config.passive_mode),
                    encoding=input_conn.get("encoding", conn_config.encoding),
                    keepalive_interval=input_conn.get("keepalive_interval", conn_config.keepalive_interval),
                    max_retries=input_conn.get("max_retries", conn_config.max_retries),
                    retry_delay=input_conn.get("retry_delay", conn_config.retry_delay),
                )

        return conn_config

    def _resolve_ssl_config(self, inputs: Dict[str, Any]) -> Optional[FTPSSLConfig]:
        """Resolve SSL config from config and inputs."""
        ssl_config = self.config.ssl_config

        # Allow inputs to override SSL config
        if inputs and "ssl_config" in inputs:
            input_ssl = inputs["ssl_config"]
            if isinstance(input_ssl, dict):
                ssl_config = FTPSSLConfig(
                    verify_ssl=input_ssl.get("verify_ssl", ssl_config.verify_ssl if ssl_config else True),
                    ssl_version=input_ssl.get(
                        "ssl_version",
                        ssl_config.ssl_version if ssl_config else None
                    ),
                    cert_file=input_ssl.get("cert_file", ssl_config.cert_file if ssl_config else None),
                    key_file=input_ssl.get("key_file", ssl_config.key_file if ssl_config else None),
                )

        return ssl_config

    def _resolve_operations(
        self,
        inputs: Dict[str, Any],
        context: ModelContext
    ) -> list[FTPOperationConfig]:
        """Resolve operations from config and inputs."""
        # Use operations from inputs if provided, otherwise use config
        operations = inputs.get("operations", self.config.operations) if inputs else self.config.operations

        # Resolve ${variable} placeholders in operation configs
        if context:
            resolved_ops = []
            for op in operations:
                # Deep copy operation and resolve templates in path fields
                resolved_op = self._resolve_operation_templates(op, context)
                resolved_ops.append(resolved_op)
            return resolved_ops

        return operations

    def _resolve_operation_templates(
        self,
        operation: FTPOperationConfig,
        context: ModelContext
    ) -> FTPOperationConfig:
        """Resolve ${variable} templates in operation configuration."""
        # This is a simplified version - in production, you'd want to handle all config types
        # For now, we'll just resolve the most common path fields

        if operation.upload:
            operation.upload.local_path = self._resolve_template(
                operation.upload.local_path, context
            ) if operation.upload.local_path else None
            operation.upload.remote_path = self._resolve_template(operation.upload.remote_path, context)
            operation.upload.content = self._resolve_template(
                operation.upload.content, context
            ) if operation.upload.content else None

        if operation.download:
            operation.download.remote_path = self._resolve_template(operation.download.remote_path, context)
            operation.download.local_path = self._resolve_template(
                operation.download.local_path, context
            ) if operation.download.local_path else None

        if operation.list:
            operation.list.remote_path = self._resolve_template(operation.list.remote_path, context)

        if operation.delete:
            operation.delete.remote_path = self._resolve_template(operation.delete.remote_path, context)

        if operation.rename:
            operation.rename.old_path = self._resolve_template(operation.rename.old_path, context)
            operation.rename.new_path = self._resolve_template(operation.rename.new_path, context)

        if operation.create_directory:
            operation.create_directory.remote_path = self._resolve_template(
                operation.create_directory.remote_path, context
            )

        if operation.get_info:
            operation.get_info.remote_path = self._resolve_template(operation.get_info.remote_path, context)

        return operation

    def _resolve_template(self, template: Optional[str], context: ModelContext) -> Optional[str]:
        """Resolve ${variable} placeholders in a string using context."""
        if not template or not context:
            return template

        # Simple template resolution: ${workflow.output.key} or ${env.KEY}
        # This is a simplified implementation
        def replace_var(match):
            var_path = match.group(1)
            # Try to resolve from context
            try:
                # Split path and traverse context
                parts = var_path.split(".")
                value = context
                for part in parts:
                    if hasattr(value, part):
                        value = getattr(value, part)
                    elif isinstance(value, dict):
                        value = value.get(part)
                    else:
                        return match.group(0)  # Return original if not found
                return str(value)
            except (AttributeError, KeyError, TypeError):
                return match.group(0)  # Return original if resolution fails

        # Match ${...} patterns
        return re.sub(r'\$\{([^}]+)\}', replace_var, template)

    def _format_output(self, credentials: FTPCredentials, results: list[Dict[str, Any]]) -> Dict[str, Any]:
        """Format aggregated output from all operations."""
        # Overall success if all operations succeeded
        all_ok = all(r.get("ok", False) for r in results)

        # Collect errors
        errors = [r.get("error") for r in results if r.get("error")]
        error_msg = "; ".join(errors) if errors else None

        return {
            "ok": all_ok,
            "operations": results,
            "connection": {
                "host": credentials.host,
                "protocol": credentials.protocol.value,
                "connected": True,
            },
            "error": error_msg,
        }


class FTPComponent(ComponentComposable):
    """FTP workflow component for file transfer operations."""

    def __init__(self, config: FTPComponentConfig):
        super().__init__()
        self.__config = config  # Private attribute per coding standards
        self._executable = None

    @property
    def config(self) -> FTPComponentConfig:
        """Public getter for component configuration."""
        return self.__config

    @property
    def executable(self) -> FTPExecutable:
        """Lazy-loaded executable instance."""
        if self._executable is None:
            self._executable = self.to_executable()
        return self._executable

    def to_executable(self) -> FTPExecutable:
        """Convert this component to an executable instance."""
        return FTPExecutable(self.config)
