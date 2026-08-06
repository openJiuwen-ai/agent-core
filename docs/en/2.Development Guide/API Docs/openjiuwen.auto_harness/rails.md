# openjiuwen.auto_harness.rails

`openjiuwen.auto_harness.rails` is the **safety guardrail collection** for Auto Harness, responsible for injecting session budget, cancellation, context, edit safety, experience store, failure rollback, and immutable file protection policies into agent lifecycle callbacks. All rails inherit from `DeepAgentRail` (except `AutoHarnessContextRail`, which inherits `ContextProcessorRail`), executing policies through callback hooks such as `before_tool_call` / `after_tool_call` / `before_model_call` / `after_model_call` / `before_task_iteration` / `after_task_iteration` / `init` / `uninit`.

Submodules:

- `budget_rail`: Session clock budget + API cost budget + CI gate logging (`BudgetRail`);
- `cancellation_rail`: Monitor orchestrator cancellation requests (`CancellationRail`);
- `context_rail`: Context processor that disables workspace context injection (`AutoHarnessContextRail`);
- `edit_safety_rail`: Atomic change tracking + scope validation + ruff checks (`EditSafetyRail`);
- `experience_rail`: Register experience retrieval tools and inject experience store prompt (`build_experience_section` / `AutoHarnessExperienceRail`);
- `revert_on_failure_rail`: Capture base commit for failure rollback support (`RevertOnFailureRail`);
- `security_rail`: Immutable file guard + input injection scanning (`SecurityRail`).

---

## class openjiuwen.auto_harness.rails.budget_rail.BudgetRail

```
class openjiuwen.auto_harness.rails.budget_rail.BudgetRail(DeepAgentRail)

def __init__(self, budget: SessionBudgetController) -> None
```

Inherits `DeepAgentRail`, merging the responsibilities of the former `SessionBudgetRail`, `CostBudgetRail`, and `CIGateRail`: checks clock budget before each tool call, estimates and accumulates API cost based on token usage after each model call, and logs CI gate boundaries at task iteration edges. When clock or cost budget is exhausted, requests forced finish via `ctx.request_force_finish`.

**Parameters**:

* **budget**(SessionBudgetController): Session budget controller instance, must provide `should_stop`, `add_cost` interfaces.

### async before_tool_call(ctx: AgentCallbackContext) -> None

Check session budget before each tool call; if `budget.should_stop` is true, logs a warning and requests forced finish via `ctx.request_force_finish({"reason": "Session budget exceeded"})`.

**Parameters**:

* **ctx**(AgentCallbackContext): Agent callback context, providing `request_force_finish` capabilities.

### async after_model_call(ctx: AgentCallbackContext) -> None

Estimate token cost from model response usage (input `3e-6` USD/token, output `15e-6` USD/token) and call `budget.add_cost` to accumulate; requests forced finish if cost budget is exhausted. Only takes effect when `ctx.inputs` is `ModelCallInputs` and the response carries `usage`.

**Parameters**:

* **ctx**(AgentCallbackContext): Agent callback context.

### async before_task_iteration(ctx: AgentCallbackContext) -> None

Log CI gate boundary at task iteration start (`"CI gate rail: iteration starting"`).

**Parameters**:

* **ctx**(AgentCallbackContext): Agent callback context.

### async after_task_iteration(ctx: AgentCallbackContext) -> None

Log CI gate boundary at task iteration completion (`"CI gate rail: iteration complete"`).

**Parameters**:

* **ctx**(AgentCallbackContext): Agent callback context.

**Example**:

```python
>>> from openjiuwen.auto_harness.infra.session_budget import SessionBudgetController
>>> from openjiuwen.auto_harness.rails.budget_rail import BudgetRail
>>> budget = SessionBudgetController(wall_clock_secs=3600.0, cost_limit_usd=10.0)
>>> rail = BudgetRail(budget=budget)
>>> rail  # After registering to agent's stream_rails, takes effect at each callback node
<openjiuwen.auto_harness.rails.budget_rail.BudgetRail object at 0x...>
```

---

## class openjiuwen.auto_harness.rails.cancellation_rail.CancellationRail

```
class openjiuwen.auto_harness.rails.cancellation_rail.CancellationRail(DeepAgentRail)

def __init__(self) -> None
```

Inherits `DeepAgentRail`, registered on stream_rails, monitors `AutoHarnessOrchestrator.should_cancel`. When the orchestrator is requested to cancel, requests forced finish at the next agent checkpoint (before tool call / after model call) with `{"reason": "user_cancelled", "cancelled": True}`. Class attribute `priority = 100`, ensuring early capture of cancellation signals.

