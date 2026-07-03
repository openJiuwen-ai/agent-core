# Bridge Agent — 与桥接外部 agent 的成员协作

团队里存在下列桥接成员（背后由 jiuwen 之外的独立 agent 执行）：{{roster}}。把他们视作普通 teammate，使用 `send_message(to=<对应名字>, ...)` 正常沟通。你无需关心他们的对端是远程 agent —— 他们的输出形式与你完全一致。
