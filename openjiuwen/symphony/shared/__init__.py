# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Shared data contracts and implementation building blocks for Symphony."""

from openjiuwen.symphony.shared.fingerprint import (
    ArtifactSpec,
    CapabilityFingerprint,
    Fingerprint,
    ParameterSpec,
)

from .naming import (
    bounded_edit_distance,
    fuzzy_name_distance,
    normalize_name_key,
    to_camel_case,
    to_camel_path,
    to_kebab_case,
    to_pascal_case,
    to_pascal_path,
)
from .profiling import StageTimer
from .rich_compat import BarColumn, Console, Panel, Progress, RichTree, SpinnerColumn, TaskProgressColumn, TextColumn
from .storage import (
    S3Location,
    create_s3_client,
    download_s3_object_to_path,
    download_s3_relative_object_if_exists,
    is_s3_uri,
    join_s3_uri,
    materialize_s3_dir,
    parse_s3_uri,
    read_s3_bytes,
    read_s3_text,
    upload_local_dir_to_s3,
    upload_s3_bytes,
)

__all__ = [
    "ArtifactSpec",
    "BarColumn",
    "CapabilityFingerprint",
    "Console",
    "Fingerprint",
    "Panel",
    "ParameterSpec",
    "Progress",
    "RichTree",
    "S3Location",
    "SpinnerColumn",
    "StageTimer",
    "TaskProgressColumn",
    "TextColumn",
    "bounded_edit_distance",
    "create_s3_client",
    "download_s3_object_to_path",
    "download_s3_relative_object_if_exists",
    "fuzzy_name_distance",
    "is_s3_uri",
    "join_s3_uri",
    "materialize_s3_dir",
    "normalize_name_key",
    "parse_s3_uri",
    "read_s3_bytes",
    "read_s3_text",
    "to_camel_case",
    "to_camel_path",
    "to_kebab_case",
    "to_pascal_case",
    "to_pascal_path",
    "upload_local_dir_to_s3",
    "upload_s3_bytes",
]
