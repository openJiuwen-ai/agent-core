---
id: B_20261119_2300_browser-steering-task-state
title: A steering message starts no new browser task
type: B
author: opPO333
author_email: 186512928+opPO333@users.noreply.github.com
modules:
  - openjiuwen/harness/tools/browser_move/playwright_runtime
files:
  - openjiuwen/harness/tools/browser_move/playwright_runtime/browser_working_context.py
  - tests/unit_tests/harness/tools/browser_move/test_browser_steering_task_state.py
designs: []
design_note: Corrects which user message names the browser task; no new state, field or protocol.
---

# A steering message starts no new browser task

Refs: #2832

## Symptoms and impact

A steering message pushed into a running browser task replaced the task. Two senders push one: a
host that adds a notice while the task runs, and the rail itself, when a model reply describes a
browser tool call without making it. The rail then built a new task state whose task text was the
steering message. Phases, known URLs, source observations, the model-protocol retry count and the
deadline start began again.

A run that had read its page could end with `no_task_observation`, because the new state held no
observation. The model-protocol recovery allows one retry per task; it got one retry per distinct steering
text.

## Related design and current contract

S_18 (F_17) keeps unknown journal effects when the current intent replaces old conditions. The
journal survived: `Session.update_state` merges a saved dict into the stored one
(`openjiuwen/core/session/state/base.py:125`, `openjiuwen/core/session/utils.py:198-203`), and the new
task state did not hold the journal key. The other fields of the task state were replaced. No spec
named how the rail picks the task text.

## Root cause and decision

`before_model_call` takes the task text from `latest_browser_user_request`. That function returned
the newest user message that is not ephemeral browser context. The agent loop writes a steering
message as a user message with source kind `steering`. That message was then the newest request.
`_ensure_task_state` compares the text with the stored task and builds a new state when they
differ.

The function now skips a message whose source-kind metadata is `steering`, the metadata the agent
loop sets on the message. A new user request still starts a new task state.

## Regression evidence and scope

`test_browser_steering_task_state.py` has five tests with eight cases. The lookup skips a host
notice and the markup retry. A request that the agent loop admits as `query` or `resume` is the
task. After a steering message `before_model_call` keeps the task id, the evidence, the phases, the
known URLs, the model-protocol retry count and a deadline that has not passed. A reply with
tool-call markup, the retry, the next `before_model_call` and a second such reply end the task as
`model_tool_protocol_error` with its task state kept. Guard that passes on the base: a new user
request starts a new task state. Offline unit tests only.

## Patterns and synced documents

S_18 states the rule. The English and Chinese browser agent pages say that a steering message
continues the current task.

## Open items

1. With the state kept, the model-protocol limit of one retry holds per task. A second such reply
   in the same task ends it as `model_tool_protocol_error`, `partial` when the task has evidence. In
   recorded runs such a second reply was a complete final report with its completion marker written
   as markup text (#2836).
2. The iteration-limit notice is a user message with source kind `iteration_limit`
   (`openjiuwen/core/single_agent/agents/react_agent.py:3599-3607`). When `on_max_iterations` is
   `final_answer`, core adds it before the last model call, and `latest_browser_user_request` still
   takes it as the task. This change leaves that case as it is.