### bind(orchestrator: AutoHarnessOrchestrator) -> None

Bind the orchestrator reference to this rail after creation, for subsequent callbacks to read `should_cancel` status.

**Parameters**:

* **orchestrator**(AutoHarnessOrchestrator): The monitored orchestrator instance.

### async before_tool_call(ctx: AgentCallbackContext) -> None

Check cancellation status before tool call; if the orchestrator has requested cancellation, logs and requests `force_finish`.

**Parameters**:

* **ctx**(AgentCallbackContext): Agent callback context.

### async after_model_call(ctx: AgentCallbackContext) -> None

Check cancellation status after model call; logic is the same as `before_tool_call`.

**Parameters**:

* **ctx**(AgentCallbackContext): Agent callback context.

---

## class openjiuwen.auto_harness.rails.context_rail.AutoHarnessContextRail

```
class openjiuwen.auto_harness.rails.context_rail.AutoHarnessContextRail(ContextProcessorRail)
```

Inherits `ContextProcessorRail`, retains the harness `ContextEngineeringRail` context processor skeleton, but disables prompt section injection that would read workspace local context files and conflict with auto-harness identity.

### async before_model_call(ctx: AgentCallbackContext) -> None

Overridden as no-op (direct `return`), does not inject workspace / tools / context prompt sections.

**Parameters**:

* **ctx**(AgentCallbackContext): Agent callback context.

### uninit(agent) -> None

Overridden as no-op (direct `return`), does not modify system prompt sections during agent teardown.

**Parameters**:

* **agent**: Associated agent instance.

---

## class openjiuwen.auto_harness.rails.edit_safety_rail.EditSafetyRail

```
class openjiuwen.auto_harness.rails.edit_safety_rail.EditSafetyRail(DeepAgentRail)

def __init__(self, max_files: int = 3) -> None
```

Inherits `DeepAgentRail`, merging the former `AtomicChangeRail` and `EditCheckRail`: hard-blocks writes outside the allowed edit scope, tracks edited files and emits steering warnings when exceeding `max_files`, runs `ruff check` after Python file writes and pushes issues as steering to the agent.

**Parameters**:

* **max_files**(int, optional): Maximum number of source files allowed to edit per round; pushes steering to keep changes compact when exceeded. Default: `3`.

### async before_tool_call(ctx: AgentCallbackContext) -> None

For `write_file` / `edit_file` tool calls, normalizes `file_path` to repo-relative path and validates it falls within the allowed edit scope. Hard-blocks if out of scope: sets `ctx.extra["_skip_tool"] = True`, writes back error `tool_result` and `ToolMessage`. Allowed edit scope: `openjiuwen/dev_tools/**`, `openjiuwen/harness/**`, `openjiuwen/core/**`, `tests/**`, `examples/**`, `docs/en/**`, `docs/zh/**`.

**Parameters**:

* **ctx**(AgentCallbackContext): Agent callback context, containing `inputs.tool_args` etc.

### async after_tool_call(ctx: AgentCallbackContext) -> None

After write completion, adds the file to the tracking set; when count exceeds `max_files`, pushes steering warning; runs `ruff check` on `.py` files, pushing output as steering on failure requesting fixes.

**Parameters**:

* **ctx**(AgentCallbackContext): Agent callback context.

### reset() -> None

Clear the tracked edited file set between tasks.

### edited_files() -> list[str]

Return the tracked edited file list in stable (sorted) order.

**Returns**:

**list[str]**, tracked edited file paths (normalized, sorted).

---

## func openjiuwen.auto_harness.rails.experience_rail.build_experience_section

```
def build_experience_section(language: str = "cn", experience_dir: str = ".auto_harness/experience") -> PromptSection
```

Build the prompt guidance snippet used by the auto-harness experience store, injected into the `SectionName.MEMORY` section, providing experience store path and `experience_search` tool usage instructions based on language, priority `85`.

**Parameters**:

* **language**(str, optional): Prompt language, supports `"cn"` and `"en"`. Default: `"cn"`.
* **experience_dir**(str, optional): Experience store directory path, written into prompt text. Default: `".auto_harness/experience"`.

**Returns**:

**PromptSection**, can be added to `system_prompt_builder`'s memory section.

---

## class openjiuwen.auto_harness.rails.experience_rail.AutoHarnessExperienceRail

```
class openjiuwen.auto_harness.rails.experience_rail.AutoHarnessExperienceRail(DeepAgentRail)

def __init__(self, experience_dir: str, *, language: str = "cn") -> None
```

