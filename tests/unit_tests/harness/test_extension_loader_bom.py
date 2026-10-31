# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Package manifests written by Windows editors may carry a UTF-8 BOM."""

from __future__ import annotations

import json

from openjiuwen.harness.resources.extension_loader import load_plugin_package


def test_plugin_manifest_with_utf8_bom_loads(tmp_path) -> None:
    manifest = tmp_path / "manifest.json"
    payload = {"package_type": "plugin", "id": "bom-plugin"}
    manifest.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload).encode("utf-8"))

    spec = load_plugin_package(manifest)

    assert spec.id == "bom-plugin"
