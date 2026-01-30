# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Arxiv research report agent example

This module demonstrates how to implement a controller-based Arxiv research
report generation agent. The agent completes report generation in three stages:
1. Data Collection: collect relevant paper data from the Arxiv API
2. Data Analysis: analyze the collected data
3. Report Generation: generate the research report and charts based on the
   analysis results

Workflow:
1. The user inputs a research request, triggering an input event
2. The event handler performs task planning and creates tasks for the three stages
3. The task scheduler executes tasks in order of priority
4. After each stage is completed, the next stage is automatically triggered

Main components:
- Task executors: DataCollectTaskExecutor, DataAnalysisTaskExecutor,
  ReportGenerateTaskExecutor
- Event handler: DeepSearchEventHandler
- Agent builder: build_deepsearch_agent

End-to-end test points:
- Trigger handle_input to simulate user input starting the full workflow
- Verify execution of the three stages: data collection, data analysis,
  report generation
- Verify that handle_task_completion callbacks correctly chain stages
- Verify that callback outputs from the event handler contain expected marker text
"""

from typing import Dict, List, AsyncIterator, Tuple
import unittest

from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller.base import ControllerConfig, Controller
from openjiuwen.core.controller.modules import EventHandler, EventHandlerInput, TaskManager, \
    TaskExecutor, TaskExecutorDependencies, EventQueue
from openjiuwen.core.controller.schema import ControllerOutputChunk, ControllerOutputPayload, \
    EventType
from openjiuwen.core.controller.schema import Event
from openjiuwen.core.controller.schema.task import Task, TaskStatus
from openjiuwen.core.controller.schema import TextDataFrame
from openjiuwen.core.session import Session
from openjiuwen.core.session.internal.wrapper import TaskSession
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent.base import AbilityManager, ControllerAgent
from openjiuwen.core.common.logging import logger


class DataCollectTaskExecutor(TaskExecutor):
    """Data collection task executor

    Responsible for executing data collection tasks and collecting required
    financial data from various data sources.

    Main responsibilities:
    - Collect data from various channels
    - Store all collected data in the context engine

    Args:
        config: controller configuration including task scheduling parameters
        ability_manager: ability manager providing tools and capabilities
            required for data collection
        context_engine: context engine used to store and manage task context data
        task_manager: task manager used to manage task states
        event_queue: event queue used to publish events such as task completion
    """

    def __init__(self, dependencies: TaskExecutorDependencies):
        super().__init__(dependencies)

    async def execute_ability(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        """Execute data collection task

        Collect financial data from various data sources and stream out the
        collected results. The collected data is stored in the context engine
        for subsequent data analysis tasks.

        Args:
            task_id: task ID used to identify the current task
            session: session object containing session context information

        Yields:
            ControllerOutputChunk: output chunks produced during data collection,
                which may include:
                - data collection progress information
                - summary of collected data
                - status updates during collection

        Note:
            - The context for the data collection flow is stored in the context
              corresponding to Task.context_id in the context engine
            - When execution finishes, a task completion event is automatically
              triggered to start the data analysis task
            - If data collection fails, a task failure event is triggered
        """
        # Simple execution: return processing information
        yield ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text="正在收集芯片相关的Arxiv论文数据...")]
            ),
            last_chunk=False
        )

        # Task completion signal
        yield ControllerOutputChunk(
            index=1,
            type="controller_output",
            payload=ControllerOutputPayload(
                type=EventType.TASK_COMPLETION,
                data=[TextDataFrame(type="text", text="芯片相关Arxiv论文数据收集完成啦")]
            ),
            last_chunk=True
        )

        yield ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text="芯片相关Arxiv论文数据收集完成")]
            ),
            last_chunk=False
        )

    async def can_pause(self, task_id: str, session: Session) -> Tuple[bool, str]:
        """Check whether the task can be paused

        Args:
            task_id: task ID
            session: session object

        Returns:
            Tuple[bool, str]: (whether it can be paused, reason description)

        """
        # Not applicable; can be left unimplemented
        ...

    async def pause(self, task_id: str, session: Session) -> bool:
        """Pause task execution

        Args:
            task_id: task ID
            session: session object

        Returns:
            bool: whether the pause succeeded
        """
        # Not applicable; can be left unimplemented
        ...

    async def can_cancel(self, task_id: str, session: Session) -> bool:
        """Check whether the task can be cancelled

        Args:
            task_id: task ID
            session: session object

        Returns:
            bool: whether it can be cancelled
        """
        # Not applicable; can be left unimplemented
        ...

    async def cancel(self, task_id: str, session: Session) -> bool:
        """Cancel task execution

        Args:
            task_id: task ID
            session: session object

        Returns:
            bool: whether the cancellation succeeded
        """
        ...


class DataAnalysisTaskExecutor(TaskExecutor):
    """Data analysis task executor

    Responsible for executing data analysis tasks and performing in-depth
    analysis on the collected financial data.

    Main responsibilities:
    - Read data collected by data collection tasks
    - Perform financial metric calculation and analysis
    - Perform trend analysis and forecasting
    - Identify key insights and patterns
    - Save analysis results into the context engine

    Args:
        config: controller configuration including task scheduling parameters
        ability_manager: ability manager providing tools and capabilities
            required for data analysis (such as AI analysis models)
        context_engine: context engine used to read collected data and store
            analysis results
        task_manager: task manager used to manage task states
        event_queue: event queue used to publish events such as task completion
    """

    def __init__(self, dependencies: TaskExecutorDependencies):
        super().__init__(dependencies)

    async def execute_ability(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        """Execute data analysis task

        Perform in-depth analysis on the collected financial data and stream out
        analysis results. The analysis results are stored in the context engine
        for subsequent report generation tasks.

        Args:
            task_id: task ID used to identify the current task
            session: session object containing session context information

        Yields:
            ControllerOutputChunk: output chunks produced during data analysis,
                which may include:
                - analysis progress information
                - summary of analysis results
                - key findings and insights
                - status updates during analysis
        """
        # Simple execution: return processing information
        yield ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text="正在分析芯片相关的Arxiv论文数据...")]
            ),
            last_chunk=False
        )

        # Return task completion information
        yield ControllerOutputChunk(
            index=1,
            type="controller_output",
            payload=ControllerOutputPayload(
                type=EventType.TASK_COMPLETION,
                data=[TextDataFrame(type="text", text="芯片相关Arxiv论文数据分析完成")]
            ),
            last_chunk=True
        )

    async def can_pause(self, task_id: str, session: Session) -> Tuple[bool, str]:
        """Check whether the task can be paused

        Args:
            task_id: task ID
            session: session object

        Returns:
            Tuple[bool, str]: (whether it can be paused, reason description)
        """
        # Not applicable; can be left unimplemented
        ...

    async def pause(self, task_id: str, session: Session) -> bool:
        """Pause task execution

        Args:
            task_id: task ID
            session: session object

        Returns:
            bool: whether the pause succeeded
        """
        # Not applicable; can be left unimplemented
        ...

    async def can_cancel(self, task_id: str, session: Session) -> bool:
        """Check whether the task can be cancelled

        Args:
            task_id: task ID
            session: session object

        Returns:
            bool: whether it can be cancelled
        """
        # Not applicable; can be left unimplemented
        ...

    async def cancel(self, task_id: str, session: Session) -> bool:
        """Cancel task execution

        Args:
            task_id: task ID
            session: session object

        Returns:
            bool: whether the cancellation succeeded
        """
        # Not applicable; can be left unimplemented
        ...


class ReportGenerateTaskExecutor(TaskExecutor):
    """Report generation task executor

    Responsible for executing report generation tasks and generating the final
    research report based on data analysis results.

    Main responsibilities:
    - Read analysis results from data analysis tasks
    - Organize report structure and content
    - Generate report text (possibly using AI generation models)
    - Format the report (add charts, tables, etc.)
    - Save the final report to the context engine

    Args:
        config: controller configuration including task scheduling parameters
        ability_manager: ability manager providing tools and capabilities
            required for report generation (such as text generation models)
        context_engine: context engine used to read analysis results and store
            generated reports
        task_manager: task manager used to manage task states
        event_queue: event queue used to publish events such as task completion
    """

    def __init__(self, dependencies: TaskExecutorDependencies):
        super().__init__(dependencies)

    async def execute_ability(self, task_id: str, session: Session) -> AsyncIterator[ControllerOutputChunk]:
        """Execute report generation task

        Generate the final research report based on data analysis results and
        stream out report content. The generated report is stored in the context
        engine and can be retrieved by the user via the session.

        Args:
            task_id: task ID used to identify the current task
            session: session object containing session context information

        Yields:
            ControllerOutputChunk: output chunks produced during report
                generation, which may include:
                - report generation progress information
                - streaming output of different report parts (summary, analysis,
                  conclusions, etc.)
                - status updates when report generation completes
        """
        # Simple execution: return processing information
        yield ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text="正在生成芯片研究报告...")]
            ),
            last_chunk=False
        )

        yield ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text="芯片领域研究报告已生成")]
            ),
            last_chunk=False
        )

        # Return task completion information
        yield ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type=EventType.TASK_COMPLETION,
                data=[TextDataFrame(type="text", text="芯片研究报告生成完成")]
            ),
            last_chunk=True
        )

    async def can_pause(self, task_id: str, session: Session) -> Tuple[bool, str]:
        """Check whether the task can be paused

        Args:
            task_id: task ID
            session: session object

        Returns:
            Tuple[bool, str]: (whether it can be paused, reason description)
        """
        # Not applicable; can be left unimplemented
        ...

    async def pause(self, task_id: str, session: Session) -> bool:
        """Pause task execution

        Args:
            task_id: task ID
            session: session object

        Returns:
            bool: whether the pause succeeded
        """
        # Not applicable; can be left unimplemented
        ...

    async def can_cancel(self, task_id: str, session: Session) -> bool:
        """Check whether the task can be cancelled

        Args:
            task_id: task ID
            session: session object

        Returns:
            bool: whether it can be cancelled
        """
        # Not applicable; can be left unimplemented
        ...

    async def cancel(self, task_id: str, session: Session) -> bool:
        """Cancel task execution

        Args:
            task_id: task ID
            session: session object

        Returns:
            bool: whether the cancellation succeeded
        """
        # Not applicable; can be left unimplemented
        ...


def build_data_collect_task_executor(dependencies: TaskExecutorDependencies) -> DataCollectTaskExecutor:
    """Build data collection task executor

    Factory function used to create a DataCollectTaskExecutor instance. After
    the executor is registered to the controller, the controller will call
    this function to create an executor instance when a data collection task
    needs to be executed.

    Args:
        dependencies: Task executor dependencies

    Returns:
        DataCollectTaskExecutor: data collection task executor instance

    Note:
        This function is registered in the controller's task executor registry
        and is called when a task of type "data_collect" is encountered.
    """
    return DataCollectTaskExecutor(dependencies)


def build_data_analysis_task_executor(dependencies: TaskExecutorDependencies) -> DataAnalysisTaskExecutor:
    """Build data analysis task executor

    Factory function used to create a DataAnalysisTaskExecutor instance. After
    the executor is registered to the controller, the controller will call
    this function to create an executor instance when a data analysis task
    needs to be executed.

    Args:
        dependencies: Task executor dependencies

    Returns:
        DataAnalysisTaskExecutor: data analysis task executor instance

    Note:
        This function is registered in the controller's task executor registry
        and is called when a task of type "data_analysis" is encountered.
    """
    return DataAnalysisTaskExecutor(dependencies)


def build_report_generate_task_executor(
        dependencies: TaskExecutorDependencies
) -> ReportGenerateTaskExecutor:
    """Build report generation task executor

    Factory function used to create a ReportGenerateTaskExecutor instance.
    After the executor is registered to the controller, the controller will
    call this function to create an executor instance when a report generation
    task needs to be executed.

    Args:
        dependencies: Task executor dependencies

    Returns:
        ReportGenerateTaskExecutor: report generation task executor instance

    Note:
        This function is registered in the controller's task executor registry
        and is called when a task of type "report_generate" is encountered.
    """
    return ReportGenerateTaskExecutor(dependencies)


class DeepSearchEventHandler(EventHandler):
    """Arxiv paper search event handler

    Event handler for the DeepSearch agent. Responsible for handling various
    types of events and coordinating the task execution workflow.

    Main responsibilities:
    - Handle input events: receive user requests, perform task planning, and create tasks for the three stages
    - Handle task completion events: monitor task execution status and automatically trigger the next stage
    - Handle task failure events: handle situations where task execution fails

    Workflow:
    1. User inputs a research request -> handle_input is called
    2. Perform task planning and create tasks for data collection, data
       analysis, and report generation
    3. After data collection tasks are completed -> handle_task_completion
       is called
    4. Check whether all data collection tasks are completed; if so, change the
       status of data analysis tasks to submitted
    5. After data analysis tasks are completed -> handle_task_completion
       is called
    6. Check whether all data analysis tasks are completed; if so, change the
       status of report generation tasks to submitted
    7. After report generation tasks are completed -> handle_task_completion
       is called and the workflow ends
    """

    def __init__(self):
        """Initialize event handler"""
        super().__init__()

    async def _planning(self, event: Event, session: Session) -> Dict:
        """Plan tasks

        Perform task planning based on the user's research request and determine
        the concrete tasks to be executed. The planning result contains detailed
        task information for the three stages: data collection, data analysis,
        and report generation.

        Args:
            event: input event containing the user's research request
            session: session object containing session context and history

        Returns:
            Dict: planning result containing the following information:
                - data collection task list: what data to collect (stock prices,
                  financial reports, news, etc.)
                - data analysis task list: what aspects to analyze (financial
                  metrics, trends, causes, etc.)
                - report generation task information: report structure and
                  formatting requirements

        Note:
            - Planning capabilities (such as LLMs) in ability_manager can be
              used for task planning
            - The planning result is used to create concrete task instances
            - The planning process can use context_engine to obtain historical
              context information
        """
        # Simple planning: create one data collection task, one data analysis
        # task, and one report generation task
        return {
            "data_collect_tasks": [{"topic": "芯片", "type": "arxiv"}],
            "data_analysis_tasks": [{"type": "trend_analysis"}],
            "report_generate_tasks": [{"format": "markdown", "type": "research_report"}]
        }

    def _create_data_collect_task(self, planning_task: Dict, session: Session) -> List[Task]:
        """Create data collection tasks

        Create a list of data collection tasks based on the planning result.
        These tasks are submitted for execution immediately.

        Args:
            planning_task: planning result containing details of data
                collection tasks
            session: session object containing session context and history

        Returns:
            List[Task]: list of data collection tasks, each representing a
            data collection operation

        Note:
            - Task priority should be set to 1 (highest priority)
            - Task status should be set to submitted, indicating that it can be
              executed immediately
            - Each task should have a unique task_id and corresponding context_id
            - Tasks are added to the task_manager for management
        """
        tasks = []
        for i, task_info in enumerate(planning_task["data_collect_tasks"]):
            task = Task(
                session_id=session.session_id(),
                task_id="task_DC_id{}".format(i),
                task_type="data_collect",
                priority=1,
                status=TaskStatus.SUBMITTED,
                context_id="context_DC_id{}".format(i),
                params={"topic": task_info["topic"], "type": task_info["type"]}
            )
            tasks.append(task)
        return tasks

    def _create_data_analysis_task(self, planning_task: Dict, session: Session) -> List[Task]:
        """Create data analysis tasks

        Create a list of data analysis tasks based on the planning result.
        These tasks will wait until data collection tasks are completed.

        Args:
            planning_task: planning result containing details of data analysis
                tasks

        Returns:
            List[Task]: list of data analysis tasks, each representing a data
            analysis operation

        Note:
            - Task priority should be set to 2 (medium priority)
            - Task status should be set to waiting, indicating that it must
              wait for preceding tasks to complete
            - Each task's ref_task_id should point to the corresponding data
              collection task ID
            - Tasks are added to the task_manager for management and wait to be
              activated
        """
        tasks = []
        for i, task_info in enumerate(planning_task["data_analysis_tasks"]):
            task = Task(
                session_id=session.session_id(),
                task_id="task_DA_id{}".format(i),
                task_type="data_analysis",
                priority=2,
                status=TaskStatus.WAITING,
                context_id="context_DA_id{}".format(i),
                params={"type": task_info["type"]}
            )
            tasks.append(task)
        return tasks

    def _create_report_generate_task(self, planning_task: Dict, session: Session) -> List[Task]:
        """Create report generation tasks

        Create a list of report generation tasks based on the planning result.
        These tasks will wait until data analysis tasks are completed.

        Args:
            planning_task: planning result containing details of report
                generation tasks

        Returns:
            List[Task]: list of report generation tasks, each representing a
            report generation operation

        Note:
            - Task priority should be set to 3 (lowest priority)
            - Task status should be set to waiting, indicating that it must
              wait for preceding tasks to complete
            - Tasks are added to the task_manager for management and wait to be
              activated
            - Usually a single research report needs only one report generation
              task
        """
        tasks = []
        for i, task_info in enumerate(planning_task["report_generate_tasks"]):
            task = Task(
                session_id=session.session_id(),
                task_id="task_RG_id{}".format(i),
                task_type="report_generate",
                priority=3,
                status=TaskStatus.WAITING,
                context_id="context_RG_id{}".format(i),
                params={"format": task_info["format"], "type": task_info["type"]}
            )
            tasks.append(task)
        return tasks

    async def handle_input(self, inputs: Event):
        """Handle input events

        Called when the user inputs a new research request. This method performs
        task planning, creates tasks for the three stages, and adds them to the
        task manager.

        Args:
            inputs: event handler input containing event and session information

        Process:
            1. Call the _planning method to perform task planning
            2. Create a data collection task list (priority 1, status submitted)
            3. Create a data analysis task list (priority 2, status waiting)
            4. Create a report generation task list (priority 3, status waiting)
            5. Add all tasks to the task_manager

        Note:
            - Data collection tasks start executing immediately
            - Data analysis and report generation tasks wait for preceding
              tasks to complete
        """
        logger.info("handle input called")
        planning_result = await self._planning(inputs.event, inputs.session)
        tasks = []
        tasks.extend(self._create_data_collect_task(planning_result, inputs.session))
        tasks.extend(self._create_data_analysis_task(planning_result, inputs.session))
        tasks.extend(self._create_report_generate_task(planning_result, inputs.session))
        await self.task_manager.add_task(tasks)
        logger.info("handle input end, successfully add tasks to task manager")
        output_chunk = ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text="成功调用hanle_input回调")]
            ),
            last_chunk=False
        )
        await inputs.session.write_stream(output_chunk)
        return {"status": "success", "tasks_created": 1}

    async def handle_task_interaction(self, inputs: EventHandlerInput):
        """Handle task interaction events

        Called when a task needs to interact with the user (for example, to
        request confirmation or additional input).

        Args:
            inputs: event handler input containing event and session information

        Note:
            The DeepSearch agent does not need to interact with the user, so
            this method does not need an implementation. If interaction is
            required, user interaction logic can be implemented here.
        """
        # Not expected in this scenario; no implementation needed
        ...

    async def handle_task_completion(self, inputs: Event):
        """Handle task completion events

        Called when a task finishes execution. This method checks whether all
        tasks in the current stage have completed and, if so, activates tasks
        in the next stage.

        Args:
            inputs: event handler input containing information about the
                completed task and session

        Process:
            Stage 1 (data collection tasks completed):
                1. Check whether all data collection tasks with priority 1 are
                   completed
                2. If any task is not completed, exit (wait for others to finish)
                3. If all are completed, check whether data analysis tasks with
                   priority 2 are still waiting
                4. If they are waiting, set their status to submitted to trigger
                   execution

            Stage 2 (data analysis tasks completed):
                1. Check whether all data analysis tasks with priority 2 are
                   completed
                2. If any task is not completed, exit (wait for others to finish)
                3. If all are completed, check whether report generation tasks
                   with priority 3 are still waiting
                4. If they are waiting, set their status to submitted to trigger
                   execution

            Stage 3 (report generation tasks completed):
                - All tasks are completed and the whole workflow ends

        Note:
            - All tasks in a stage must be completed before the next stage can
              start
            - Task execution is triggered by changing their status to submitted
            - The task scheduler automatically detects status changes and
              executes tasks
        """
        logger.info("handle task completion called")
        all_tasks = await self.task_manager.get_task(task_filter=None)

        output_chunk = ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type="processing",
                data=[TextDataFrame(type="text", text=f"成功调用handle_task_completion回调 event: {inputs.event.event_id}")]
            ),
            last_chunk=False
        )
        await inputs.session.write_stream(output_chunk)
        # activate next high priority tasks if all current priority tasks are completed
        # get all priority levels, sort them in ascending order
        priorities = sorted(set(task.priority for task in all_tasks))

        for i, current_priority in enumerate(priorities):
            # check if all tasks of current priority are completed
            current_priority_tasks = [task for task in all_tasks if task.priority == current_priority]
            all_current_completed = all(task.status == TaskStatus.COMPLETED for task in current_priority_tasks)

            if all_current_completed:
                # check next priority
                if i + 1 < len(priorities):
                    next_priority = priorities[i + 1]
                    # check if next high priority is waiting, if so, activate it
                    next_priority_waiting_tasks = [task for task in all_tasks if
                                                   task.priority == next_priority and task.status == TaskStatus.WAITING]

                    if next_priority_waiting_tasks:
                        for task in next_priority_waiting_tasks:
                            task.status = TaskStatus.SUBMITTED
                        return {"status": "success", "tasks_created": 1}

        return {"status": "success", "tasks_created": 1}

    async def handle_task_failed(self, inputs: EventHandlerInput):
        """Handle task failure events

        Called when a task fails. Task failure causes the entire research report
        generation workflow to terminate.

        Args:
            inputs: event handler input containing information about the failed
                task and the error

        Raises:
            Exception: indicates task execution failure and termination of the
                whole workflow

        Note:
            - Failure reasons can be logged or stored in the context engine
            - Other created tasks can optionally be cleaned up
            - The user can optionally be notified of the failure reason
        """
        logger.info("handle task failed called")
        return {"status": "success", "tasks_failed": 1}


async def build_deepsearch_agent(agent_card: AgentCard) -> ControllerAgent:
    """Build DeepSearch Agent

    Factory function used to create and configure the complete DeepSearch
    research paper agent. This function creates the controller, registers task
    executors, sets the event handler, and finally returns the fully configured
    agent.

    Args:
        agent_card: agent card containing basic information about the agent
            (id, name, description, etc.)

    Returns:
        ControllerAgent: fully configured DeepSearch Agent instance ready to use

    Process:
        1. Create a Controller instance
        2. Create and set the DeepSearchEventHandler event handler
        3. Register three task executors:
           - data_collect: data collection task executor
           - data_analysis: data analysis task executor
           - report_generate: report generation task executor
        4. Create a ControllerAgent instance and associate it with the
           controller and card
        5. Return the configured agent
    """
    deepsearch_controller = Controller()
    config = ControllerConfig(enable_task_persistence=True, event_queue_size=5555)
    deepsearch_agent = ControllerAgent(
        card=agent_card,
        controller=deepsearch_controller,
        config=config
    )
    deepsearch_controller.set_event_handler(DeepSearchEventHandler())
    deepsearch_controller.add_task_executor(
        "data_collect", build_data_collect_task_executor
    ).add_task_executor(
        "data_analysis", build_data_analysis_task_executor
    ).add_task_executor(
        "report_generate", build_report_generate_task_executor
    )
    return deepsearch_agent


class DeepSearchAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_deepsearch_end_to_end(self):
        agent_card = AgentCard(
            id="deepsearch",
            name="DeepSearch",
            description="Arxiv研究报告智能体，可以通过收集、分析数据生成Arxiv研究报告",
        )
        agent = await build_deepsearch_agent(agent_card)
        session = TaskSession(session_id="example_deepsearch")
        output_texts: List[str] = []

        async for chunk in agent.stream("帮我查找芯片相关研究论文", session):
            logger.info(chunk)
            if isinstance(chunk, ControllerOutputChunk):
                if chunk.payload and chunk.payload.data:
                    for item in chunk.payload.data:
                        if isinstance(item, TextDataFrame):
                            output_texts.append(item.text)
            else:
                output_texts.append(chunk.payload.get("result", ""))

        full_output = "\n".join(output_texts)

        assert "正在收集芯片相关的Arxiv论文数据..." in full_output, "断言失败：数据收集阶段未启动"
        assert "芯片相关Arxiv论文数据收集完成" in full_output, "断言失败：数据收集阶段未报告完成"
        assert "正在分析芯片相关的Arxiv论文数据..." in full_output, "断言失败：数据分析阶段未启动"
        assert "芯片相关Arxiv论文数据分析完成" in full_output, "断言失败：数据分析阶段未报告完成"
        assert "正在生成芯片研究报告..." in full_output, "断言失败：报告生成阶段未启动"
        assert "芯片研究报告生成完成" in full_output, "断言失败：报告生成阶段未报告完成"
        assert "成功调用hanle_input回调" in full_output, "断言失败：没有调用到注册的handle_input函数"
        assert full_output.count("成功调用handle_task_completion回调") == 3, \
            "断言失败：handle_task_completion 回调未被调用 3 次"
        logger.info("✅ test_deepsearch_end_to_end passed")

    async def test_deepsearch_end_to_end_invoke(self):
        agent_card = AgentCard(
            id="deepsearch",
            name="DeepSearch",
            description="Arxiv研究报告智能体，可以通过收集、分析数据生成Arxiv研究报告",
        )
        agent = await build_deepsearch_agent(agent_card)
        session = TaskSession(session_id="example_deepsearch")

        result = await agent.invoke("帮我查找芯片相关研究论文", session)
        full_output = "".join(map(str, result.data))
        logger.info(f"deepsearch agent invoke result: {full_output}")
        assert "正在收集芯片相关的Arxiv论文数据" in full_output, "断言失败：数据收集阶段未启动"
        assert "芯片相关Arxiv论文数据收集完成" in full_output, "断言失败：数据收集阶段未报告完成"
        assert "正在分析芯片相关的Arxiv论文数据" in full_output, "断言失败：数据分析阶段未启动"
        assert "芯片相关Arxiv论文数据分析完成" in full_output, "断言失败：数据分析阶段未报告完成"
        assert "正在生成芯片研究报告" in full_output, "断言失败：报告生成阶段未启动"
        assert "芯片研究报告生成完成" in full_output, "断言失败：报告生成阶段未报告完成"
        assert "成功调用hanle_input回调" in full_output, "断言失败：没有调用到注册的handle_input函数"
        assert full_output.count("成功调用handle_task_completion回调") == 3, \
            "断言失败：handle_task_completion 回调未被调用 3 次"
        logger.info("✅ test_deepsearch_end_to_end_invoke passed")
