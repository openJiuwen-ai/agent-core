# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""jiuwenswarm CLI exec harness for skill_train benchmarks.

jiuwenswarm CLI as the agent under evaluation:

1. ``prepare_workspace`` writes an isolated per-item workspace holding the
   skill (``.agents/skills/<name>/SKILL.md``), ``task.md`` and optional
   attachments / linked data directories.
2. ``run_jiuwenswarm_cli_exec`` runs ``jiuwenswarm chat --cwd <ws> --jsonl``
   as a subprocess and captures the Gateway event stream.
3. The event stream is parsed into compact trace steps and persisted next to
   the prediction directory (``jiuwenswarm_trace_steps.txt``) so the
   reflector sees real tool activity, not only the collapsed final answer.

The Gateway/AgentServer must already be running (``jiuwenswarm-start app``).
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from openjiuwen.core.common.logging import logger

TARGET_SKILL_NAME = "reflact-target"
EXEC_WORK_DIR_NAME = "jiuwenswarm_exec"
DEFAULT_CLI_PATH = "jiuwenswarm"
DEFAULT_CHAT_MODE = "code.normal"
DEFAULT_EMPTY_RESPONSE_RETRIES = 1

_STEP_TRUNCATE = 500
_RESULT_TRUNCATE = 200
_DEFAULT_MAX_CHARS = 4000
_ATTEMPT_HEADER_RE = re.compile(r"(?m)^={5,}.*={5,}\s*$")

# Exit codes of ``jiuwenswarm chat`` (see jiuwenswarm/cli/chat.py).
EXIT_GATEWAY_UNREACHABLE = 3
EXIT_INTERACTIVE_UNAVAILABLE = 4


# ── Configuration ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class JiuwenswarmExecConfig:
    """Runtime knobs for the jiuwenswarm CLI target."""

    cli_path: str = DEFAULT_CLI_PATH
    gateway_url: str = ""
    chat_mode: str = DEFAULT_CHAT_MODE
    instance_name: str = ""
    empty_response_retries: int = DEFAULT_EMPTY_RESPONSE_RETRIES


def _parse_int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def get_jiuwenswarm_exec_config() -> JiuwenswarmExecConfig:
    """Load CLI exec settings from environment variables."""
    return JiuwenswarmExecConfig(
        cli_path=os.getenv("JIUWENSWARM_CLI_PATH", "").strip() or DEFAULT_CLI_PATH,
        gateway_url=os.getenv("JIUWENSWARM_GATEWAY_URL", "").strip(),
        chat_mode=os.getenv("JIUWENSWARM_CHAT_MODE", "").strip() or DEFAULT_CHAT_MODE,
        instance_name=os.getenv("JIUWENSWARM_INSTANCE_NAME", "").strip(),
        empty_response_retries=max(
            0,
            _parse_int(os.getenv("EXEC_EMPTY_RESPONSE_RETRIES"), DEFAULT_EMPTY_RESPONSE_RETRIES),
        ),
    )


def configure_jiuwenswarm_exec(
    *,
    cli_path: str | None = None,
    gateway_url: str | None = None,
    chat_mode: str | None = None,
    instance_name: str | None = None,
    empty_response_retries: int | None = None,
) -> JiuwenswarmExecConfig:
    """Persist exec settings into the environment (worker processes inherit them).

    ``None`` leaves the current value untouched; empty strings clear it.
    """
    updates: dict[str, str] = {}
    if cli_path is not None:
        updates["JIUWENSWARM_CLI_PATH"] = str(cli_path).strip()
    if gateway_url is not None:
        updates["JIUWENSWARM_GATEWAY_URL"] = str(gateway_url).strip()
    if chat_mode is not None:
        updates["JIUWENSWARM_CHAT_MODE"] = str(chat_mode).strip()
    if instance_name is not None:
        updates["JIUWENSWARM_INSTANCE_NAME"] = str(instance_name).strip()
    if empty_response_retries is not None:
        updates["EXEC_EMPTY_RESPONSE_RETRIES"] = str(max(0, int(empty_response_retries)))
    for key, value in updates.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)
    return get_jiuwenswarm_exec_config()


def as_dict_config(cfg: JiuwenswarmExecConfig | None = None) -> dict[str, Any]:
    """Expose config as a plain dict (logging / tests)."""
    c = cfg or get_jiuwenswarm_exec_config()
    return {
        "cli_path": c.cli_path,
        "gateway_url": c.gateway_url,
        "chat_mode": c.chat_mode,
        "instance_name": c.instance_name,
        "empty_response_retries": c.empty_response_retries,
    }


