from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.manager.schemas import (
    OriginalTask,
    SubtaskContract,
)
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline import manager as manager_module
from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.pipeline.transitions import (
    sync_host_requirements,
    validate_contract,
)


def test_explicit_no_literature_create_skips_survey_and_allows_design() -> None:
    task = OriginalTask(
        topic="从零生成一篇教学短论文；无需文献综述。",
        task_mode="create_new_paper",
    )
    state = manager_module.build_initial_state(task, config={})

    assert "topic_survey" not in state.task_state.enabled_modules
    sync_host_requirements(state)
    research_req = next(item for item in state.task_state.requirements if item.id == "req-research")
    assert research_req.status == "completed"
    assert "disabled" in research_req.notes

    validate_contract(
        state.task_state,
        state.reports,
        SubtaskContract(
            module="experiment_design",
            mode="create",
            goal="Create a design from the original task brief",
            acceptance_criteria=["host-validated"],
        ),
    )


def test_ordinary_create_task_keeps_survey_enabled() -> None:
    state = manager_module.build_initial_state(
        OriginalTask(topic="Write a paper about a new experiment"),
        config={},
    )

    assert "topic_survey" in state.task_state.enabled_modules
