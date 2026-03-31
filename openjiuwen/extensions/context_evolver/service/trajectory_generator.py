# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Standalone trajectory generation utilities.
3 options : parallel / sequential / combined

Provides MaTTS-mode trial runners that work with any ReActAgent without
requiring ContextEvolvingReActAgent or TaskMemoryRail.

Functions
---------
format_trajectory(messages)
    Convert a message list into a clean trajectory string.

summarize_trajectories(memory_service, user_id, params)
    Convert trajectories + feedback into memory via TaskMemoryService.

run_trials(...)
    Run MaTTS trials (parallel / sequential / combined) and summarize.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from openjiuwen.core.common.logging import context_engine_logger as logger
from openjiuwen.core.foundation.llm import AssistantMessage, ToolMessage, UserMessage
from openjiuwen.core.single_agent import Session
from ..core import config as memory_config
from ..core.persistence import MemoryPersistenceHelper
from ..core.schema import VectorNode
from .task_memory_service import TaskMemoryService

_ALGO_TO_NAME = {
    "ACE": "ace",
    "ReasoningBank": "rb",
    "ReMe": "reme",
    "RefCon": "reme",
    "DivCon": "reme",
}

# ---------------------------------------------------------------------------
# Input dataclass
# ---------------------------------------------------------------------------


@dataclass
class SummarizeTrajectoriesInput:
    """Parameters for :func:`summarize_trajectories`.

    Mandatory
    ---------
    query, trajectory, matts_mode

    Algorithm-specific (optional)
    -----------------------------
    ground_truth:
        Raw expected answer string.  Passed to the summary context so ACE ops
        can use it when ``USE_GROUNDTRUTH: true`` in config.yaml.
        Not used by RB or ReMe.
    feedback:
        Task/unit-test specific evaluation result(s).  Passed as-is to the
        summary context (e.g. for ACE when ``USE_GROUNDTRUTH: true``).
        Can be a single value or a list aligned with trajectories.
        Not consumed here — algorithm ops interpret it directly.
    scores:
        Integer scores aligned with trajectories.  Required for ReMe.
        Optional for RB: when ``USE_GOLDLABEL: true`` in config.yaml, a
        boolean ``label`` list is derived from these scores and forwarded
        to the memory service.
    """
    query: str
    trajectory: Union[str, List[Optional[str]]]
    matts_mode: str
    ground_truth: Optional[str] = None
    feedback: Optional[List[str]] = None
    score: Optional[List[int]] = None


@dataclass
class RunTrialsInput:
    """Parameters for :func:`run_trials`."""
    memory_service: TaskMemoryService
    user_id: str
    question: str
    ground_truth: str
    matts_k: Optional[int] = None
    matts_mode: str = "parallel"
    # Persistence configuration — mirrors ContextEvolvingReActAgent constructor
    persist_type: Optional[str] = None
    persist_path: str = "./memories/{algo_name}/{user_id}.json"
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "vector_nodes"


@dataclass
class TrialOutput:
    """Result of a single trial run."""
    trajectory: Optional[str]
    feedback: Any = None
    score: Optional[int] = None


# ---------------------------------------------------------------------------
# Standalone helpers
# ---------------------------------------------------------------------------

def format_trajectory(messages: list) -> str:
    """Format a list of messages into a clean trajectory string.

    Strips injected context blocks (memory context, self-refine preambles)
    and formats each message as a labelled line:

    * ``USER: <text>``
    * ``THOUGHT: <text>``
    * ``ACTION: <tool_name>(<args>)``
    * ``OBSERVATION: <text>``

    Parameters
    ----------
    messages:
        List of ``UserMessage``, ``AssistantMessage``, or ``ToolMessage``
        objects from the session context.

    Returns
    -------
    str
        Newline-joined trajectory string.
    """
    transcript: List[str] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            content = msg.content
            # Strip "Task:" prefix (e.g. "Task:\nFind the bug.")
            task_marker = "Task:"
            if content.startswith(task_marker):
                content = content[len(task_marker):].lstrip("\n ").strip()
            # Strip injected memory block and everything after it
            related_exp_marker = "Some Related Experience to help you complete the task"
            idx = content.find(related_exp_marker)
            if idx != -1:
                content = content[:idx].strip()
            # If a "Question: " marker exists, keep only from the last occurrence
            question_marker = "Question: "
            last_idx = content.rfind(question_marker)
            if last_idx != -1:
                content = content[last_idx + len(question_marker):]
            transcript.append(f"USER: {content.strip()}")
        elif isinstance(msg, AssistantMessage):
            if msg.content:
                transcript.append(f"THOUGHT: {msg.content}")
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    transcript.append(f"ACTION: {tc.name}({tc.arguments})")
        elif isinstance(msg, ToolMessage):
            transcript.append(f"OBSERVATION: {msg.content}")
    return "\n".join(transcript)