# ── Workspace preparation ────────────────────────────────────────────────────


_EMPTY_SKILL_FALLBACK = "No additional dynamic guidance was provided for this task."
_DEFAULT_SKILL_DESCRIPTION = "Dynamic ReflACT skill for the current benchmark task."


def _frontmatter_block(fields: dict[str, str]) -> list[str]:
    """YAML-ish frontmatter lines (Agent Skills layout)."""
    rows = ["---"]
    rows.extend(f'{key}: "{value}"' for key, value in fields.items())
    rows.append("---")
    return rows


def render_skill_md(
    skill_content: str,
    *,
    name: str = TARGET_SKILL_NAME,
    description: str = _DEFAULT_SKILL_DESCRIPTION,
    preamble: str = "",
) -> str:
    """Wrap the trained skill body in Agent Skills frontmatter."""
    body = (skill_content or "").strip()
    if not body:
        body = _EMPTY_SKILL_FALLBACK
    parts = _frontmatter_block({"name": name, "description": description})
    parts.extend(["", "# ReflACT Target Skill", ""])
    intro = preamble.strip()
    if intro:
        parts.extend([intro, ""])
    parts.append(body)
    return "\n".join(parts) + "\n"


def _is_symlink_privilege_error(exc: OSError) -> bool:
    if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
        return True
    return exc.errno in {
        errno.EPERM,
        getattr(errno, "ENOTSUP", -1),
        getattr(errno, "EOPNOTSUPP", -1),
    }


def _link_or_copy(src: str, dst: str) -> None:
    src_abs = os.path.abspath(src)
    if os.path.lexists(dst):
        raise FileExistsError(f"link destination already exists: {dst} (from {src})")
    try:
        os.symlink(src_abs, dst, target_is_directory=os.path.isdir(src_abs))
    except OSError as exc:
        # Fail closed: only fall back for the symlink-privilege case.
        if not _is_symlink_privilege_error(exc):
            raise
        if os.path.isdir(src_abs):
            shutil.copytree(src_abs, dst)
        else:
            shutil.copy2(src_abs, dst)


def _reset_workdir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_under(root: Path, rel: str, content: str) -> Path:
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def _stage_attachments(root: Path, images: list[str]) -> list[str]:
    """Copy images into ``attachments/`` and return markdown bullet lines."""
    bullets: list[str] = []
    folder = root / "attachments"
    folder.mkdir(parents=True, exist_ok=True)
    for index, image in enumerate(images, 1):
        if not os.path.exists(image):
            raise FileNotFoundError(image)
        absolute = os.path.abspath(image)
        leaf = os.path.basename(absolute) or f"image_{index}"
        dest = folder / f"{index:02d}_{leaf}"
        shutil.copy2(absolute, dest)
        rel = dest.relative_to(root).as_posix()
        bullets.append(f"- `{rel}` (source: `{absolute}`)")
    return bullets


def prepare_workspace(
    *,
    work_dir: str,
    skill_md: str,
    task_text: str = "",
    task_filename: str = "task.md",
    images: list[str] | None = None,
    extra_files: dict[str, str] | None = None,
    copy_files: list[tuple[str, str]] | None = None,
    link_dirs: list[tuple[str, str]] | None = None,
    skill_name: str = TARGET_SKILL_NAME,
) -> tuple[str, str]:
    """Create an isolated per-item workspace.

    Layout::

        work_dir/
          .agents/skills/<skill_name>/SKILL.md
          task.md
          attachments/NN_<image>      (when images given)
          ATTACHMENTS.md              (when images given)
          <extra_files / copy_files / link_dirs>

    Returns ``(skill_path, task_path)``.
    """
    root = _reset_workdir(Path(work_dir))
    skill_path = _write_under(
        root,
        f".agents/skills/{skill_name}/SKILL.md",
        skill_md,
    )
    task_path = root / task_filename
    if task_text:
        task_path.write_text(task_text, encoding="utf-8")

    for rel_path, content in (extra_files or {}).items():
        _write_under(root, rel_path, content)

    for src, rel_dst in copy_files or []:
        dest = root / rel_dst
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    for src, rel_dst in link_dirs or []:
        dest = root / rel_dst
        dest.parent.mkdir(parents=True, exist_ok=True)
        _link_or_copy(src, str(dest))

    if images:
        bullets = _stage_attachments(root, images)
        manifest = (
            "# Attachments\n\n"
            "Use these local files when the task refers to attached images or documents.\n\n"
            + "\n".join(bullets)
            + "\n"
        )
        (root / "ATTACHMENTS.md").write_text(manifest, encoding="utf-8")
    return str(skill_path), str(task_path)


