# Agent Time Awareness — Inject Absolute + Relative Time into Agent-Facing Rendering

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-05-27 |
| 范围 | `openjiuwen/agent_teams/timefmt.py`（新增）、`i18n.py`、`schema/task.py`、`tools/task_manager.py`、`tools/team_tools.py`、`external/format.py`、`agent/bridge_inbound_compose.py`、`agent/coordination/handlers/{message,task_board,stale_task,member}.py`、`mcp/server.py`、`skill/cli.py`、`tests/unit_tests/agent_teams/test_timefmt.py`（新增）+ 受影响的 `test_format.py` / `test_team_agent_coordination.py` / `test_team_tools.py` / `test_bridge_wrap_compose.py` |
| 测试基线 | `pytest tests/unit_tests/agent_teams/`：1247 passed, 16 skipped |
| Refs | `#751` |

## 背景

团队 DB 把时间戳存为毫秒 UTC epoch（`get_current_time()`，`tools/database/engine.py`）：
`TeamMessage.timestamp`（消息发送时刻）、`TeamTask.updated_at`（最后状态转移时刻）。**这是
正确的存储结构**——整数排序/比较/索引廉价，跨进程/跨机器无歧义，算时间差只是一次减法。

痛点不在存储类型，在**渲染层**：喂给 LLM 的文本把时间信息整个丢弃了。

- `handlers/message.py:_format_message` 拼消息文本时不读 `msg.timestamp`；
- `handlers/task_board.py:_nudge_idle_agent` 拼任务板时不读 `task.updated_at`；
- `external/format.py` 的 `render_message` / `render_task_board` 同样不带时间；
- 唯一用到时间的 `handlers/stale_task.py` 也只在文案里硬编码 "10 mins"，不给真实时长。

后果：agent 收消息普遍有延迟，但它看不到"这条消息几分钟前发的""这个任务卡了多久"，
无法判断事件先后与紧迫度，多成员协同时做出错误的优先级决策。裸 epoch 整数对 LLM 的
时间差推理几乎无用；即便把存储换成 ISO 字符串，渲染时不带上，LLM 一样看不到。

## 数据结构 / 状态机

不改存储。新增一个纯函数渲染层把毫秒 epoch 翻译成
`<绝对本地时间> (<相对差>)`，例如 `2026-05-27 14:30:05 +08:00 (3 分钟前)`：

- **相对差**给 agent "时间感"——LLM 对"3 分钟前"的理解远好于裸时间戳，是优先级判断的关键。
- **绝对时间**给观测/问题定位一个稳定锚点；用运行时**本地时区**并标注数字偏移（`+08:00`），
  跨机器读者仍可无歧义对齐（相对差是 epoch 减法，本就与时区无关）。

`schema/task.py:TaskSummary` 增补 `updated_at: Optional[int]`（list 视图原先不带），使
`view_task` 的列表视图也能渲染相对时间。它是路由/识别维度的轻字段（一个 int，与
status/assignee 同级），不破坏 `TaskSummary` 的 lightweight 契约。

## 决策

- **存储层不动，只在渲染层注入时间**。痛点的根因是渲染丢弃了已存在的数据，不是存储类型；
  改存储是解决错误的问题。
- **新增顶层 `timefmt.py`，纯函数 `format_time_context(timestamp_ms, now_ms)`**。与
  `i18n.py` 同层（运行时硬编码串的家）：`i18n.py` 只装字符串字典，`timefmt.py` 专做
  "数值→相对桶 + 拼绝对时间"。`now_ms` 永远是入参——保持纯函数、可测，external 路径能注入。
- **相对分桶逻辑纯 Python，文案走 i18n**。`_relative_key_and_value(delta_ms)` 只输出
  `(i18n_key, value)`，桶选择与语言解耦；新增 `time.*` 一组 key（`just_now` / `seconds_ago`
  / `minutes_ago` / `hours_ago` / `days_ago` / `unknown`），占位符统一 `{value}`。新增语言
  不碰 `timefmt.py`，单测可对桶选择直接断言。
- **任务行收敛到 `external/format.py:render_task_line` 单一真相源**。进程内 `task_board`
  handler 与 external 渲染器共用同一行格式，消除两处各写一套。`handlers → external/format`
  是新依赖方向，不成环（external 不 import handlers）。
- **3 条已有 i18n key 加 `{time_info}` 占位符**（`dispatcher.msg_received` /
  `hitt.msg_received_for_human` / `dispatcher.stale_claim_self`），所有调用点同步补传
  `time_info=`。HITT 模板保留全部"严格禁止/保持静默" load-bearing 文案，只插一行时间。