async def summarize_trajectories(
    memory_service: TaskMemoryService,
    user_id: str,
    params: SummarizeTrajectoriesInput,
) -> Optional[Dict[str, Any]]:
    """Summarize trajectories and store the result in the memory service.

    Applies sequential-mode truncation (keep last only), then delegates to
    ``memory_service.summarize`` with algorithm-appropriate kwargs.

    Label derivation
    ----------------
    * ``feedback`` is passed raw to the summary context — it is task/unit-test
      specific and interpreted by the algorithm ops (e.g. ACE).
    * ``label`` (``List[bool]``) is derived from ``scores`` **only** when
      ``USE_GOLDLABEL: true`` in config.yaml and scores are provided.  This is
      used by RB (optional) and expected by ReMe.

    Parameters
    ----------
    memory_service:
        ``TaskMemoryService`` instance that owns the memory store.
    user_id:
        Memory namespace identifier.
    params:
        See :class:`SummarizeTrajectoriesInput`.

    Returns
    -------
    dict or None
        The summary result dict, or ``None`` if summarization failed.
    """
    if hasattr(params.trajectory, "__iter__") and not isinstance(params.trajectory, str):
        trajectories = list(params.trajectory)
    else:
        trajectories = [params.trajectory]

    ground_truth: str = params.ground_truth if params.ground_truth else ""
    feedbacks: List[str] = list(params.feedback) if params.feedback else []
    scores: List[int] = list(params.score) if params.score else []

    # --- sequential mode: keep only the last trajectory -------------------
    if params.matts_mode == "sequential":
        trajectories = [trajectories[-1]] if trajectories else []
        feedbacks = [feedbacks[-1]] if feedbacks else []
        scores = [scores[-1]] if scores else []

    # --- build kwargs to forward to the summary context -------------------
    extra_kwargs: Dict[str, Any] = {}

    # score — forwarded for ReMe-family algorithms (ReMe, RefCon, DivCon)
    _reme_family = {"reme", "refcon", "divcon"}
    is_reme = str(memory_config.get("SUMMARY_ALGO", "REME")).lower() in _reme_family
    if is_reme and scores:
        extra_kwargs["score"] = scores

    # label — derived from scores, forwarded only when USE_GOLDLABEL is enabled and algo is RB
    is_rb = str(memory_config.get("SUMMARY_ALGO", "RB")).lower() == "rb"
    use_goldlabel = str(memory_config.get("USE_GOLDLABEL", "false")).lower() == "true"
    if is_rb and use_goldlabel and scores:
        extra_kwargs["label"] = [s == 1.0 for s in scores]

    # feedback and ground_truth — forwarded only when USE_GROUNDTRUTH is enabled and algo is ACE
    is_ace = str(memory_config.get("SUMMARY_ALGO", "ACE")).lower() == "ace"
    use_groundtruth = str(memory_config.get("USE_GROUNDTRUTH", "false")).lower() == "true"
    if is_ace and use_groundtruth and feedbacks:
        extra_kwargs["feedback"] = feedbacks
    if is_ace and use_groundtruth and ground_truth:
        extra_kwargs["ground_truth"] = ground_truth

    try:
        return await memory_service.summarize(
            user_id=user_id,
            matts=params.matts_mode,
            query=params.query,
            trajectories=trajectories,
            **extra_kwargs,
        )
    except Exception as exc:
        logger.error("Failed to summarize trajectories: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Internal helper — trial loop (shared by all matts_mode variants)
# ---------------------------------------------------------------------------

_SELF_REFINE_PROMPT = (
    "Let's carefully re-examine the previous trajectory, including your reasoning "
    "steps and action taken. Pay special attention to whether you used the best "
    "search sequence and whether you used the tool correctly. If you find "
    "inconsistencies, correct them. If everything seems correct, make it more "
    "efficient. Now, solve the same problem again from scratch.\n\n"
)

_SELF_DIVERSITY_PROMPT = (
    "Let's carefully re-examine the previous trajectory, including your reasoning "
    "steps and action taken. The solution might be correct or wrong. Now, solve the "
    "same problem again from scratch using DIFFERENT reasoning approach. "
    "Focus on exploring alternative strategies.\n\n"
)


def evaluate_trial(
    question: str,
    output: str,
    ground_truth: Optional[str] = None,
) -> tuple:
    """Evaluate a single trial and return ``(feedback, score)``.

    IMPORTANT
    ----------
        **This function is intended to be customised for your task/dataset.**
        Replace the body with any evaluation logic relevant to your benchmark
        or application, for example:

        * Run a unit-test suite and return its pass/fail status.
        * Call a reward model or LLM judge.
        * Use task-specific metrics (F1, exact-match, execution accuracy, …).

    When ``ground_truth`` is provided the default implementation performs a
    simple case-insensitive substring check of ``ground_truth`` inside
    ``output``.  When ``ground_truth`` is omitted (or ``None``) no correctness
    check is performed and the trial is assumed successful (score=1).
    """
    if ground_truth:
        is_correct = ground_truth.lower() in output.lower()
        feedback = "success" if is_correct else "failure"
        score = 1 if is_correct else 0
    else:
        # No ground truth available — treat as successful by default.
        feedback = "success"
        score = 1
    return feedback, score


async def _run_trials_inner(
    agent: Any,
    question: str,
    ground_truth: str,
    matts_k: int,
    self_refine: bool,
) -> List[TrialOutput]:
    """Run *matts_k* trials and return per-trial results.

    Parameters
    ----------
    agent:
        Any ReActAgent with ``invoke()``, ``card``, and ``context_engine``.
    question:
        The question to answer.
    ground_truth:
        Expected answer string, forwarded to :func:`evaluate_trial`.
    matts_k:
        Number of trials to run.
    self_refine:
        When ``True`` each trial after the first prepends the previous
        trajectory with :data:`_SELF_REFINE_PROMPT` (sequential/combined).
        When ``False`` every trial uses the same ``"Question: {question}"``
        query (parallel).

    Returns
    -------
    list of TrialOutput
        One :class:`TrialOutput` per trial.  Failed trials have
        ``trajectory=None``, ``feedback=None``, ``score=None``.
    """
    results: List[TrialOutput] = []

    for run_id in range(matts_k):
        logger.info("    Running trial %s/%s...", run_id + 1, matts_k)

        session = Session(card=agent.card)
        try:
            # Build query — use previous trajectory for self-refine modes
            prev_trajectories = [r.trajectory for r in results if r.trajectory]
            if self_refine and run_id > 0 and prev_trajectories:
                prev_traj = prev_trajectories[-1]
                if memory_config.get("COMBINED_MATTS_PROMPT", "refine") == "refine":
                    current_query = (
                        f"Previous attempt:\n{prev_traj}\n\n"
                        + _SELF_REFINE_PROMPT
                        + f"Question: {question}"
                    )
                else:
                    current_query = (
                        f"Previous attempt:\n{prev_traj}\n\n"
                        + _SELF_DIVERSITY_PROMPT
                        + f"Question: {question}"
                    )
            else:
                current_query = f"Question: {question}"

            invoke_inputs: Dict[str, Any] = {"query": current_query}
            if self_refine:
                invoke_inputs["retrieval_query"] = question

            # Use _invoke_with_memory if available to bypass invoke's routing logic and avoid infinite recursion.
            _inner_invoke = getattr(agent, "_invoke_with_memory", agent.invoke)
            result = await _inner_invoke(invoke_inputs, session=session)
            output = result.get("output", "")

            # Evaluate the trial using the pluggable evaluate_trial function
            feedback, score = evaluate_trial(question, output, ground_truth=ground_truth)
            status = "SUCCESS" if score == 1 else "FAILURE"
            logger.info("      Result: %s (Output: %s...)", status, output[:50])

            context = agent.context_engine.get_context(
                session_id=session.get_session_id(),
                context_id="default_context_id",
            )
            if context:
                trajectory = format_trajectory(context.get_messages())
            else:
                trajectory = f"USER: {question}\nASSISTANT: {output}"

            for line in trajectory.split("\n"):
                logger.info("      %s", line)

            results.append(TrialOutput(trajectory=trajectory, feedback=feedback, score=score))

        except Exception as exc:
            logger.error("Trial failed: %s", exc)
            results.append(TrialOutput(trajectory=None, feedback=None, score=None))

    return results


# ---------------------------------------------------------------------------
# Public MaTTS trial runner
# ---------------------------------------------------------------------------

async def run_trials(
    agent: Any,
    params: RunTrialsInput,
) -> Optional[Dict[str, Any]]:
    """Run MaTTS trials and summarize the resulting trajectories.

    A single entry point for all four MaTTS patterns:

    * ``"none"``       — No scaling: exactly **1** trial is run and that
      single trajectory is summarized.  ``matts_k`` is ignored.

    * ``"parallel"``   — Step 7 (RB): *matts_k* independent trials, each with
      the same ``"Question: {question}"`` query.  All trajectories are
      summarized together.

    * ``"sequential"`` — Step 8 (ACE): *matts_k* self-refine trials where each
      trial after the first prepends the previous trajectory with a correction
      prompt.  Only the **last** trajectory is summarized.

    * ``"combined"``   — Step 9 (RefCon): same self-refine execution as
      ``"sequential"`` but **all** trajectories are summarized together.

    Parameters
    ----------
    agent:
        Any ReActAgent with ``invoke()``, ``card``, and ``context_engine``.
        Does not need to be a ``ContextEvolvingReActAgent``.
    params:
        :class:`RunTrialsInput` dataclass specifying memory_service, user_id,
        question, ground_truth, matts_k, and matts_mode.

    Returns
    -------
    dict or None
        Summary dict from ``memory_service.summarize``, or ``None`` on error.

    Examples
    --------
    Parallel (ReasoningBank)::

        result = await run_trials(agent, RunTrialsInput(
            memory_service=svc, user_id="alice",
            question=question, ground_truth=answer,
            matts_k=3, matts_mode="parallel"))

    Sequential (ACE)::

        result = await run_trials(agent, RunTrialsInput(
            memory_service=svc, user_id="alice",
            question=question, ground_truth=answer,
            matts_k=3, matts_mode="sequential"))

    Combined (RefCon)::

        result = await run_trials(agent, RunTrialsInput(
            memory_service=svc, user_id="alice",
            question=question, ground_truth=answer,
            matts_k=3, matts_mode="combined"))
    """
    # if persist_type is set (standalone mode), load memories from persistence backend
    # if persist_type is not set (integrated mode), memories are already loaded by ContextEvolvingReActAgent
    persistence_helper = None
    algo_name = _ALGO_TO_NAME.get(
        getattr(params.memory_service, "summary_algorithm", ""), "ace"
    )
    if params.persist_type is not None:
        persistence_helper = MemoryPersistenceHelper(
            persist_type=params.persist_type,
            persist_path=params.persist_path,
            milvus_host=params.milvus_host,
            milvus_port=params.milvus_port,
            milvus_collection=params.milvus_collection,
        )
        try:
            data = persistence_helper.load(params.user_id, algo_name)

            if data:
                if hasattr(params.memory_service, "vector_store"):
                    count = 0
                    for node_id, node_data in data.items():
                        try:
                            node = VectorNode.from_dict(node_data)
                            if hasattr(params.memory_service.vector_store, "load_node"):
                                params.memory_service.vector_store.load_node(node_id, node)
                                count += 1
                        except Exception as node_exc:
                            logger.warning("Failed to load node %s: %s", node_id, node_exc)
                    logger.info(
                        "Loaded %d memories into vector store (algo=%s)", count, algo_name
                    )
                else:
                    logger.warning(
                        "Memory service does not expose vector_store, cannot load memories."
                    )
        except Exception as exc:
            logger.error("Failed to load existing memories: %s", exc)

    # "none" mode: exactly 1 trial, no self-refine, 1 trajectory summarized
    if params.matts_mode == "none":
        matts_k = 1
        self_refine = False
    else:
        matts_k = params.matts_k
        if matts_k is None:
            matts_k = int(memory_config.get("MATTS_DEFAULT_K", 3))
        self_refine = params.matts_mode in ("sequential", "combined")

    trial_outputs: List[TrialOutput] = await _run_trials_inner(
        agent, params.question, params.ground_truth, matts_k, self_refine
    )

    # Unpack per-trial results into aligned lists for summarization
    trajectories = [t.trajectory for t in trial_outputs]
    feedbacks = [t.feedback for t in trial_outputs]
    scores = [t.score if t.score is not None else 0 for t in trial_outputs]

    # add ground_truth, feedback and score if needed depending on the algorithm and dataset
    summary_result = await summarize_trajectories(
        params.memory_service,
        params.user_id,
        SummarizeTrajectoriesInput(
            query=params.question,
            trajectory=trajectories,
            matts_mode=params.matts_mode,
            ground_truth=params.ground_truth,
            feedback=feedbacks,
            score=scores,
        ),
    )

    # Persist updated memories back to the backend (standalone mode)
    if persistence_helper is not None and hasattr(params.memory_service, "vector_store"):
        try:
            all_nodes = params.memory_service.vector_store.get_all()
            if all_nodes:
                nodes_dict = {n.id: n.to_dict() for n in all_nodes}
                persistence_helper.save(params.user_id, algo_name, nodes_dict)
        except Exception as exc:
            logger.error("Failed to persist memories after run_trials: %s", exc)

    return summary_result


__all__ = [
    "SummarizeTrajectoriesInput",
    "RunTrialsInput",
    "TrialOutput",
    "format_trajectory",
    "evaluate_trial",
    "summarize_trajectories",
    "run_trials",
]