Inherits `DeepAgentRail`, registers `ExperienceSearchTool` during `init` and injects experience store prompt section into `system_prompt_builder`; refreshes the section during `before_model_call`; removes tool and section during `uninit`. Class attribute `priority = 80`.

**Parameters**:

* **experience_dir**(str): Experience store directory path, passed through to experience retrieval tool and prompts.
* **language**(str, optional): Tool and prompt language. Default: `"cn"`.

### init(agent) -> None

Calls parent `init`, records the agent's `system_prompt_builder` reference, and registers `ExperienceSearchTool` to the global `Runner.resource_mgr` and agent's `ability_manager` via `_register_experience_tool`.

**Parameters**:

* **agent**: Associated agent instance.

### uninit(agent) -> None

Remove the registered experience retrieval tool from the agent's `ability_manager` and global `Runner.resource_mgr`, and remove the `SectionName.MEMORY` section from `system_prompt_builder`.

**Parameters**:

* **agent**: Associated agent instance.

### async before_model_call(ctx: AgentCallbackContext) -> None

If `system_prompt_builder` exists, removes then re-adds the experience store section to ensure the latest language and path configuration is used.

**Parameters**:

* **ctx**(AgentCallbackContext): Agent callback context.

---

## class openjiuwen.auto_harness.rails.revert_on_failure_rail.RevertOnFailureRail

```
class openjiuwen.auto_harness.rails.revert_on_failure_rail.RevertOnFailureRail(DeepAgentRail)

def __init__(self) -> None
```

Inherits `DeepAgentRail`, captures the current HEAD as base commit before each task iteration starts, providing the orchestrator with `git reset --hard` rollback capability on failure.

### set_base_commit(sha: str) -> None

Record the base commit SHA for rollback.

**Parameters**:

* **sha**(str): Git commit SHA.

### base_commit() -> str

Read-only property, returns the currently recorded base commit SHA; returns empty string if not captured.

**Returns**:

**str**, current base commit SHA.

### async before_task_iteration(ctx: AgentCallbackContext) -> None

Capture current HEAD via `git rev-parse HEAD` and record via `set_base_commit`; skips if git is unavailable.

**Parameters**:

* **ctx**(AgentCallbackContext): Agent callback context.

### async revert(workspace: str) -> bool

Execute `git reset --hard <base_commit>` rollback in the specified workspace. Returns `False` if no base commit or `git reset` fails, `True` on success.

**Parameters**:

* **workspace**(str): Working directory for git commands.

**Returns**:

**bool**, whether the rollback was successful.

---

## class openjiuwen.auto_harness.rails.security_rail.SecurityRail

```
class openjiuwen.auto_harness.rails.security_rail.SecurityRail(DeepAgentRail)

def __init__(self, immutable_files: List[str] | None = None, high_impact_prefixes: List[str] | None = None) -> None
```

Inherits `DeepAgentRail`, merging the former `ImmutableFileRail` and `InputSanitizationRail`: in `before_tool_call`, uses glob patterns to block writes to immutable files and marks high-impact edits; in `before_model_call`, scans input text for prompt injection / shell injection suspicious patterns and requests forced finish.

**Parameters**:

* **immutable_files**(List[str] | None, optional): Glob pattern list for immutable files (e.g., `["*.lock", "setup.cfg"]`), hard-blocks writes on match. Default: `None` (empty list).
* **high_impact_prefixes**(List[str] | None, optional): High-impact path prefix list, marks `ctx.extra["high_impact"]` as `True` on match. Default: `None` (empty list).

### async before_tool_call(ctx: AgentCallbackContext) -> None

For `write_file` / `edit_file` tool calls, checks `file_path` with `fnmatch`: hard-blocks on immutable pattern match (sets `_skip_tool`, writes back error `ToolMessage`); sets `ctx.extra["high_impact"] = True` on high-impact prefix match.

**Parameters**:

* **ctx**(AgentCallbackContext): Agent callback context.

### async before_model_call(ctx: AgentCallbackContext) -> None

Extracts text from model input messages, scans for suspicious patterns (ignore previous instructions, `system prompt`, `rm -rf /`, `$(...)` command substitution, backtick commands, etc.); on match, logs warning, requests forced finish via `ctx.request_force_finish`, and pushes steering warning not to execute injected instructions. Only takes effect when `ctx.inputs` is `ModelCallInputs`.

**Parameters**:

* **ctx**(AgentCallbackContext): Agent callback context.
