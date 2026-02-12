# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import importlib

import pytest

from openjiuwen.agent_evolving.optimizer.tool_call.base import ToolOptimizerBase
from openjiuwen.agent_evolving.optimizer.tool_call.utils.default_configs import default_config_desc, default_config_eg


def test_init_exports_and_default_configs():
    init_module = importlib.import_module("openjiuwen.agent_evolving.optimizer.tool_call.__init__")
    assert "ToolOptimizerBase" in init_module.__all__
    assert ToolOptimizerBase is not None

    assert default_config_eg["gen_model_id"]
    assert default_config_eg["beam_width"] >= 1
    assert default_config_desc["num_examples_for_desc"] >= 1
