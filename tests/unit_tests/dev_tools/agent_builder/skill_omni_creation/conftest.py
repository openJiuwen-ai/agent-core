# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import os
import sys
from pathlib import Path

SCRIPTS_DIR = (
    Path(__file__).resolve().parents[5]
    / "openjiuwen"
    / "dev_tools"
    / "skill_creator"
    / "skills"
    / "skill_omni_creation"
    / "scripts"
)

os.environ.setdefault("API_BASE", "https://mock.api")
os.environ.setdefault("API_KEY", "mock-key")
os.environ.setdefault("MODEL_NAME", "mock-model")

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
