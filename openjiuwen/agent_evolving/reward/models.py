"""Model access: a streamed JSON client with per-call quotas, the answer language, the session's model, and
whether a model can see images.
"""

import asyncio
import json
import os
import re
from contextvars import ContextVar
from pathlib import Path

from .session import latest_session_history, sessions_root

SESSION_EXTRA = {"deepseek": {"thinking": {"type": "enabled"}, "reasoning_effort": "low"},
                 "qwen": {"enable_thinking": False}}


def session_model():
    """Name of the model the latest JiuwenSwarm session ran on (its last context.usage record)."""
    name = None
    for line in latest_session_history().read_text(encoding="utf-8").splitlines():
        if '"context.usage"' in line:
            record = json.loads(line)
            name = record.get("model") or name
    if not name:
        raise ValueError("the latest JiuwenSwarm session has no model record yet")
    return name


def jiuwenswarm_models(text):
    """The model_client_config entries of config.yaml's models.defaults (PyYAML if present, else a plain scan)."""
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is not None:
        models = (yaml.safe_load(text) or {}).get("models", {}).get("defaults", []) or []
        return [m.get("model_client_config", {}) for m in models if isinstance(m, dict)]
    clients = []
    for block in text.split("model_client_config:")[1:]:
        fields = {}
        for key in ("model_name", "api_base", "api_key"):
            found = re.search(rf"^\s*{key}:\s*(.+?)\s*$", block.split("model_config_obj:")[0], re.M)
            if found:
                fields[key] = found.group(1).strip().strip("'\"")
        clients.append(fields)
    return clients


def env_file_values(path):
    """KEY=VALUE lines of a .env file (comments and blank lines skipped, surrounding quotes removed)."""
    values = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.removeprefix("export ").split("=", 1)
                values[key.strip()] = value.strip().strip("'\"")
    return values


def expand(value, env_file):
    """config.yaml values like ${BAILIAN_KEY}: filled from the process environment, else from JiuwenSwarm's .env.
    A variable set in neither is an error, so a placeholder is never sent as a key.
    """
    if not isinstance(value, str) or "${" not in value:
        return value
    values = {**env_file_values(env_file), **os.environ}

    def fill(found):
        name = found.group(1).split(":-")[0]
        if name in values and values[name]:
            return values[name]
        if ":-" in found.group(1):
            return found.group(1).split(":-", 1)[1]
        raise ValueError(f"${{{name}}} in config.yaml is not set (environment or {env_file})")

    return re.sub(r"\$\{([^}]+)\}", fill, value)


def model_config(value):
    """A model config dict. "session" (or {"model": "session", ...overrides}) means: the model the JiuwenSwarm
    session runs on, with its address and key from ~/.jiuwenswarm/config/config.yaml.
    """
    overrides = dict(value) if isinstance(value, dict) else {}
    if value != "session" and overrides.get("model") != "session":
        return value
    name = session_model()
    config_file = sessions_root().parents[1] / "config" / "config.yaml"
    client = next((c for c in jiuwenswarm_models(config_file.read_text(encoding="utf-8"))
                   if c.get("model_name") == name), None)
    if not client:
        raise ValueError(f"model {name!r} of the session is not in {config_file}")
    extra = next((v for k, v in SESSION_EXTRA.items() if k in name.lower()), {})
    overrides.pop("model", None)
    env_file = config_file.parent / ".env"
    return {"model": name, "base_url": expand(client["api_base"], env_file),
            "api_key": expand(client["api_key"], env_file), "extra_body": extra, **overrides}


def resolve_models(config):
    config = dict(config)
    for role in ("producer", "judge", "art_judge", "evidence_model"):
        if config.get(role):
            config[role] = model_config(config[role])
    return config

VISION_PROBE = (  # a 32x32 red square
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYn'
    'KSopGR8tMC0oMCUoKSj/2wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgo'
    'KCgoKCgoKCj/wAARCAAgACADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUF'
    'BAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVW'
    'V1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi'
    '4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAEC'
    'AxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVm'
    'Z2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq'
    '8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDz+iiivmD9yCiiigAooooAKKKKAP/Z'
)


def vision_cache_path():
    """Where probe results are kept: $REWARD_HOME/vision.json, default ~/.reward/vision.json."""
    return Path(os.environ.get("REWARD_HOME") or Path.home() / ".reward") / "vision.json"


