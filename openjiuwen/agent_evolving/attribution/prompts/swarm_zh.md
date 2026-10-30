# 多 Agent 协作（swarm）附加知识

本次会话是多 agent 协作场景，归因时需特别关注跨 agent 链路：

- leader（主 agent）拆解任务并派发给 member（子 agent）；member 的结果通过跨 agent 消息回传；
- send_message 是跨 agent 交流工具，承担派发与回传；
- 常见跨 agent 问题来源（作为归因线索，用轨迹证据核实）：
  1. 派发内容不完整或有歧义（member 按错误理解执行）；
  2. 派发错对象（任务给了不合适的 agent）；
  3. 回传结果不完整、失真或丢失（leader 基于缺失/错误信息继续）；
  4. 多个 member 的结果合并时冲突，或时序依赖未满足（后续步骤用了未就绪的结果）。

汇报跨 agent 问题时：
- 定位条目尽量覆盖链路两端；start_node_id 与 end_node_id 只能来自同一 subagent，跨 subagent 的链路请分别成条（如"派发端"与"接收端"各一条）；
- text_md 写明涉及的 subagent 与消息方向（谁派发给谁 / 谁回传给谁），并给出改进建议。
