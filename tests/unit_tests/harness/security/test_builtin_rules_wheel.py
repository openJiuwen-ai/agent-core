# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""builtin_rules.yaml must be loadable from the built wheel, not only the source tree."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[4]
_RULES_SUFFIX = "openjiuwen/harness/resources/builtin_rules.yaml"


def test_builtin_rules_load_from_built_wheel(tmp_path: Path) -> None:
    subprocess.run(
        [
            sys.executable, "-m", "pip", "wheel",
            "--no-deps", "--no-build-isolation",
            "-w", str(tmp_path),
            str(_ROOT),
        ],
        check=True,
        cwd=_ROOT,
        timeout=180,
    )
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        names = [name for name in wheel.namelist() if name.replace("\\", "/").endswith(_RULES_SUFFIX)]
        assert names, "harness/resources/builtin_rules.yaml missing from wheel"
        loaded = yaml.safe_load(wheel.read(names[0]))
    rules = loaded["rules"]
    broad = next(rule for rule in rules if rule["id"] == "shell_broad_process_termination")
    assert broad["action"] == "deny"
    assert "powershell" in broad["tools"]
    assert "Stop-Process" in broad["pattern"]
