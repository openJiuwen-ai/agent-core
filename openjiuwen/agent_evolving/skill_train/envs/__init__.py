# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Environment adapters for skill_train benchmarks.

QA-style datasets: subclass ``DatasetEnvAdapter``, implement env-specific
dataloader / evaluator / ``process_one``, then register in
``skill_train.registry``. Full onboarding guide: ``envs/README.md``.
"""
