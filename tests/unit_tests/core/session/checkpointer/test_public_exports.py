# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""官方文档从包根导入的名字必须真实可用。"""

import importlib


def test_documented_imports_resolve_from_package_root():
    module = importlib.import_module("openjiuwen.core.session.checkpointer")
    for name in (
        "CheckpointerConfig",
        "CheckpointerFactory",
        "CheckpointerProvider",
        "Checkpointer",
        "InMemoryCheckpointer",
        "PersistenceCheckpointer",
        "NoopCheckpointer",
    ):
        assert hasattr(module, name), f"missing export: {name}"
