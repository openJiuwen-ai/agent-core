# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import json
import logging
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
_VERSION_RE = re.compile(r"^v(\d+)$")


@dataclass
class LoRAPublishRequest:
    user_id: str
    lora_path: str
    metadata: Optional[dict] = None
    base_model: str = ""
    parent_lora_id: str = ""
    parent_lora_version: str = ""
    parent_lora_path: str = ""
    availability_status: str = "pending"
    availability_reason: str = ""
    availability_checked_at: Optional[datetime] = None
    training_source: str = "base_model"


@dataclass
class LoRAVersion:
    user_id: str
    version: str  # "v1", "v2", ...
    path: str
    created_at: datetime
    trajectory_count: int
    reward_avg: float
    base_model: str
    parent_lora_id: str = ""
    parent_lora_version: str = ""
    parent_lora_path: str = ""
    availability_status: str = "pending"
    availability_reason: str = ""
    availability_checked_at: Optional[datetime] = None
    training_source: str = "base_model"


class LoRARepository:
    def __init__(self, root: str = "lora_repo"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def publish(self, request: LoRAPublishRequest, *args, **kwargs) -> LoRAVersion:
        request = self._coerce_publish_request(request, args, kwargs)
        user_id = request.user_id
        user_dir = self.root / user_id
        user_dir.mkdir(parents=True, exist_ok=True)

        # 计算下一个版本号
        existing = self._list_version_dirs(user_dir)
        next_num = 1 + max((self._version_num(path) for path in existing), default=0)
        version = f"v{next_num}"
        version_dir = user_dir / version
        version_dir.mkdir(parents=True, exist_ok=True)

        # 复制 LoRA 权重文件
        src = Path(request.lora_path)
        for f in src.iterdir() if src.is_dir() else [src]:
            shutil.copy2(f, version_dir / f.name)

        # 计算 reward 平均值
        metadata = request.metadata or {}
        reward_avg = metadata.get("reward_avg", metadata.get("avg_score", 0.0))
        trajectory_count = metadata.get("trajectory_count", metadata.get("sample_count", 0))

        # 写 metadata.json
        meta = {
            "user_id": user_id,
            "version": version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "trajectory_count": trajectory_count,
            "reward_avg": reward_avg,
            "base_model": request.base_model,
            "parent_lora_id": request.parent_lora_id,
            "parent_lora_version": request.parent_lora_version,
            "parent_lora_path": request.parent_lora_path,
            "availability_status": metadata.get("availability_status", request.availability_status),
            "availability_reason": metadata.get("availability_reason", request.availability_reason),
            "availability_checked_at": (
                metadata.get("availability_checked_at")
                or (request.availability_checked_at.isoformat() if request.availability_checked_at else "")
            ),
            "training_source": metadata.get("training_source", request.training_source),
        }
        (version_dir / "metadata.json").write_text(json.dumps(meta, indent=2))

        # 原子更新 latest 软链
        latest_link = user_dir / "latest"
        tmp_link = user_dir / ".latest_tmp"
        if tmp_link.exists() or tmp_link.is_symlink():
            tmp_link.unlink()
        if latest_link.exists() and latest_link.is_dir() and not latest_link.is_symlink():
            raise RuntimeError(f"Cannot update latest symlink because {latest_link} is a directory")
        tmp_link.symlink_to(version)
        os.replace(tmp_link, latest_link)

        lora_version = LoRAVersion(
            user_id=user_id,
            version=version,
            path=str(version_dir),
            created_at=datetime.fromisoformat(meta["created_at"]),
            trajectory_count=trajectory_count,
            reward_avg=reward_avg,
            base_model=request.base_model,
            parent_lora_id=request.parent_lora_id,
            parent_lora_version=request.parent_lora_version,
            parent_lora_path=request.parent_lora_path,
            availability_status=meta["availability_status"],
            availability_reason=meta["availability_reason"],
            availability_checked_at=(
                datetime.fromisoformat(meta["availability_checked_at"])
                if meta["availability_checked_at"]
                else None
            ),
            training_source=str(meta.get("training_source") or "base_model"),
        )
        logger.info(f"Published LoRA {version} for user {user_id} at {version_dir}")
        return lora_version

    @staticmethod
    def _coerce_publish_request(
        request: LoRAPublishRequest,
        args: tuple,
        kwargs: dict,
    ) -> LoRAPublishRequest:
        if isinstance(request, LoRAPublishRequest):
            if args or kwargs:
                raise TypeError("publish() accepts no extra arguments when given LoRAPublishRequest")
            return request

        if not isinstance(request, str):
            raise TypeError("publish() expects LoRAPublishRequest")
        if not args:
            raise TypeError("legacy publish() requires lora_path")

        lora_path = args[0]
        if len(args) > 2:
            raise TypeError("legacy publish() accepts at most user_id, lora_path, metadata")
        metadata = args[1] if len(args) == 2 else kwargs.pop("metadata", None)
        publish_request = LoRAPublishRequest(
            user_id=request,
            lora_path=lora_path,
            metadata=metadata,
            base_model=kwargs.pop("base_model", ""),
            parent_lora_id=kwargs.pop("parent_lora_id", ""),
            parent_lora_version=kwargs.pop("parent_lora_version", ""),
            parent_lora_path=kwargs.pop("parent_lora_path", ""),
            availability_status=kwargs.pop("availability_status", "pending"),
            availability_reason=kwargs.pop("availability_reason", ""),
            availability_checked_at=kwargs.pop("availability_checked_at", None),
            training_source=kwargs.pop("training_source", "base_model"),
        )
        if kwargs:
            unknown = ", ".join(sorted(kwargs))
            raise TypeError(f"legacy publish() got unexpected keyword argument(s): {unknown}")
        return publish_request

    def get_latest(self, user_id: str) -> Optional[LoRAVersion]:
        latest_link = self.root / user_id / "latest"
        if not latest_link.exists():
            return None
        version_dir = latest_link.resolve()
        meta_file = version_dir / "metadata.json"
        if not meta_file.exists():
            return None
        meta = json.loads(meta_file.read_text())
        return self._load_version(meta, version_dir)

    def get_latest_available(self, user_id: str) -> Optional[LoRAVersion]:
        user_dir = self.root / user_id
        if not user_dir.exists():
            return None
        for version_dir in sorted(self._list_version_dirs(user_dir), key=self._version_num, reverse=True):
            meta_file = version_dir / "metadata.json"
            if not meta_file.exists():
                continue
            meta = json.loads(meta_file.read_text())
            version = self._load_version(meta, version_dir)
            if version.availability_status == "available":
                return version
        return None

    def list_versions(self, user_id: str) -> list[LoRAVersion]:
        user_dir = self.root / user_id
        if not user_dir.exists():
            return []
        versions = []
        for version_dir in sorted(self._list_version_dirs(user_dir), key=self._version_num):
            meta_file = version_dir / "metadata.json"
            if not meta_file.exists():
                continue
            meta = json.loads(meta_file.read_text())
            versions.append(self._load_version(meta, version_dir))
        return versions

    def get_version(self, user_id: str, version: str) -> Optional[LoRAVersion]:
        version_dir = self.root / user_id / version
        if not version_dir.is_dir() or not _VERSION_RE.match(version):
            return None
        meta_file = version_dir / "metadata.json"
        if not meta_file.exists():
            return None
        meta = json.loads(meta_file.read_text())
        return self._load_version(meta, version_dir)

    def list_users(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(
            path.name
            for path in self.root.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )

    def set_latest(self, user_id: str, version: str) -> LoRAVersion:
        lora_version = self.get_version(user_id, version)
        if lora_version is None:
            raise FileNotFoundError(f"LoRA version not found: {user_id}/{version}")
        user_dir = self.root / user_id
        latest_link = user_dir / "latest"
        tmp_link = user_dir / ".latest_tmp"
        if tmp_link.exists() or tmp_link.is_symlink():
            tmp_link.unlink()
        if latest_link.exists() and latest_link.is_dir() and not latest_link.is_symlink():
            raise RuntimeError(f"Cannot update latest symlink because {latest_link} is a directory")
        tmp_link.symlink_to(version)
        os.replace(tmp_link, latest_link)
        return lora_version

    def set_availability(
        self,
        user_id: str,
        version: str,
        *,
        available: bool,
        reason: str = "",
        checked_at: Optional[datetime] = None,
    ) -> LoRAVersion:
        lora_version = self.get_version(user_id, version)
        if lora_version is None:
            raise FileNotFoundError(f"LoRA version not found: {user_id}/{version}")
        version_dir = self.root / user_id / version
        meta_file = version_dir / "metadata.json"
        meta = json.loads(meta_file.read_text())
        meta["availability_status"] = "available" if available else "unavailable"
        meta["availability_reason"] = reason
        meta["availability_checked_at"] = (
            (checked_at or datetime.now(timezone.utc)).isoformat()
        )
        meta_file.write_text(json.dumps(meta, indent=2))
        return self._load_version(meta, version_dir)

    def delete_version(self, user_id: str, version: str, *, force: bool = False) -> None:
        version_dir = self.root / user_id / version
        if not version_dir.is_dir() or not _VERSION_RE.match(version):
            raise FileNotFoundError(f"LoRA version not found: {user_id}/{version}")

        latest = self.get_latest(user_id)
        if latest is not None and latest.version == version and not force:
            raise RuntimeError("cannot delete latest LoRA without force=true")

        deleting_marker = version_dir / ".deleting"
        deleting_marker.write_text(datetime.now(timezone.utc).isoformat())
        shutil.rmtree(version_dir)

        latest_link = self.root / user_id / "latest"
        if latest is not None and latest.version == version and latest_link.exists():
            latest_link.unlink()

    @staticmethod
    def _list_version_dirs(user_dir: Path) -> list[Path]:
        return [d for d in user_dir.iterdir() if d.is_dir() and _VERSION_RE.match(d.name)]

    @staticmethod
    def _version_num(version_dir: Path) -> int:
        match = _VERSION_RE.match(version_dir.name)
        if match is None:
            raise ValueError(f"Invalid version directory name: {version_dir.name}")
        return int(match.group(1))

    @staticmethod
    def _load_version(meta: dict, version_dir: Path) -> LoRAVersion:
        availability_checked_at = meta.get("availability_checked_at") or ""
        return LoRAVersion(
            user_id=meta["user_id"],
            version=meta["version"],
            path=str(version_dir),
            created_at=datetime.fromisoformat(meta["created_at"]),
            trajectory_count=meta["trajectory_count"],
            reward_avg=meta["reward_avg"],
            base_model=meta["base_model"],
            parent_lora_id=str(meta.get("parent_lora_id") or ""),
            parent_lora_version=str(meta.get("parent_lora_version") or ""),
            parent_lora_path=str(meta.get("parent_lora_path") or ""),
            availability_status=str(meta.get("availability_status") or "pending"),
            availability_reason=str(meta.get("availability_reason") or ""),
            availability_checked_at=(
                datetime.fromisoformat(availability_checked_at)
                if availability_checked_at
                else None
            ),
            training_source=str(meta.get("training_source") or "base_model"),
        )
