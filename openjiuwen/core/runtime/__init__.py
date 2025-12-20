#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import contextvars

workflow_runtime_vars: contextvars.ContextVar[dict] = contextvars.ContextVar("workflow_runtime_vars", default={})