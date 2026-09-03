"""Chains pipeline modules together. No research logic lives here — only wiring."""

from __future__ import annotations

from typing import Any

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.common.logging import get_logger
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.agent import CodeImplementationAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.code_implementation.schemas import CodeImplementationInput
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.agent import ExperimentDesignAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_design.schemas import ExperimentDesignInput
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.agent import ExperimentExecutionAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import ExperimentExecutionInput
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reflection.agent import ReflectionAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reflection.schemas import ReflectionInput
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.agent import ReportingAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.reporting.schemas import ReportingInput
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.agent import TopicSurveyAgent
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.topic_survey.schemas import TopicSurveyInput

logger = get_logger(__name__)


class PipelineRunner:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.topic_survey = TopicSurveyAgent(config)
        self.experiment_design = ExperimentDesignAgent(config)
        self.code_implementation = CodeImplementationAgent(config)
        self.experiment_execution = ExperimentExecutionAgent(config)
        self.reflection = ReflectionAgent(config)
        self.reporting = ReportingAgent(config)

    def run(self, topic: str) -> list[str]:
        survey = self.topic_survey.run(TopicSurveyInput(topic=topic))
        plan = self.experiment_design.run(ExperimentDesignInput(research=survey)).plan
        implementation = self.code_implementation.run(CodeImplementationInput(plan=plan)).implementation
        if implementation.status != "ready":
            return []
        result = self.experiment_execution.run(
            ExperimentExecutionInput(plan=plan, implementation=implementation)
        ).result

        reflection = None
        try:
            reflection = self.reflection.run(
                ReflectionInput(plan=plan, result=result, implementation=implementation)
            ).reflection
        except Exception:
            # A failed/timed-out reflection must never block reaching
            # reporting — see docs/reflection_design.md §9.
            logger.exception(
                "reflection failed for run_id=%s; continuing without it", plan.run_id
            )

        try:
            report = self.reporting.run(
                ReportingInput(survey=survey, plan=plan, result=result, reflection=reflection)
            )
        except Exception:
            # Reporting is now a multi-turn agentic session (model calls +
            # LaTeX toolchain) with real failure surface — an unexpected
            # exception here must fail loud for this idea, not abort the
            # whole batch. See docs/architecture.md's "fail loud for this
            # idea, don't abort the batch" rule (originally about the
            # code_implementation gate, same principle applies here now).
            logger.exception("reporting failed for run_id=%s", plan.run_id)
            return [f"failed: reporting raised an exception for run_id={plan.run_id}"]

        if report.status != "compiled":
            return [f"failed: {report.notes or 'reporting did not produce a compiled paper'}"]
        return [report.paper_pdf_path]