def exec_work_dir_for(out_root: str, item_id: str) -> str:
    """Return ``predictions/<safe_id>/jiuwenswarm_exec``."""
    from openjiuwen.agent_evolving.skill_train.utils import safe_fs_id

    return str(Path(out_root) / "predictions" / safe_fs_id(str(item_id)) / EXEC_WORK_DIR_NAME)


# ── Prompt shaping ───────────────────────────────────────────────────────────


def skill_relpath(skill_name: str = TARGET_SKILL_NAME) -> str:
    """Workspace-relative path of the injected skill file."""
    return f".agents/skills/{skill_name}/SKILL.md"


def default_exec_prompt(task_hint: str, *, skill_name: str = TARGET_SKILL_NAME) -> str:
    """Item-level prompt passed to the CLI (env adapters add *task_hint*)."""
    return (
        f"Read `{skill_relpath(skill_name)}` directly; do not call a Skill tool.\n"
        f"Read `task.md` and {task_hint.strip()}\n"
        "Return the final answer inside <answer>...</answer>."
    )


def _edit_policy_clause(allow_file_edits: bool) -> str:
    if allow_file_edits:
        return (
            "You may modify files in the workspace when the task asks you to create an artifact. "
        )
    return "Do not modify files. "


def _exec_prompt(prompt: str, *, allow_file_edits: bool, skill_name: str) -> str:
    skill_path = skill_relpath(skill_name)
    preamble = [
        "Use the workspace files to solve the task.",
        f"Read task.md and the skill at {skill_path} before answering.",
        "If ATTACHMENTS.md exists, read it and inspect the listed local files.",
        "Do not call a Skill tool; the ReflACT guidance is a local markdown file.",
        "Do not ask the user questions and do not request permission.",
        _edit_policy_clause(allow_file_edits),
        "Return only the final answer text, keeping any required <answer>...</answer> tags exactly.",
    ]
    return " ".join(preamble) + "\n\n" + prompt


def _retry_prompt(prompt: str, attempt: int, *, skill_name: str) -> str:
    if attempt <= 0:
        return prompt
    skill_path = skill_relpath(skill_name)
    nudge = (
        "Previous execution returned an empty final response. "
        f"Re-read task.md and {skill_path}. "
        "If ATTACHMENTS.md exists, use the listed files. "
        "Then produce the final answer inside <answer>...</answer>."
    )
    return f"{prompt}\n\n{nudge}"


# ── Event stream parsing ─────────────────────────────────────────────────────


def iter_jsonl_events(raw: str) -> Iterator[dict[str, Any]]:
    """Yield ``{"type": "event", ...}`` frames from ``--jsonl`` stdout.

    Non-JSON lines (stderr, attempt headers) are skipped.
    """
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(frame, dict) and frame.get("type") == "event":
            yield frame


def _payload(frame: dict[str, Any]) -> dict[str, Any]:
    payload = frame.get("payload")
    return payload if isinstance(payload, dict) else {}


def _is_content_final(payload: dict[str, Any]) -> bool:
    inner = str(payload.get("event_type") or "")
    return not inner or inner == "chat.final"


def extract_final_response(events: Iterable[dict[str, Any]]) -> tuple[str, str]:
    """Return ``(final_text, error)`` from one attempt's event frames.

    Prefers the last content-bearing ``chat.final``; falls back to the
    concatenated ``chat.delta`` stream when no final frame arrived.
    """
    final_text = ""
    deltas: list[str] = []
    error = ""
    for frame in events:
        event = str(frame.get("event") or "")
        payload = _payload(frame)
        if event == "chat.delta":
            content = payload.get("content")
            if isinstance(content, str):
                deltas.append(content)
        elif event == "chat.final":
            if payload.get("event_type") == "team.error":
                error = str(payload.get("error") or payload.get("message") or "team.error")
                continue
            if not _is_content_final(payload):
                continue
            content = payload.get("content")
            if isinstance(content, str) and content.strip():
                final_text = content
        elif event == "chat.error":
            error = str(payload.get("error") or payload.get("message") or "chat.error")
    if not final_text.strip():
        final_text = "".join(deltas)
    return final_text.strip(), error


