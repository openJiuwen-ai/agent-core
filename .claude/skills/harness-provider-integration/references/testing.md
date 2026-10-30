# 测试：单测钉住映射，e2e 证明能力

生成的包自带三类测试，都在包目录下用 `python -m pytest` 运行（先 `pip install -e ".[test]"`）：

| 文件 | 跑什么 | 依赖 |
|---|---|---|
| `tests/test_harness.py` | 假运行时驱动的单测 | 无网络、无厂商运行时 |
| `tests/test_registration.py` | openjiuwen 经 entry point 解析本包；manifest 工厂建出未启动的 harness | 包已安装 |
| `tests/test_e2e.py` | 真实 CLI / SDK 上的协议契约 | 运行时已安装、已登录；否则自动跳过 |

`tests/contract.py` 是 openjiuwen 内置 provider e2e 契约的副本；`tests/conftest.py` 提供 `workdir`。

## 单测

脚手架生成的单测在**生成后立即全绿**（三种模式均已在仓库外的干净环境验证），它们是厂商化
过程中的回归网。厂商化时同步改夹具，保持绿色。

| 模式 | 假运行时 | 运行方式 |
|---|---|---|
| ACP | `tests/fake_agent.py`：独立 Python 脚本，stdio 上说 ACP（JSON-RPC），按 prompt 关键词选择行为 | harness 用 `command=(sys.executable, fake_agent.py)` 真实启动子进程 |
| 一次性 CLI | `tests/fake_cli.py`：独立脚本，每次调用打印参考记录后退出 | 同上 |
| SDK | 测试文件内 `_install_fake_sdk`：`monkeypatch.setitem(sys.modules, SDK_MODULE, module)` | 进程内 |

假运行时把收到的东西写进 `FAKE_*_STATE` 指向的 JSON（argv、prompt、会话、权限结果），测试据此
断言线上发出的内容。跨进程恢复（checkpoint）也靠这个文件保存会话。

必须覆盖（模板已有，厂商化后保持）：

- [ ] 配置校验：未知字段报错；凭据字段不进 `repr`。
- [ ] 惰性：`provider.create(...)` 不启动进程 / 不 import SDK；harness 停在 `TERMINATED`。
- [ ] 运行时缺失：CLI 不存在或 SDK 未安装时 `start` 失败为 `process_start_failed` / `HarnessError`，
      且不留半开的 cycle（`state is TERMINATED`）。
- [ ] 文本 Turn：DELTA… + FINAL，`final_output` 正确，用量上报。
- [ ] 工具 Turn：STARTED/COMPLETED 同 `item_id`，STARTED 带真实参数，COMPLETED 带 `result` /
      `rendered_result`。
- [ ] 追加轮次串行：第二个输入被接受为 `FOLLOW_UP`，事件序号不交错。
- [ ] 失败：厂商错误 → 共享类别；重试中的错误 → `DiagnosticEvent(kind=retrying)` 而非失败。
- [ ] 进程/连接中途消失：Turn FAILED，之后的 Turn 不挂起。
- [ ] `stop()`：活动 Turn ABORTED，排队 Turn 以 `HARNESS_STOP` 收口。
- [ ] 每项能力成对：声明时测行为、未声明时测 `UnsupportedHarnessCapabilityError`
      （`skipif(card.supports(...))`）。
- [ ] 系统提示词只在新会话的首轮送达；恢复的会话不重复。
- [ ] 宿主交互：声明 `TOOL_APPROVAL` 时走宿主，否则按默认；中止中的请求回取消。
- [ ] CLI 来源：PATH 上有 CLI 时用它、不碰 bundle；PATH 上没有时由 bundle 兜底且其目录前置到子进程
      PATH；`cli_path` 覆盖一切；兜底 bundle 损坏时启动失败（`bundled.BUNDLED_ROOT` 指到临时目录模拟）。

夹具规则：**形状来自真实运行记录**。假运行时只是"我们代码的证据"；按想象写的夹具会把错误映射钉死。

## e2e

按卡片能力自动选择用例；用运行时自己的默认模型与登录，不在测试里安装或配置模型；需要换模型时
设 `<NAME>_E2E_MODEL`。

| 用例 | 契约函数 | 证明 |
|---|---|---|
| 文本 | `run_text_turn` | 基本 Turn、IDLE/TERMINATED 状态 |
| 工具 | `run_tool_turn` | 工具 item 成对、答案正确 |
| 追加 | `run_follow_up_turns` | 串行、同一事件流 |
| 中止 | `run_abort_turn` | ABORTED 且会话可继续（GRACEFUL/FORCE_ABORT） |
| 恢复 | `run_resume_turns` | 新进程恢复会话（CHECKPOINT） |
| entry point | `create_harness` / `build_harness_context` | 装好的包经 openjiuwen 工厂跑通一轮 |
| steer | `run_steer_turn` | STEER 进入当前 Turn（声明时自己加） |
| 提问 | `run_user_input_turn` | USER_INPUT；`tool_hint` 用厂商自己的工具名 |

另外按需补：MCP 挂载（stdio FastMCP fixture，`McpServerConfig(name, McpTransport.STDIO,
command=(sys.executable, "-c", code), env=...)`，断言工具 item 与答案里的随机标记）、系统提示词送达
（暗号题）、同一 runtime 记忆。

真实运行很慢（一次几十秒到数分钟）：给足超时，或放后台跑。卡住时先用一个带硬超时的探针脚本打印
每个通知，定位是"模型没拿到工具 / 没走到预期分支"还是"我们死锁"。

## 平台 wheel 冒烟

`build_runtime.py` 对本机平台自动执行：安全解压到临时 `site-packages` → `sys.path` → 解析 provider →
确认加载的是解压出的模块 → bundled CLI `--version`。要做完整的冻结宿主演练（验证兜底），就在一个 PATH 里**没有**
该 CLI 的环境里，把解压目录追加到 `sys.path`，用 `create_harness(manifest, provider="<name>")` 跑
`contract.run_text_turn` 与 `run_tool_turn`。

## 收尾检查

```bash
python $SKILL_DIR/scripts/scaffold.py --check openjiuwen-harness-<name>   # 必须 0 个标记
python -m pytest -q                                                        # e2e 不能全是 skipped
```
