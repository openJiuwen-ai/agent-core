# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from openjiuwen.core.context_engine.context.session_memory_manager import (
    SessionMemoryConfig,
    SessionMemoryManager,
    SessionMemoryUpdateAgent,
)
from openjiuwen.core.context_engine.qa_artifact.catalog import CatalogBuilder
from openjiuwen.core.context_engine.qa_artifact.lifecycle import (
    cancel_qa_artifact_tasks_for_session,
    register_qa_artifact_manager,
)
from openjiuwen.core.context_engine.qa_artifact.manager import QAArtifactManager
from openjiuwen.core.context_engine.qa_artifact.overview import QAOverviewGenerator
from openjiuwen.core.context_engine.qa_artifact.schema import (
    IRREDUCIBLE_CONTEXT_USER_MESSAGE_ZH,
    QA_MEMORY_STATE_KEY,
    CatalogEntry,
    IrreducibleContextError,
    QAArtifactConfig,
    QAArtifacts,
    QAArtifactState,
    validate_qa_artifact_thresholds,
)
from openjiuwen.core.context_engine.qa_artifact.store import QAArtifactStore
from openjiuwen.core.context_engine.qa_artifact.tools import LOAD_QA_INDEX_TOOL_NAME, LoadQaIndexTool
from openjiuwen.core.context_engine.qa_ref import QARef

__all__ = [
    "CatalogBuilder",
    "LOAD_QA_INDEX_TOOL_NAME",
    "LoadQaIndexTool",
    "QA_MEMORY_STATE_KEY",
    "QAArtifactConfig",
    "QAArtifactManager",
    "QAArtifactState",
    "QAArtifactStore",
    "QAArtifacts",
    "CatalogEntry",
    "IRREDUCIBLE_CONTEXT_USER_MESSAGE_ZH",
    "IrreducibleContextError",
    "QAOverviewGenerator",
    "QARef",
    "SessionMemoryConfig",
    "SessionMemoryManager",
    "SessionMemoryUpdateAgent",
    "build_qa_artifact_manager",
    "cancel_qa_artifact_tasks_for_session",
    "register_qa_artifact_manager",
    "validate_qa_artifact_thresholds",
]


def build_qa_artifact_manager(
    qa_config: QAArtifactConfig,
    session_memory_config: SessionMemoryConfig | None = None,
) -> QAArtifactManager:
    sm_config = session_memory_config or SessionMemoryConfig()
    session_memory_manager = SessionMemoryManager(sm_config)
    overview = QAOverviewGenerator(session_memory_manager)
    catalog = CatalogBuilder(qa_config, session_memory_config=sm_config)
    mgr = QAArtifactManager(qa_config, overview, catalog)
    register_qa_artifact_manager(mgr)
    return mgr
