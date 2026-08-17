# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Small synchronous client for the online-RL Gateway APIs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping, Optional

import httpx

_FILENAME_RE = re.compile(r'filename="?([^";]+)"?')


class GatewayAPIClient:
    """Typed convenience wrapper around Gateway trajectory, training, and LoRA APIs."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        timeout: float = 30.0,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def __enter__(self) -> "GatewayAPIClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def health(self) -> dict[str, Any]:
        return self._get_json("/health")

    def rl_health(self) -> dict[str, Any]:
        return self._get_json("/v1/rl/health")

    def gateway_stats(self) -> dict[str, Any]:
        return self._get_json("/v1/gateway/stats")

    def upload_rail_batch(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._post_json("/v1/gateway/upload/batch", payload)

    def create_trajectories(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._post_json("/v1/rl/trajectories:batchCreate", payload)

    def trajectory_stats(
        self,
        *,
        model_id: str = "",
        user_id: str = "",
    ) -> dict[str, Any]:
        return self._get_json(
            "/v1/rl/trajectories/stats",
            params=self._clean_params({"model_id": model_id, "user_id": user_id}),
        )

    def list_trajectories(
        self,
        *,
        model_id: str = "",
        status: str = "",
        user_id: str = "",
        session_id: str = "",
        task_id: str = "",
        source: str = "",
        policy_version: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        return self._get_json(
            "/v1/rl/trajectories",
            params=self._clean_params({
                "model_id": model_id,
                "status": status,
                "user_id": user_id,
                "session_id": session_id,
                "task_id": task_id,
                "source": source,
                "policy_version": policy_version,
                "limit": limit,
            }),
        )

    def get_trajectory(self, trajectory_id: str) -> dict[str, Any]:
        return self._get_json(f"/v1/rl/trajectories/{trajectory_id}")

    def update_trajectory(
        self,
        trajectory_id: str,
        updates: Mapping[str, Any],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        params = {"force": str(force).lower()} if force else None
        return self._patch_json(f"/v1/rl/trajectories/{trajectory_id}", updates, params=params)

    def delete_trajectory(self, trajectory_id: str, *, force: bool = False) -> dict[str, Any]:
        return self._delete_json(
            f"/v1/rl/trajectories/{trajectory_id}",
            params={"force": str(force).lower()} if force else None,
        )

    def create_training_task(
        self,
        *,
        task_id: str = "",
        user_id: str = "",
        drain_pending_on_train: Optional[bool] = None,
        max_samples_per_run: Optional[int] = None,
        ppo_samples_per_step: Optional[int] = None,
        allow_partial_last_step: Optional[bool] = None,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, value in {
            "task_id": task_id,
            "user_id": user_id,
            "drain_pending_on_train": drain_pending_on_train,
            "max_samples_per_run": max_samples_per_run,
            "ppo_samples_per_step": ppo_samples_per_step,
            "allow_partial_last_step": allow_partial_last_step,
            "metadata": dict(metadata or {}) if metadata is not None else None,
        }.items():
            if value is not None and value != "":
                payload[key] = value
        return self._post_json("/v1/training/tasks", payload)

    def list_training_tasks(self, *, limit: int = 100) -> dict[str, Any]:
        return self._get_json("/v1/training/tasks", params={"limit": limit})

    def get_training_task(self, task_id: str) -> dict[str, Any]:
        return self._get_json(f"/v1/training/tasks/{task_id}")

    def update_training_task(self, task_id: str, updates: Mapping[str, Any]) -> dict[str, Any]:
        return self._patch_json(f"/v1/training/tasks/{task_id}", updates)

    def stop_training_task(self, task_id: str) -> dict[str, Any]:
        return self.update_training_task(task_id, {"status": "stopping"})

    def list_lora(self, *, model_id: str = "") -> dict[str, Any]:
        return self._get_json("/v1/rl/lora", params=self._clean_params({"model_id": model_id}))

    def register_lora(
        self,
        *,
        model_id: str,
        lora_path: str,
        base_model: str = "",
        metadata: Optional[Mapping[str, Any]] = None,
        set_latest: Optional[bool] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model_id": model_id,
            "lora_path": lora_path,
        }
        if base_model:
            payload["base_model"] = base_model
        if metadata is not None:
            payload["metadata"] = dict(metadata)
        if set_latest is not None:
            payload["set_latest"] = set_latest
        return self._post_json("/v1/rl/lora", payload)

    def get_latest_lora(self, model_id: str) -> dict[str, Any]:
        return self._get_json("/v1/rl/lora/latest", params={"model_id": model_id})

    def get_effective_lora(self, model_id: str, *, ensure_loaded: bool = True) -> dict[str, Any]:
        return self._post_json(
            "/v1/rl/lora/effective",
            {"model_id": model_id, "ensure_loaded": ensure_loaded},
        )

    def get_lora(self, lora_id: str) -> dict[str, Any]:
        return self._get_json(f"/v1/rl/lora/{lora_id}")

    def set_latest_lora(self, lora_id: str) -> dict[str, Any]:
        return self._post_json(f"/v1/rl/lora/{lora_id}:setLatest", {})

    def set_lora_availability(
        self,
        lora_id: str,
        *,
        available: bool,
        reason: str = "",
    ) -> dict[str, Any]:
        return self._post_json(
            f"/v1/rl/lora/{lora_id}:setAvailability",
            {"available": available, "reason": reason},
        )

    def delete_lora(self, lora_id: str) -> dict[str, Any]:
        return self._delete_json(f"/v1/rl/lora/{lora_id}")

    def download_lora(self, lora_id: str, output: str | Path) -> Path:
        """Download a LoRA artifact to ``output``.

        ``output`` may be a directory or a concrete file path. Directory-backed
        LoRA versions are returned by the Gateway as zip archives.
        """
        response = self._request("GET", f"/v1/rl/lora/{lora_id}:download")
        target = Path(output)
        if target.exists() and target.is_dir():
            target = target / self._download_filename(response, default=f"{lora_id.replace(':', '_')}.zip")
        elif not target.suffix:
            target.mkdir(parents=True, exist_ok=True)
            target = target / self._download_filename(response, default=f"{lora_id.replace(':', '_')}.zip")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        return target

    def _get_json(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        return self._json(self._request("GET", path, params=params))

    def _post_json(self, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self._json(self._request("POST", path, json=dict(payload)))

    def _patch_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        params: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        return self._json(self._request("PATCH", path, params=params, json=dict(payload)))

    def _delete_json(self, path: str, *, params: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        return self._json(self._request("DELETE", path, params=params))

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.api_key:
            headers.setdefault("Authorization", f"Bearer {self.api_key}")
        response = self._client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
        response.raise_for_status()
        return response

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Gateway response is not a JSON object")
        return data

    @staticmethod
    def _clean_params(params: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in params.items() if value not in ("", None)}

    @staticmethod
    def _download_filename(response: httpx.Response, *, default: str) -> str:
        safe_default = GatewayAPIClient._safe_download_filename(default, fallback="download.zip")
        disposition = response.headers.get("content-disposition", "")
        match = _FILENAME_RE.search(disposition)
        if not match:
            return safe_default
        return GatewayAPIClient._safe_download_filename(match.group(1), fallback=safe_default)

    @staticmethod
    def _safe_download_filename(filename: str, *, fallback: str) -> str:
        name = Path(filename.replace("\\", "/")).name
        return fallback if name in {"", ".", ".."} else name
