# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Workspace integration helpers for the online RL launcher."""

from __future__ import annotations

import json
import os
from pathlib import Path


def build_trajectory_env_updates(
    *,
    gateway_url: str,
    model_path: str,
    trajectory_batch_size: int,
    trajectory_mode: str,
    trajectory_tenant_id: str | None = None,
) -> dict[str, str]:
    """Build online-RL env vars for JiuwenClaw Rail uploads."""
    updates = {
        'USE_RL_ONLINE_RAIL': '1',
        'ENABLE_TRAJECTORY_COLLECTION': 'false',
        'TRAJECTORY_GATEWAY_URL': gateway_url,
        'TRAJECTORY_TOKENIZER_PATH': model_path,
        'TRAJECTORY_BATCH_SIZE': str(trajectory_batch_size),
        'TRAJECTORY_MODE': trajectory_mode,
    }
    if trajectory_tenant_id:
        updates['RL_ONLINE_TENANT_ID'] = trajectory_tenant_id
    return updates


def ensure_workspace(
    *,
    config_env: Path,
    gateway_url: str,
    model_name: str,
    model_path: str,
    trajectory_mode: str,
    trajectory_gateway_url: str | None = None,
    trajectory_batch_size: int = 8,
) -> None:
    """Ensure JiuwenClaw .env points to the Gateway."""
    if not config_env.exists():
        from jiuwenclaw.utils import prepare_workspace

        prepare_workspace(overwrite=False, preferred_language='zh')

    traj_gateway = trajectory_gateway_url or gateway_url

    web_user_id = os.getenv('WEB_USER_ID', 'local-web-user').strip() or 'local-web-user'
    trajectory_tenant_id = os.getenv('RL_ONLINE_TENANT_ID', '').strip() or web_user_id
    custom_headers = json.dumps(
        {'x-user-id': trajectory_tenant_id},
        ensure_ascii=True,
        separators=(',', ':'),
    )

    updates = {
        'API_BASE': gateway_url,
        'API_KEY': 'EMPTY',
        'MODEL_NAME': model_name,
        'MODEL_PROVIDER': 'OpenAI',
        'WEB_USER_ID': web_user_id,
        'CUSTOM_HEADERS': f"'{custom_headers}'",
        'EMBED_API_BASE': gateway_url,
        'EMBED_API_KEY': 'EMPTY',
        'EMBED_MODEL': model_name,
        'BROWSER_RUNTIME_MCP_ENABLED': '0',
        'EVOLUTION_AUTO_SCAN': 'false',
    }
    updates.update(
        build_trajectory_env_updates(
            gateway_url=traj_gateway,
            model_path=model_path,
            trajectory_batch_size=trajectory_batch_size,
            trajectory_mode=trajectory_mode,
            trajectory_tenant_id=trajectory_tenant_id,
        )
    )

    existing: dict[str, str] = {}
    if config_env.exists():
        for line in config_env.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                existing[key.strip()] = value.strip()

    quoted_keys = {
        'API_BASE',
        'API_KEY',
        'MODEL_NAME',
        'MODEL_PROVIDER',
        'WEB_USER_ID',
        'RL_ONLINE_TENANT_ID',
        'EMBED_API_BASE',
        'EMBED_API_KEY',
        'EMBED_MODEL',
    }
    for key, value in updates.items():
        existing[key] = f'"{value}"' if key in quoted_keys else value

    lines = [f'{key}={value}' for key, value in existing.items()]
    config_env.write_text('\n'.join(lines) + '\n', encoding='utf-8')
