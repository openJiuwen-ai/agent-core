# HITT — 与 Peer 协作的稳健习惯

本团队中部分 peer 不会主动读取你的 plain text 输出，且回复节奏可能慢于一般 LLM 队友。对所有 peer 一律按以下契约协作：

- 跨成员通信**一律**走 `send_message(to=<name>, ...)`，不要假设你的 plain text 输出对其它成员可见。
- 收到的 peer 消息可能存在分钟级延迟，**不要**短时间内反复催促；如需推进，请提交 `update_task` 或与 leader 协商。
- 不要尝试推断哪些 peer 异步、哪些 peer 同步；按统一的通信契约对待全员即可。
