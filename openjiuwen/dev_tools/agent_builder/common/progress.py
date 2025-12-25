#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import time
from typing import Dict, List, Any, Optional, Callable
from datetime import datetime, timezone
from dataclasses import dataclass, field
from functools import wraps

from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.agent_builder.common.enums import (
    AgentType, ProgressStage, ProgressStatus
)


@dataclass
class ProgressStep:
    stage: ProgressStage
    status: ProgressStatus
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    duration: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage.value,
            "status": self.status.value,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "duration": self.duration,
            "error": self.error,
        }


@dataclass
class BuildProgress:
    session_id: str
    agent_type: AgentType
    current_stage: ProgressStage
    current_status: ProgressStatus
    current_message: str
    steps: List[ProgressStep] = field(default_factory=list)
    overall_progress: float = 0.0
    start_time: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    last_update_time: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "agent_type": self.agent_type.value,
            "current_stage": self.current_stage.value,
            "current_status": self.current_status.value,
            "current_message": self.current_message,
            "steps": [step.to_dict() for step in self.steps],
            "overall_progress": self.overall_progress,
            "start_time": self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_update_time": self.last_update_time.strftime("%Y-%m-%d %H:%M:%S"),
            "error": self.error,
        }


class ProgressReporter:
    """Class to report the progress of agent building.

    Responsible for collecting and reporting progress updates during the agent building process.
    Supports observer pattern to notify interested parties of progress changes by invoking a callback function.

    """

    def __init__(self, session_id: str, agent_type: AgentType):
        self.session_id = session_id
        self.agent_type = agent_type
        self.progress = BuildProgress(
            session_id=session_id,
            agent_type=agent_type,
            current_stage=ProgressStage.INITIALIZING,
            current_status=ProgressStatus.PENDING,
            current_message="Initializing..."
        )
        self._callbacks: List[Callable[[BuildProgress], None]] = []
        self._step_start_times: Dict[ProgressStage, float] = {}

    @property
    def callbacks(self):
        return self._callbacks

    def add_callback(self, callback: Callable[[BuildProgress], None]):
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[BuildProgress], None]):
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def get_progress(self) -> BuildProgress:
        return self.progress

    def complete(self, message: str = "Completed"):
        self.progress.current_status = ProgressStatus.SUCCESS
        self.progress.current_message = message
        self.progress.overall_progress = 100.0
        self.progress.last_update_time = datetime.now(tz=timezone.utc)

        if self.progress.steps:
            self._end_current_stage(ProgressStatus.SUCCESS, message)

        logger.info(f"Progress completed for session {self.session_id}: {self.agent_type.value}")

        self._notify()

    def start_stage(self,
                    stage: ProgressStage,
                    message: str,
                    details: Optional[Dict[str, Any]] = None,
                    progress: Optional[float] = None):
        if self.progress.current_stage != ProgressStage.INITIALIZING:
            self._end_current_stage(ProgressStatus.SUCCESS)

        self._step_start_times[stage] = time.time()

        self.progress.current_stage = stage
        self.progress.current_status = ProgressStatus.RUNNING
        self.progress.current_message = message
        self.progress.last_update_time = datetime.now(tz=timezone.utc)

        if progress is not None:
            self.progress.overall_progress = progress
        else:
            self.progress.overall_progress = self._calculate_progress(stage)

        step = ProgressStep(
            stage=stage,
            status=ProgressStatus.RUNNING,
            message=message,
            details=details or {}
        )
        self.progress.steps.append(step)
        logger.info(f"Started stage: {stage.value}, message: {message}")
        self._notify()

    def update_stage(self,
                     message: Optional[str] = None,
                     details: Optional[Dict[str, Any]] = None,
                     progress: Optional[float] = None):
        if message is not None:
            self.progress.current_message = message
        if details is not None:
            self.progress.steps[-1].details.update(details)
        if progress is not None:
            self.progress.overall_progress = progress
        self.progress.last_update_time = datetime.now(tz=timezone.utc)
        self._notify()

    def complete_stage(self, message: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        self._end_current_stage(ProgressStatus.SUCCESS, message, details)

    def fail_stage(self, error: str, message: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        self.progress.error = error
        self._end_current_stage(ProgressStatus.FAILED, message, details, error)

    def warn_stage(self, warning: str, message: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        if message is not None:
            self.progress.current_message = message

        if self.progress.steps:
            self.progress.steps[-1].status = ProgressStatus.WARNING
            self.progress.steps[-1].details["warning"] = warning
            if details is not None:
                self.progress.steps[-1].details.update(details)
        
        self.progress.current_status = ProgressStatus.WARNING
        self.progress.last_update_time = datetime.now(tz=timezone.utc)
        self._notify()

    def _notify(self):
        for callback in self._callbacks:
            try:
                callback(self.progress)
            except Exception as e:
                logger.error(f"Progress callback error: {e}")

    def _end_current_stage(self,
                           status: ProgressStatus,
                           message: Optional[str] = None,
                           details: Optional[Dict[str, Any]] = None,
                           error: Optional[str] = None):
        if not self.progress.steps:
            return

        current_step = self.progress.steps[-1]
        current_step.status = status
        if details is not None:
            current_step.details.update(details)
        if message:
            current_step.message = message
        if error:
            current_step.error = error
            self.progress.error = error

        stage = current_step.stage
        if stage in self._step_start_times:
            duration = time.time() - self._step_start_times[stage]
            current_step.duration = duration
            del self._step_start_times[stage]

        self.progress.current_status = status
        if message:
            self.progress.current_message = message
        self.progress.last_update_time = datetime.now(tz=timezone.utc)

    def _calculate_progress(self, stage: ProgressStage) -> float:
        llm_agent_progress_map: Dict[ProgressStage, float] = {
            ProgressStage.INITIALIZING: 0,
            ProgressStage.RESOURCE_RETRIEVING: 20,
            ProgressStage.CLARIFYING: 40,
            ProgressStage.GENERATING: 60,
            ProgressStage.CONVERTING: 90,
        }

        workflow_progress_map: Dict[ProgressStage, float] = {
            ProgressStage.INITIALIZING: 0,
            ProgressStage.RESOURCE_RETRIEVING: 20,
            ProgressStage.CLARIFYING: 40,
            ProgressStage.GENERATING: 60,
            ProgressStage.VALIDATING: 70,
            ProgressStage.CONVERTING: 90,
        }

        if self.agent_type == AgentType.LLM_AGENT:
            return llm_agent_progress_map.get(stage, 0.0)
        elif self.agent_type == AgentType.WORKFLOW:
            return workflow_progress_map.get(stage, 0.0)

        return 0.0


class ProgressManager:
    """Class to manage multiple ProgressReporters.

    Responsible for creating, retrieving, and managing ProgressReporter instances for different sessions.

    Examples:
        progress_manager = ProgressManager()
        reporter = progress_manager.create_reporter("123", "llm_agent")
        progress = reporter.get_progress("123")
    """

    def __init__(self):
        self._reporters: Dict[str, ProgressReporter] = {}

    def get_reporter(self, session_id: str) -> Optional[ProgressReporter]:
        return self._reporters.get(session_id)

    def create_reporter(self, session_id: str, agent_type: AgentType) -> ProgressReporter:
        if session_id in self._reporters:
            return self._reporters[session_id]

        reporter = ProgressReporter(session_id, agent_type)
        self._reporters[session_id] = reporter
        return reporter

    def remove_reporter(self, session_id: str):
        if session_id in self._reporters:
            del self._reporters[session_id]

    def get_progress(self, session_id: str) -> Optional[BuildProgress]:
        reporter = self._reporters.get(session_id)
        if reporter:
            return reporter.get_progress()
        return None


def progress_stage(*,
                   stage: ProgressStage,
                   start_message: str,
                   complete_message: str,
                   fail_message: Optional[str] = None,
                   detail_builder: Optional[Callable[[object, object], dict]] = None):
    """
    Used to automatically report build progress before and after instance method execution.

    Args:
        stage: ProgressStage
        start_message: Stage start notification message
        complete_message: Stage complete notification message
        fail_message: Stage fail notification message
        detail_builder: Callable that builds a detail dict, will receive `self` and function return `result`
    """
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            reporter = getattr(self, "progress_reporter", None)

            try:
                if reporter:
                    reporter.start_stage(stage, start_message)

                result = func(self, *args, **kwargs)
                details = detail_builder(self, result) if detail_builder else None

                if reporter:
                    reporter.complete_stage(complete_message, details=details)

                return result
            except Exception as e:
                if reporter:
                    reporter.fail_stage(error=str(e), message=fail_message)

                logger.error(f"{fail_message or stage}: {str(e)}")
                raise

        return wrapper

    return decorator