def _truncate(text: Any, limit: int) -> str:
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"...[+{len(text) - limit} chars]"


def _jsonish(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


def _tool_call_fields(payload: dict[str, Any]) -> tuple[str, Any]:
    inner = payload.get("tool_call")
    if isinstance(inner, dict):
        name = inner.get("name") or inner.get("tool_name") or ""
        args = inner.get("arguments", inner.get("input"))
    else:
        name = payload.get("tool_name") or payload.get("name") or ""
        args = payload.get("arguments", payload.get("input"))
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            pass
    return str(name), args


def _tool_result_fields(payload: dict[str, Any]) -> tuple[str, Any, bool]:
    inner = payload.get("tool_result")
    source = inner if isinstance(inner, dict) else payload
    name = source.get("name") or source.get("tool_name") or ""
    result = source.get("result", source.get("output", source.get("content")))
    success = source.get("success")
    status = str(source.get("status") or "").lower()
    is_error = success is False or status in {"error", "failed", "cancelled"}
    return str(name), result, is_error


def _summarize_tool_call(name: str, args: Any) -> str:
    if isinstance(args, dict):
        if name in {"read_file", "ReadFile", "Read", "read"}:
            return f"{name} {args.get('path') or args.get('file_path') or ''}".rstrip()
        if name in {"bash", "Bash", "run_bash", "shell"}:
            return f"{name} {args.get('command', '')}".rstrip()
        if name in {"grep", "Grep"}:
            return f"{name} {args.get('pattern', '')}".rstrip()
        if name in {"glob", "Glob"}:
            return f"{name} {args.get('pattern', '') or args.get('glob', '')}".rstrip()
        if name in {"write_file", "edit_file", "Write", "Edit"}:
            return f"{name} {args.get('path') or args.get('file_path') or ''}".rstrip()
    return _truncate(f"{name} {_jsonish(args)}", _STEP_TRUNCATE)


def parse_jiuwenswarm_trace_steps(raw: str) -> list[dict[str, Any]]:
    """Flatten the ``--jsonl`` stream into ordered compact steps.

    Returns ``{"index", "type", "summary"}`` dicts where ``type`` is one of
    ``text`` / ``tool_call`` / ``tool_result`` / ``error``.
    ``chat.delta`` fragments are folded into one ``text`` step per attempt.
    Streamed ``chat.reasoning`` chunks are omitted (token-level noise that
    crowded out tool/answer steps under the char budget).
    """
    steps: list[dict[str, Any]] = []
    for chunk in _ATTEMPT_HEADER_RE.split(raw or ""):
        if chunk.strip():
            steps.extend(_parse_attempt_steps(chunk))
    for index, step in enumerate(steps, 1):
        step["index"] = index
    return steps


class _StepCollector:
    """Accumulates steps for one attempt, folding streamed deltas."""

    __slots__ = ("steps", "_delta_buf")

    def __init__(self) -> None:
        self.steps: list[dict[str, Any]] = []
        self._delta_buf: list[str] = []

    def add(self, kind: str, summary: str) -> None:
        self.steps.append({"type": kind, "summary": summary})

    def delta(self, content: Any) -> None:
        if isinstance(content, str):
            self._delta_buf.append(content)

    def flush_deltas(self) -> None:
        text = "".join(self._delta_buf).strip()
        self._delta_buf.clear()
        if text:
            self.add("text", _truncate(text, _STEP_TRUNCATE))

    def final_text(self, text: Any) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        summary = _truncate(text, _STEP_TRUNCATE)
        # The final frame usually repeats the streamed deltas verbatim.
        if self.steps and self.steps[-1]["type"] == "text" and self.steps[-1]["summary"] == summary:
            return
        self.add("text", summary)


def _parse_attempt_steps(chunk: str) -> list[dict[str, Any]]:
    collector = _StepCollector()
    for frame in iter_jsonl_events(chunk):
        event = str(frame.get("event") or "")
        payload = _payload(frame)
        if event == "chat.delta":
            collector.delta(payload.get("content"))
        elif event == "chat.reasoning":
            # Skip token-streamed reasoning; keep tools / text / errors only.
            continue
        elif event == "chat.tool_call":
            collector.flush_deltas()
            name, args = _tool_call_fields(payload)
            collector.add("tool_call", _summarize_tool_call(name, args))
        elif event == "chat.tool_result":
            name, result, is_error = _tool_result_fields(payload)
            summary = _truncate(_jsonish(result), _RESULT_TRUNCATE)
            if is_error:
                summary = f"[error] {summary}"
            collector.add("tool_result", f"{name}: {summary}".strip())
        elif event == "chat.final":
            collector.flush_deltas()
            if payload.get("event_type") == "team.error":
                collector.add("error", _truncate(payload.get("error") or "team.error", _STEP_TRUNCATE))
            elif _is_content_final(payload):
                collector.final_text(payload.get("content"))
        elif event == "chat.error":
            collector.flush_deltas()
            collector.add(
                "error",
                _truncate(payload.get("error") or payload.get("message") or "error", _STEP_TRUNCATE),
            )
    collector.flush_deltas()
    return collector.steps


def format_jiuwenswarm_trace_steps(raw: str, *, max_chars: int = _DEFAULT_MAX_CHARS) -> str:
    """Render parsed steps as ``[n] type: summary`` lines."""
    steps = parse_jiuwenswarm_trace_steps(raw)
    if not steps:
        return ""
    text = "\n".join(f"[{s['index']}] {s['type']}: {s['summary']}" for s in steps)
    if len(text) > max_chars:
        text = text[:max_chars] + "\n...[jiuwenswarm trace steps truncated]..."
    return text


def build_trace_summary(raw: str, response: str) -> str:
    """Short summary file."""
    counts = {"reasoning": 0, "tool_call": 0, "tool_result": 0, "error": 0}
    for frame in iter_jsonl_events(raw):
        event = str(frame.get("event") or "")
        prefix_len = len("chat.")
        key = event[prefix_len:] if event.startswith("chat.") else ""
        if key in counts:
            counts[key] += 1
    answer_format = "missing"
    if "<answer>" in (response or "").lower():
        answer_format = "tagged"
    elif (response or "").strip():
        answer_format = "plain_text"
    errors: list[str] = []
    for line in (raw or "").splitlines():
        lowered = line.lower()
        if not line.startswith("{") and ("error" in lowered or "traceback" in lowered):
            errors.append(line.strip())
        if len(errors) >= 3:
            break
    return "\n".join(
        [
            "JiuwenSwarm CLI Trace Summary",
            f"- final answer format: {answer_format}",
            f"- final response chars: {len(response or '')}",
            f"- reasoning events: {counts['reasoning']}",
            f"- tool calls: {counts['tool_call']}",
            f"- tool results: {counts['tool_result']}",
            f"- error events: {counts['error']}",
            f"- stderr errors: {' | '.join(errors) if errors else 'none'}",
        ]
    )


def persist_trace_artifacts(work_dir: str, raw: str, response: str) -> str:
    """Write ``jiuwenswarm_raw.txt`` / ``_trace_summary.txt`` / ``_trace_steps.txt``.

    Files land in ``dirname(work_dir)`` (the prediction dir). Raw output is
    appended across turns with ``===== TURN BREAK =====``; steps are rebuilt
    from the combined raw and always overwritten. Returns the combined raw.
    """
    pred_dir = Path(work_dir.rstrip("/\\")).parent
    pred_dir.mkdir(parents=True, exist_ok=True)
    raw_path = pred_dir / "jiuwenswarm_raw.txt"
    combined = raw
    if raw_path.exists():
        prev = raw_path.read_text(encoding="utf-8")
        if prev.strip():
            combined = f"{prev}\n\n===== TURN BREAK =====\n\n{raw}"
    raw_path.write_text(combined, encoding="utf-8")
    (pred_dir / "jiuwenswarm_trace_summary.txt").write_text(build_trace_summary(combined, response), encoding="utf-8")
    (pred_dir / "jiuwenswarm_trace_steps.txt").write_text(format_jiuwenswarm_trace_steps(combined), encoding="utf-8")
    return combined


# ── Subprocess execution ─────────────────────────────────────────────────────


class GatewayUnreachableError(RuntimeError):
    """``jiuwenswarm chat`` could not connect to the Gateway (exit code 3)."""


def build_cli_command(
    *,
    work_dir: str,
    prompt: str,
    cfg: JiuwenswarmExecConfig,
) -> list[str]:
    """Assemble the headless ``jiuwenswarm chat --jsonl`` command."""
    ws = str(Path(work_dir).resolve())
    cmd = [
        cfg.cli_path,
        "chat",
        "--cwd",
        ws,
        "--project-dir",
        ws,
        "--mode",
        cfg.chat_mode or DEFAULT_CHAT_MODE,
        "--jsonl",
    ]
    if cfg.gateway_url:
        cmd.extend(["--gateway-url", cfg.gateway_url])
    if cfg.instance_name:
        cmd.extend(["--name", cfg.instance_name])
    # ``--trusted-dir`` is intentionally omitted: it is persisted into the
    # user's config.yaml and would leak per-item workspaces globally.
    cmd.extend(["--", prompt])
    return cmd


def _run_once(
    *,
    work_dir: str,
    prompt: str,
    timeout: int,
    cfg: JiuwenswarmExecConfig,
) -> tuple[str, str]:
    cmd = build_cli_command(work_dir=work_dir, prompt=prompt, cfg=cfg)
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        raw = stdout
        if stderr:
            raw = f"{raw}\n[stderr]\n{stderr}" if raw else f"[stderr]\n{stderr}"
        return "", f"{raw}\n[timeout] jiuwenswarm chat exceeded {timeout}s"
    except OSError as exc:
        raise RuntimeError(f"jiuwenswarm CLI could not be executed ({cfg.cli_path}): {exc}") from exc

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    raw = stdout
    if stderr.strip():
        raw = f"{raw}\n[stderr]\n{stderr}" if raw else f"[stderr]\n{stderr}"
    raw = f"{raw}\n[exit_code] {proc.returncode}"

    if proc.returncode == EXIT_GATEWAY_UNREACHABLE:
        raise GatewayUnreachableError(
            "jiuwenswarm chat could not reach the Gateway "
            f"({cfg.gateway_url or 'default ws://127.0.0.1:19001/tui'}); "
            "start it with `jiuwenswarm-start app`."
        )

    response, error = extract_final_response(iter_jsonl_events(stdout))
    if proc.returncode == EXIT_INTERACTIVE_UNAVAILABLE and not response:
        logger.warning("jiuwenswarm chat requested user input in headless mode (work_dir=%s)", work_dir)
    if error and not response:
        logger.warning("jiuwenswarm chat reported an error: %s", error[:300])
    return response, raw


def run_jiuwenswarm_cli_exec(
    *,
    work_dir: str,
    prompt: str,
    timeout: int,
    allow_file_edits: bool = False,
    skill_name: str = TARGET_SKILL_NAME,
    cfg: JiuwenswarmExecConfig | None = None,
) -> tuple[str, str]:
    """Run the jiuwenswarm agent once in *work_dir* and persist trace artifacts.

    Returns ``(final_response, combined_raw)``. An empty response is retried
    ``empty_response_retries`` times with an explicit re-read hint.
    Never raises on timeout or agent-side
    errors (the caller scores an empty answer); raises
    :class:`GatewayUnreachableError` when the Gateway is down so a whole
    batch fails fast instead of silently scoring zero.
    """
    config = cfg or get_jiuwenswarm_exec_config()
    retries = int(config.empty_response_retries)
    all_raw: list[str] = []
    last_response = ""
    for attempt in range(retries + 1):
        shaped = _retry_prompt(prompt, attempt, skill_name=skill_name)
        attempt_prompt = _exec_prompt(
            shaped,
            allow_file_edits=allow_file_edits,
            skill_name=skill_name,
        )
        response, raw = _run_once(
            work_dir=work_dir,
            prompt=attempt_prompt,
            timeout=timeout,
            cfg=config,
        )
        all_raw.append(f"===== JIUWENSWARM ATTEMPT {attempt + 1} =====\n{raw}")
        last_response = response
        if response.strip():
            break
    merged_raw = "\n\n".join(all_raw)
    persist_trace_artifacts(work_dir, merged_raw, last_response)
    return last_response, merged_raw


def with_overrides(cfg: JiuwenswarmExecConfig, **changes: Any) -> JiuwenswarmExecConfig:
    """Return a copy of *cfg* with non-``None`` *changes* applied."""
    kept = {key: value for key, value in changes.items() if value is not None}
    if not kept:
        return cfg
    return replace(cfg, **kept)
