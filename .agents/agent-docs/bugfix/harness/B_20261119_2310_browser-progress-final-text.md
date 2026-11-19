---
id: B_20261119_2310_browser-progress-final-text
title: A browser_progress tool call ends the task with the text of its model response
type: B
author: opPO333
author_email: 186512928+opPO333@users.noreply.github.com
modules:
  - openjiuwen/harness/tools/browser_move/playwright_runtime
files:
  - openjiuwen/harness/tools/browser_move/playwright_runtime/runtime.py
  - tests/unit_tests/harness/tools/browser_move/test_browser_progress_tool_final_text.py
designs: []
design_note: Gives the existing progress-tool path the final text the other worker-report paths already pass; no new field or terminal reason.
---

# A browser_progress tool call ends the task with the text of its model response

Refs: #2829

## Symptoms and impact

A worker can end its task with a `browser_progress` tool call in place of a progress block. The
model writes its report as the text of the same response and puts the status in the call. The
tool hook applied that status with an empty final text. A task the model reported `completed` this
way ended `partial` with `model_output_empty` unless the state held typed evidence. The report it
wrote reached neither the final text nor the summary for the parent.

## Related design and current contract

S_05 (F_28, item 2): "A normal model answer requires non-empty worker output; the legacy explicit
progress-tool path can return actual extracted values, never passive page metadata." S_18 lists
`browser_progress` text with partial and next_action. The progress block and the plain answer
already pass the worker's text.

## Root cause and decision

`_handle_progress_tool_alias` called `_apply_worker_progress_to_task_state(session, payload, "")`.
The hook runs in `before_tool_call`, after the response that made the call has passed
`after_model_call`.

`after_model_call` now keeps the text of a response whose only tool call is `browser_progress`,
with the id of that call, in the callback extra of the invoke. The text has its `<browser_progress>`
block taken out, as the plain-answer path does. The hook passes that text as the final text when the
id is the id of its call, and an empty text otherwise. No text is kept for a response cut at the
output limit (`length`, `max_tokens`), or for a response with other tool calls, whose text precedes
calls that still run. The extra lives for one invoke.

After the hook ends the task, core ends the invoke with the forced result as its answer, and
`after_invoke` passes that result JSON to `_apply_worker_progress_to_task_state` once more. That call
leaves `last_worker_final` of a task that already ended as it is. The parent's summary is then the
worker's text.

Unchanged: a response without text completes only on typed evidence and otherwise ends as
`model_output_empty`; page metadata answers nothing. A final text that announces a tool call, such as
"I'll now call browser_progress", matches the unfinished-tool-intent pattern and ends the task as
`model_tool_protocol_error`, as such text does on the other report paths.

## Regression evidence and scope

`test_browser_progress_tool_final_text.py` has six tests:

- The text of the response that calls `browser_progress` is the final text, the task completes, and
  after `after_invoke` the summary for the parent is that text.
- A final text that announces a tool call ends as `model_tool_protocol_error`.
- Guards that pass on the base: the text of an earlier response, a text that holds only the progress
  block, the text of a response cut at the output limit, and the text of a response with other tool
  calls are no final text; each task ends `partial`.

Offline unit tests only.

## Patterns and synced documents

S_05 states the rule. The English and Chinese browser agent pages say which text the tool path
passes.

## Open items

None.
