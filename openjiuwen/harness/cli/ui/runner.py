"""Non-interactive run mode.

Supports three output formats:
- ``text``        — rendered to terminal (same as REPL)
- ``json``        — single JSON object with result + metadata
- ``stream-json`` — JSONL, one line per chunk
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

from rich.console import Console

from openjiuwen.harness.cli.agent.config import CLIConfig
from openjiuwen.harness.cli.agent.factory import create_backend
from openjiuwen.harness.cli.ui.renderer import (
    CHUNK_ANSWER,
    CHUNK_LLM_OUTPUT,
    _extract_content,
    render_stream,
)


def _write_terminal(text: str) -> None:
    """Write *text* directly to stdout (CLI user output)."""
    os.write(1, text.encode())


async def run_once(
    cfg: CLIConfig,
    prompt: str,
    output_format: str = "text",
) -> int:
    """Execute a single non-interactive query.

    Args:
        cfg: CLI configuration.
        prompt: User prompt text.
        output_format: ``"text"`` | ``"json"`` | ``"stream-json"``.

    Returns:
        Exit code: ``0`` on success, ``1`` on error.
    """
    backend = create_backend(cfg)
    try:
        await backend.start()
        stream = backend.run_streaming(prompt)

        if output_format == "text":
            return await _output_text(stream)
        elif output_format == "json":
            return await _output_json(stream, cfg)
        elif output_format == "stream-json":
            return await _output_stream_json(stream)
        else:
            _print_error(
                ValueError(f"Unknown output format: {output_format}")
            )
            return 1
    except Exception as exc:  # noqa: BLE001
        _print_error(exc)
        return 1
    finally:
        await backend.stop()


async def _output_text(
    stream: AsyncIterator[Any],
) -> int:
    """Render stream to terminal (same as REPL)."""
    console = Console()
    await render_stream(stream, console)
    return 0


async def _output_json(
    stream: AsyncIterator[Any],
    cfg: CLIConfig,
) -> int:
    """Collect all chunks and output a single JSON object."""
    result_text = ""
    chunk_count = 0
    has_llm_output = False
    async for chunk in stream:
        chunk_count += 1
        chunk_type = getattr(chunk, "type", "")
        if chunk_type == CHUNK_LLM_OUTPUT:
            has_llm_output = True
            result_text += _extract_content(chunk)
        elif chunk_type == CHUNK_ANSWER and not has_llm_output:
            result_text += _extract_content(chunk)

    output = {
        "result": result_text,
        "chunks": chunk_count,
        "model": cfg.model,
    }
    _write_terminal(
        json.dumps(output, ensure_ascii=False, indent=2)
    )
    _write_terminal("\n")
    return 0


async def _output_stream_json(
    stream: AsyncIterator[Any],
) -> int:
    """Output each chunk as a JSONL line."""
    async for chunk in stream:
        payload = chunk.payload
        line = {
            "type": chunk.type,
            "index": chunk.index,
            "payload": (
                payload
                if isinstance(payload, (str, dict))
                else str(payload)
            ),
        }
        _write_terminal(json.dumps(line, ensure_ascii=False))
        _write_terminal("\n")
    return 0


def _print_error(error: Exception) -> None:
    """Convert API errors to user-friendly messages."""
    msg = str(error).lower()
    if "rate_limit" in msg or "429" in msg:
        hint = "Rate limited. Please try again later."
    elif "authentication" in msg or "401" in msg:
        hint = (
            "API Key invalid. Check OPENJIUWEN_API_KEY."
        )
    elif "too long" in msg or "context_length" in msg:
        hint = (
            "Context too long. Use /compact to trim history."
        )
    elif "timeout" in msg:
        hint = "Request timed out. Check your network."
    else:
        hint = f"Error: {error}"
    console = Console(stderr=True)
    console.print(f"[red]\u2717 {hint}[/red]")
