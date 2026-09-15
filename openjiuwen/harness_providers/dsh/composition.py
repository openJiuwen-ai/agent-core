# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Build temporary Cordis overlays using the bundled DSH plugins."""

import json
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from openjiuwen.harness_protocol import HarnessContext, McpTransport, UnsupportedHarnessCapabilityError


def mcp_configs(context: HarnessContext) -> list[dict[str, Any]]:
    """Translate protocol MCP definitions to DSH's native MCP-client config."""
    configs = []
    names = set()
    for server in context.mcp_servers:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", server.name) or server.name in names:
            raise ValueError("DSH MCP names must be unique and match [A-Za-z0-9_-]{1,32}")
        names.add(server.name)
        config: dict[str, Any] = {"serverName": server.name, "failOnStartupError": True}
        if server.transport is McpTransport.STDIO:
            config.update(
                transport="stdio",
                command=server.command[0],
                args=list(server.command[1:]),
                env=dict(server.env),
            )
            if context.cwd:
                config["cwd"] = context.cwd
        elif server.transport is McpTransport.HTTP:
            config.update(transport="streamable-http", url=server.url, headers=dict(server.headers))
        else:
            raise UnsupportedHarnessCapabilityError("DSH cannot mount in-process MCP servers")
        configs.append(config)
    return configs


def write_overlay(
    context: HarnessContext,
    *,
    include_prompt: bool,
    prompt_mode: str = "replace",
    enable_skill_plugins: bool = False,
) -> tuple[tempfile.TemporaryDirectory, str, dict[str, str]] | None:
    """Keep user data in the child environment and register native plugins.

    Append mode contributes an independent section and a single interpolation
    variable: user text is not recursively parsed as DSH template syntax.
    """
    configs = mcp_configs(context)
    if not configs and not (include_prompt and context.system_prompt) and not enable_skill_plugins:
        return None
    variable = f"OPENJIUWEN_DSH_HOST_{uuid.uuid4().hex.upper()}"
    directory = tempfile.TemporaryDirectory(prefix="openjiuwen-dsh-")
    root = Path(directory.name)
    values: dict[str, Any] = {"mcps": configs}
    rows: list[str] = []
    inserts: list[str] = []
    try:
        if include_prompt and context.system_prompt:
            plugin = root / "host-prompt.mjs"
            if prompt_mode == "replace":
                values["prompt"] = {"personaPrefix": context.system_prompt}
                body = (
                    f"  const value = JSON.parse(process.env.{variable}).prompt.personaPrefix;\n"
                    "  ctx.on('system-prompt/assemble', async (_assembly, _context, next) => {\n"
                    "    const assembly = await next();\n"
                    "    return {...assembly, sections: assembly.sections.map(section =>\n"
                    "      section.name === 'deployment:persona-prefix' ? {...section, text: value} : section)};\n"
                    "  });\n"
                )
            else:
                values["append"] = context.system_prompt
                body = (
                    f"  const value = JSON.parse(process.env.{variable}).append;\n"
                    "  ctx.systemPrompt.variable('openjiuwen_host_instructions', () => value);\n"
                    "  ctx.systemPrompt.section({name: 'openjiuwen:host-instructions',\n"
                    "    order: Number.MAX_SAFE_INTEGER, text: '{{openjiuwen_host_instructions}}'});\n"
                )
            plugin.write_text(
                "export const name = 'openjiuwen-host-prompt';\n"
                "export const inject = ['systemPrompt'];\n"
                "export function apply(ctx) {\n" + body + "}\n", encoding="utf-8",
            )
            plugin.chmod(0o600)
            inserts.append(f'    - id: openjiuwen-host-prompt\n      name: {json.dumps(str(plugin))}\n')
        if enable_skill_plugins:
            skill_plugins = [
                ("skill", "skill"),
                ("skill-filesystem", "skill-filesystem"),
                ("tool-skill", "tool-skill"),
            ]
            for plugin_id, package in skill_plugins:
                inserts.append(f'    - id: {plugin_id}\n      name: "@deepseek-ai/dsh-{package}"\n')
        for index in range(len(configs)):
            inserts.append(
                f'    - id: openjiuwen-mcp-{index}\n'
                f'      name: "@deepseek-ai/dsh-mcp-client"\n'
                f'      config: !!js "JSON.parse(process.env.{variable}).mcps[{index}]"\n'
            )
        if inserts:
            rows.extend(['- insert:\n', *inserts])
        path = root / "host.patch.yml"
        path.write_text("".join(rows), encoding="utf-8")
        path.chmod(0o600)
    except BaseException:
        directory.cleanup()
        raise
    return directory, str(path), {variable: json.dumps(values, ensure_ascii=False)}
