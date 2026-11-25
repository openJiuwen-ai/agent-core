"""Run a real local stdio MCP server and verify timeout/cancellation boundaries.

This probe uses only local stdio pipes and the repository's installed MCP SDK.
It never contacts an external service. Run it from the repository root with the
project virtual environment; it exits non-zero on any contract breach.
"""

import asyncio
import sys


def _serve() -> None:
    """Keep framework imports and their logging out of the protocol subprocess."""
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("local-timeout-probe")

    @server.tool()
    async def wait_forever() -> str:
        """Keep the server operation pending until the client deadline expires."""
        await asyncio.Event().wait()
        return "unreachable"

    server.run(transport="stdio")


def make_client():
    from openjiuwen.core.foundation.tool import McpServerConfig
    from openjiuwen.core.foundation.tool.mcp.client.stdio_client import StdioClient

    return StdioClient(
        McpServerConfig(
            server_name="local-timeout-probe",
            client_type="stdio",
            server_path="local-timeout-probe",
            params={
                "command": sys.executable,
                "args": [__file__, "--serve"],
            },
        )
    )


async def verify_call_timeout() -> None:
    from openjiuwen.core.foundation.tool import McpConnectionError, McpTimeoutError

    client = make_client()
    try:
        connected = await client.connect(timeout=10.0)
        assert connected is True, client._last_connect_error
        try:
            await client.call_tool("wait_forever", {}, timeout=0.1)
        except McpTimeoutError as exc:
            assert exc.code == 182302
            assert exc.params["operation"] == "call_tool"
            assert isinstance(exc, McpConnectionError)
            assert exc.cause is not None
        else:
            raise AssertionError("call_tool did not raise McpTimeoutError")
    finally:
        disconnected = await client.disconnect(timeout=3.0)
        assert disconnected is True, "stdio disconnect did not complete"


async def verify_caller_cancellation() -> None:
    client = make_client()
    task = asyncio.create_task(client.connect(timeout=10.0))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        from openjiuwen.core.foundation.tool import McpTimeoutError

        if isinstance(exc, McpTimeoutError):
            raise AssertionError("caller cancellation became McpTimeoutError") from exc
        raise
    else:
        raise AssertionError("caller cancellation was swallowed")
    disconnected = await client.disconnect(timeout=3.0)
    assert disconnected is True, "cancelled connection did not clean up"


async def main() -> None:
    await verify_call_timeout()
    await verify_caller_cancellation()
    print("local stdio MCP timeout and caller-cancellation probes passed")


if __name__ == "__main__":
    if "--serve" in sys.argv:
        _serve()
    else:
        asyncio.run(main())
