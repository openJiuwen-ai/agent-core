# 禁止覆盖全员的组播，强制改用广播

## 元信息
| 项 | 值 |
|---|---|
| 日期 | 2026-05-15 |
| 范围 | openjiuwen/agent_teams/tools/team_tools.py、tools/locales/descs/{cn,en}/send_message.md、tests/unit_tests/agent_teams/test_team_tools.py |
| 测试基线 | `pytest tests/unit_tests/agent_teams/test_team_tools.py` → 88 passed, 14 skipped；其中 `-k SendMessage` 21 passed |
| Refs | #751 |

## 背景

`send_message` 工具有三种投递方式：点对点（`to` 为成员名）、组播（`to` 为成员名数组）、
广播（`to` 为 `"*"`）。组播按接收人数线性产生独立消息行与事件，**同样人数下比广播更贵**——
`send_message.md` 描述文件自己已写明这一点。

但此前没有任何机制阻止 LLM 用组播把消息发给"团队全体其他成员"。这正是广播的语义，却付出了
组播 N 倍的写库 + 发事件成本。描述文件只是"建议谨慎"，没有硬约束，LLM 仍会犯。需要从两端收紧：
描述文件写成硬契约，工具实现当场拒绝。

## 数据结构 / 状态机

不涉及新数据结构。复用 `TeamBackend.list_members()`（`tools/team.py:720`）——它返回
`List[TeamMember]` 且**已排除调用者自己**（`member.member_name != self.member_name`），
不按状态过滤，包含 leader / human_agent。正好对应"团队全体成员（排除自己）"这一集合。

`SendMessageTool._multicast` 已先做 strip / 去空白 / 去重得到 `deduped`，并在投递循环前
依次拒绝空列表、`"*"`、`"user"`。新检查插在这三个守卫之后、`_auto_start_members()` 之前：

```python
if self._team:
    roster = {member.member_name for member in await self._team.list_members()}
    if roster and set(deduped) == roster:
        return ToolOutput(success=False, error="...use to='*' to broadcast instead...")
```

## 决策

### 1. 用集合精确相等 `==`，不用超集 `>=`

`set(deduped) == roster` 命中才拒绝。目标是真子集时（含因 ghost 成员导致集合不等的情况）
照常走投递循环，ghost 仍由循环内 `get_member` 归入 `failed`，不会误触发。这也匹配用户需求
原话"成员列表与团队全体成员相同时"。

若用 `>=`（超集也拒），"全员 + 一个 ghost" 会得到"改用广播"的错误——但广播同样到不了 ghost，
错误是误导性的。`==` 把"覆盖全员"和"含未知成员"两类问题清晰分开：前者要求改广播，后者由现有
partial-failure 反馈纠正。

### 2. 复用 `if self._team:` 守卫，`_team` 为 None 时优雅跳过

`_multicast` 现有的逐个 `get_member` 校验本就在 `if self._team:` 下——新检查复用同一守卫。
`_team` 未注入时（部分单测直接构造 `SendMessageTool` 不传 `team`）静默跳过，best-effort，
与现有成员校验行为一致，不为缺省路径引入新分支语义。

### 3. 规则对团队规模一视同仁，不为"只有一个其他成员"开特例

团队只有一个其他成员时，组播该成员 = 覆盖全员 → 同样拒绝。理由：广播到一人也比组播便宜，
语义一致；为团队规模开特例就是"好代码没有特殊情况"反面。`roster` 为空（调用者是唯一成员）时
`set(deduped)`（已被空列表守卫保证非空）不会等于空集，自然不触发，无需额外判空分支——
`if roster` 只是把这层意图写明，不是必需逻辑。

### 4. 拒绝时返回裸 `ToolOutput(success=False, error=...)`，不带 `data`

与现有 `*` / `user` / 空列表三个拒绝分支完全一致。`map_result` 对 `data is None` 的失败分支
直接回显 `error` 文本（`isinstance(d, dict)` 为假，不会走 multicast 格式化），无需任何额外处理。
错误文案用英文——`_multicast` 内现有错误串全是英文。

### 5. 描述文件双语同步改成硬契约

`descs/cn/send_message.md` 与 `descs/en/send_message.md` 的组播行，在"谨慎选择"之后、
"不能与 `*`/`user` 混用"之前，加入一句加粗硬约束："接收人覆盖全体其他成员时禁止组播、
必须改用广播 `*`，工具会直接拒绝。" 描述是行为契约，工具实现是兜底——两者都改才闭环。

## 拒绝的方案

- **只改描述文件、不改工具实现**：描述里早就有"组播比广播贵、谨慎使用"的软建议，LLM 照犯。
  行为契约需要工具层硬兜底，否则只是又一句会被忽略的提示。
- **在工具层把"覆盖全员的组播"自动降级为广播**：静默改写调用者意图，违反"工具不替 LLM 做决策"——
  且 `test_invoke_multicast_single_element_does_not_degrade` 已确立"组播不自动降级"的既有契约。
  返回错误让 LLM 自己改用 `"*"`，保持决策可见。
- **超集 `>=` 判定**：见决策 1，会对"全员 + ghost"产生误导性错误。

## 验证

- `tests/unit_tests/agent_teams/test_team_tools.py` 新增 `test_invoke_multicast_rejects_full_roster`：
  spawn m1、m2，组播 `["m1","m2"]`，断言 `success is False`、`error` 含 `"broadcast"`、`data is None`。
- 4 个既有组播用例（`all_success` / `dedup_preserves_order` / `single_element_does_not_degrade` /
  `skips_blank_entries`）的测试团队此前恰好让"组播目标 == 全体其他成员"，会被新规则拒。调整为
  各自多 spawn 一个成员，使组播目标成为**严格子集**，各用例原意（全送达 / 去重保序 / 单元素不降级 /
  跳空白）完整保留，断言不变。
- 不受影响、未改：`partial_failure`（roster `{m1}` ≠ `{m1,ghost}`）、`all_fail`（roster 空）、
  `rejects_wildcard` / `rejects_user` / `empty_list`（对应守卫在全员检查之前命中）。
- `pytest test_team_tools.py` → 88 passed, 14 skipped，无回归；`ruff check` + `ruff format --check` 干净。

## 已知遗留

- 全员判定不按成员状态过滤：`list_members()` 含 SHUTDOWN 成员。实践上 LLM 不会把已下线成员
  列进组播目标，因此 `set(deduped)` 通常是活跃成员子集、不等于含死成员的 roster，不会误触发；
  真把死成员也列进去而触发"改用广播"也是合理纠正。暂不引入状态过滤这层复杂度。
- subprocess 模式下 `_team` 注入路径与 inprocess 一致，本改动未涉及跨进程差异。
