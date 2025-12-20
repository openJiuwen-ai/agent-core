#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Generator
from pydantic import BaseModel, Field

from openjiuwen.core.agent.agent import Agent
from openjiuwen.agent_builder.tune.base import TuneConstant


class Progress(BaseModel):
    val_baseline_score: float = Field(default=0.0, ge=0.0, le=1.0)
    current_epoch: int = Field(default=0, ge=0)
    max_epoch: int = Field(default=TuneConstant.DEFAULT_ITERATION_NUM, ge=0)
    current_batch_iter: int = Field(default=0, ge=0)
    max_batch_iter: int = Field(default=1, ge=0)
    best_score: float = Field(default=0.0, ge=0.0, le=1.0)
    best_batch_score: float = Field(default=0.0, ge=0.0, le=1.0)

    def run_epoch(self) -> Generator:
        for epoch in range(1, self.max_epoch + 1):
            self.current_epoch = epoch
            yield epoch

    def run_batch(self) -> Generator:
        self.best_batch_score = 0
        for iter in range(self.max_batch_iter):
            self.current_batch_iter = iter
            yield iter


class Callbacks:
    def on_train_begin(self, agent: Agent, progress: Progress) -> None:
        pass

    def on_train_end(self, agent: Agent, progress: Progress) -> None:
        pass

    def on_train_epoch_begin(self, agent: Agent, progress: Progress) -> None:
        pass

    def on_train_epoch_end(self, agent: Agent, progress: Progress) -> None:
        pass