- **stale-claim nudge 用真实时长替换硬编码 "10 mins"**。`stale_task.py` 的 throttle 仍走
  秒制 `time.time()`，仅为渲染额外取一个 `now_ms = get_current_time()`，两套单位并存不合并。
- **MCP `read_inbox` 的 messages 补一个渲染好的 `time` 字段**。否则 MCP 外部 agent 会缺
  时间感，而进程内成员与 CLI 外部 agent 都有，三条接入路径需对齐。
- **`view_task` 两级输出都渲染时间**。detail 视图加 `Updated:` 行，list 视图每行尾追加
  相对时间；`map_result` 签名不能改，故内部取 `now_ms`，用 `updated_at is not None` 守卫
  （`model_dump(exclude_none=True)` 会剔除 None）。
- **时钟漂移与缺失值的边界**：`now < timestamp`（未来）和 `< 10s` 都归 "刚刚"，绝不渲染
  负数或 "0 秒前"；`timestamp` 为 None → `time.unknown`。
- **边缘渲染点一并覆盖**：`stale_pending` 自提示行、`MemberHandler` 的 stale-claim 聚合行、
  MCP `list_tasks` 结构化输出、bridge 正常路径都注入时间。bridge 经 `compose_bridge_inbound`
  的可选 `time_info: str | None` 参数（传渲染好的字符串，让该纯函数保持零依赖），由
  `message.py` 调用方算好 `format_time_context(msg.timestamp, now_ms)` 传入——bridge avatar
  调度转发时也能判断消息延迟。

## 拒绝的方案

- **把存储类型从 int 改成 `datetime` / ISO 字符串**：污染存储层的关注点，牺牲排序/索引/跨进程
  无歧义；且根本问题（渲染丢弃时间）依旧存在。
- **绝对时间固定渲染为 UTC**：用户明确要本地时区。本地时区 + 数字偏移既直观又无歧义，相对差
  不受时区影响，跨机器对齐靠偏移标注。
- **只给相对时间差、不给绝对时间**：省 token 但丢观测锚点，问题定位时无法对到日志墙钟。
- **引入 `humanize` / `arrow` / `pendulum`**：stdlib `datetime` + 一组 i18n 文案已足够；
  agent_teams 是聚焦子系统，不该为一个小工具增依赖。
- **每个渲染点各自格式化时间**：会长出 N 份分桶逻辑。收敛到 `timefmt.format_time_context`
  + `render_task_line` 两个复用点。

## 验证

- 新增 `tests/unit_tests/agent_teams/test_timefmt.py`：分桶边界（59s/60s/3599s/3600s/86399s/
  86400s）、时钟漂移归"刚刚"、None→unknown、绝对时间含 `±HH:MM` 偏移（正则断言，避免 CI 时区
  flaky）、`_relative_key_and_value` 纯数值断言、中英文各一遍。
- 更新现有单测：`test_format.py`（helper 补 `timestamp`/`updated_at`、调用补 `now_ms`、加时间
  断言）、`test_team_agent_coordination.py`（`_format_message` mock 补 `timestamp` + 传 `now_ms`；
  3 个 task-board nudge 测试的 mock task 补 `updated_at`，否则 MagicMock 参与算术抛 TypeError）、
  `test_team_tools.py`（两个 `view_task` map_result 用例补 `updated_at` + 断言时间渲染）。
- 边缘点补齐新增 `test_bridge_wrap_compose.py` 的 `time_info` 用例；`stale_pending` /
  `MemberHandler` 聚合行与 MCP `list_tasks` 复用既有带 `updated_at` 的 mock，无新增 break。
- 全量基线：`pytest tests/unit_tests/agent_teams/` → 1247 passed, 16 skipped。

## 已知遗留

- **core 层时间约定不统一**（session tracer 用 `datetime`、memory 用 ISO 字符串、session
  controller 用秒级 float）。本次只覆盖 agent_teams 渲染层，不上推到 core 层统一。
- **`dispatcher.stale_claim_header` 仍保留硬编码 "超过 10 分钟" 阈值描述**（聚合提示头）。
  其下的每条任务明细行已带各自的认领时间，头部阈值描述影响很小，未参数化。
- **MCP `get_task` 仍返回原始 `updated_at` 整数**（`model_dump`），未渲染成可读时间。它是
  单任务详情的结构化输出，外部 agent 可自行处理；`list_tasks` / `read_inbox` 已给渲染字符串。