async def can_see(model, log=lambda message: None, notes=None):
    """Whether the model reads images: the config's "vision" if set, else a cached probe (name a color). An unclear
    answer or a failed call is tried once more; if it stays unclear, no pictures are sent this run and a warning
    says so (nothing is cached, so the next run asks again).
    """
    if getattr(model, "vision", None) is not None:
        return bool(model.vision)
    path = vision_cache_path()
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    key = f"v2|{getattr(model, 'base_url', '')}|{model.name}"   # v2: older caches may hold a wrong "blind"
    if key in cache:
        return cache[key]
    problem = "no color in the answer"
    for _ in range(2):
        try:
            answer = await model.json("You describe images.",
                                      'What is the main color of the image? Reply {"color": "..."}.',
                                      [("image", VISION_PROBE)])
        except Exception as error:  # noqa: BLE001 - an API that rejects images cannot see them
            problem = str(error)
            if re.search(r"image|vision|multimodal", problem, re.I) and re.search(
                r"not support|unsupported", problem, re.I,
            ):
                cache[key] = False
                break
            continue
        color = answer.get("color") if isinstance(answer, dict) else None
        if isinstance(color, str) and color.strip():
            cache[key] = color.strip().casefold().strip(".。") in {"red", "红", "红色", "crimson"}
            break
    if key not in cache:
        log(f"vision probe for {model.name} unclear: {problem[:200]}")
        if notes is not None:
            notes.append(f"没能确认模型 {model.name} 能否看图，本次没有发截图；在配置里写 \"vision\": true 可跳过试探。")
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return cache[key]

ANSWER_LANGUAGE = ContextVar("reward_answer_language", default=None)


def items(found, *keys):
    """The list a model reply holds under the first of `keys` it has; a reply that is itself a list (some models
    drop the wrapping object) is taken as that list.
    """
    if isinstance(found, list):
        return found
    if isinstance(found, dict):
        for key in keys:
            if isinstance(found.get(key), list):
                return found[key]
    return []


def is_chinese(text):
    return bool(re.search(r"[\u4e00-\u9fff]", text or ""))


def set_language(task):
    ANSWER_LANGUAGE.set("Chinese (简体中文)" if re.search(r"[\u4e00-\u9fff]", task or "") else "English")


def resolve(value):
    if isinstance(value, str) and value.startswith("env:"):
        return os.environ.get(value[4:], "")
    return value


class Model:
    """One JSON-answering model, streamed so a slow but working call is told apart from a stuck one.

    Config: "timeout" is the call's whole quota in seconds, retries included (default 360);
    "first_chunk_timeout" and "idle_timeout" (default 60 each) catch a stream that never starts or stalls.
    """

    def __init__(self, config):
        from openai import AsyncOpenAI

        self.name = config["model"]
        self.extra = config.get("extra_body") or {}
        self.quota = float(config.get("timeout", 360))
        self.first_chunk = float(config.get("first_chunk_timeout", 60))
        self.idle = float(config.get("idle_timeout", 60))
        self.deadline = None  # loop time the whole run must end by; set by reward
        self.vision = config.get("vision")  # True / False, or None to probe once
        self.base_url = resolve(config["base_url"])
        self.client = AsyncOpenAI(base_url=resolve(config["base_url"]), api_key=resolve(config["api_key"]),
                                  timeout=self.quota, max_retries=0)

    async def json(self, system, user, images=()):
        content = [{"type": "text", "text": user}]
        for label, data in images:
            content += [{"type": "text", "text": label},
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + data}}]
        language = (f"\nWrite explanatory text in {ANSWER_LANGUAGE.get()}. Keep JSON keys, IDs, enum values and "
                    "code unchanged." if ANSWER_LANGUAGE.get() else "")
        messages = [{"role": "system", "content": system + language + "\nReply with one JSON object only."},
                    {"role": "user", "content": content if images else user}]
        loop = asyncio.get_running_loop()
        end = loop.time() + self.quota
        if self.deadline is not None:
            end = min(end, self.deadline)
        error = None
        for _ in range(2):  # one retry, inside the same quota
            if end - loop.time() < 5:
                break
            try:
                text = await self._stream(messages, end)
                return json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip()))
            except Exception as failure:  # noqa: BLE001 - retry once while the quota lasts
                error = failure
        raise error or TimeoutError(f"{self.name}: no time left for the call")

    async def _stream(self, messages, end):
        """Collect the answer text; reasoning chunks count as activity but are not part of the answer."""
        loop = asyncio.get_running_loop()
        stream = await asyncio.wait_for(self.client.chat.completions.create(
            model=self.name, response_format={"type": "json_object"}, extra_body=self.extra or None,
            messages=messages, stream=True), max(1.0, min(self.first_chunk, end - loop.time())))
        parts, wait = [], self.first_chunk
        chunks = stream.__aiter__()
        try:
            while True:
                left = end - loop.time()
                if left <= 0:
                    raise TimeoutError(f"{self.name}: call quota used up")
                try:
                    chunk = await asyncio.wait_for(chunks.__anext__(), min(wait, left))
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    raise TimeoutError(f"{self.name}: no data for {min(wait, left):.0f}s") from None
                wait = self.idle
                for choice in getattr(chunk, "choices", None) or []:
                    delta = getattr(choice, "delta", None)
                    if delta is not None and getattr(delta, "content", None):
                        parts.append(delta.content)
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                await close()
        return "".join(parts)
