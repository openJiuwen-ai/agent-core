# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Constants for the session version-control (vcs) package."""

# Backend kind identifiers.
BACKEND_JSONL = "jsonl"
BACKEND_KV = "kv"

# KV key namespace and field segments. Key layout: "{session_id}:vcs:{...}".
VCS_NAMESPACE = "vcs"
KV_HEAD_SUFFIX = "head"
KV_LOG_PREFIX = "log"
KV_SNAPSHOT_PREFIX = "snap"
KV_COMMIT_PREFIX = "commit"

# Zero-padded width for event_id in kv log keys so lexical order == numeric order.
EVENT_ID_WIDTH = 20

# Jsonl backend on-disk layout.
DEFAULT_ROOT_DIRNAME = ".openjiuwen"
VCS_DIRNAME = "vcs"
HEAD_FILENAME = "HEAD"
LOG_DIRNAME = "logs"
LOG_FILENAME = "log.jsonl"
SNAPSHOT_DIRNAME = "snapshots"
COMMIT_DIRNAME = "commits"

# Snapshot composition keys.
CONTEXT_KEY = "context"  # snapshot["context"] and global_state["context"]
STATE_KEY = "state"  # snapshot["state"]
GLOBAL_STATE_KEY = "global_state"

# fsync policies.
FSYNC_EACH = "each"
FSYNC_BATCH = "batch"
FSYNC_SNAPSHOT = "snapshot"
FSYNC_OFF = "off"

# Defaults.
DEFAULT_BACKEND = BACKEND_JSONL
DEFAULT_FSYNC_POLICY = FSYNC_BATCH
DEFAULT_SNAPSHOT_EVERY = 50

# Reference prefix for addressing a raw event id, e.g. "e12".
EVENT_REF_PREFIX = "e"
