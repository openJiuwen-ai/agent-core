# openjiuwen.dev_tools.agent_builder.utils

`openjiuwen.dev_tools.agent_builder.utils` provides **enums, progress tracking, and helpers**:

- Enums `AgentType`, `BuildState`, `ProgressStage`, `ProgressStatus`;
- `ProgressReporter`, `ProgressManager`, and global `progress_manager`;
- Utilities: `extract_json_from_text`, `format_dialog_history`, `safe_json_loads`, `validate_session_id`, `merge_dict_lists`, `deep_merge_dict`, `load_json_file`.

**Exports**: see `__all__` in `utils/__init__.py`.

---

## class openjiuwen.dev_tools.agent_builder.utils.enums.AgentType

String enum:

* **LLM_AGENT**: `"llm_agent"`.
* **WORKFLOW**: `"workflow"`.

---

## class openjiuwen.dev_tools.agent_builder.utils.enums.BuildState

* **INITIAL**, **PROCESSING**, **COMPLETED**: build states.

---

## class openjiuwen.dev_tools.agent_builder.utils.enums.ProgressStage

Build stages (shared and workflow-specific), e.g. `INITIALIZING`, `CLARIFYING`, `GENERATING_CONFIG`, `DETECTING_INTENTION`, `GENERATING_WORKFLOW_DESIGN`, etc.

---

## class openjiuwen.dev_tools.agent_builder.utils.enums.ProgressStatus

* **PENDING**, **RUNNING**, **SUCCESS**, **FAILED**, **WARNING**.

---

## AgentTypeLiteral

`Literal["llm_agent", "workflow"]` for typing.

---

## class openjiuwen.dev_tools.agent_builder.utils.progress.ProgressStep

Dataclass for one progress step (`stage`, `status`, `message`, `details`, `timestamp`, `duration`, `error`).

### to_dict() -> Dict[str, Any]

JSON-friendly dict.

---

## class openjiuwen.dev_tools.agent_builder.utils.progress.BuildProgress

Dataclass for overall session progress (`session_id`, `agent_type`, `current_stage`, `steps`, `overall_progress`, etc.).

### to_dict() -> Dict[str, Any]

Serializes full progress.

---

## class openjiuwen.dev_tools.agent_builder.utils.progress.ProgressReporter

Progress reporter with optional callbacks.

**Parameters**:

* **session_id**(str): Session id.
* **agent_type**(str): `'llm_agent'` or `'workflow'`.

### add_callback / remove_callback

Register or remove `Callable[[BuildProgress], None]`.

### start_stage / update_stage / complete_stage / fail_stage / warn_stage

Advance stages, update text, or mark failure/warning.

### get_progress() -> BuildProgress

Current `BuildProgress` instance.

### complete(message: str = "Build completed") -> None

Marks the build complete (100%).

---

## class openjiuwen.dev_tools.agent_builder.utils.progress.ProgressManager

Manages per-session `ProgressReporter` instances.

### create_reporter(session_id: str, agent_type: str) -> ProgressReporter

Creates or returns an existing reporter.

### get_reporter / remove_reporter / get_progress

Query or remove by session.

---

## progress_manager

Global **ProgressManager** singleton used by the executor and [AgentBuilder.get_progress](agent_builder.md).

---

## func openjiuwen.dev_tools.agent_builder.utils.utils.extract_json_from_text

Extracts the first Markdown JSON code block; otherwise returns the original text.

---

## func openjiuwen.dev_tools.agent_builder.utils.utils.format_dialog_history

Formats a list of `{"role","content"}` dicts into a multi-line string.

---

## func openjiuwen.dev_tools.agent_builder.utils.utils.safe_json_loads

Parses JSON safely; returns `default` on failure.

---

## func openjiuwen.dev_tools.agent_builder.utils.utils.validate_session_id

Validates session id contains only letters, digits, underscore, and hyphen.

---

## func openjiuwen.dev_tools.agent_builder.utils.utils.merge_dict_lists

Merges dict lists by a unique key and deduplicates.

---

## func openjiuwen.dev_tools.agent_builder.utils.utils.deep_merge_dict

Deep-merges dicts (see source for whether `base` is mutated).

---

## func openjiuwen.dev_tools.agent_builder.utils.utils.load_json_file

Loads a JSON file as a dict; raises on missing file or parse errors (see `ValidationError` in source).
