# DSH 启动能力适配与 SDK 升级

- 日期：2026-09-11
- Refs: #751
- 范围：`harness_providers/dsh`、`pyproject.toml`、`uv.lock`

## 已落地能力

SDK/runtime 从 0.1.2a3 成对升级至 0.1.5rc1，extra 下限同步更新，lock 仅更新这两个包。
模型、认证、用户 DSH home 沿用本机配置，不额外安装或重配模型。

- `HarnessContext.system_prompt`：临时 Cordis plugin 默认仅替换原生 prefix section（F_100 进一步增加 append 模式）。
  保留现有其它 sections；显式 system_prompt_env_var 仍走 custom composition。
- MCP_TOOLS：context 中 stdio/HTTP MCP server 转为原生 mcp-client。名称唯一，argv/env/cwd、
  url/headers 保留，failOnStartupError=true；initialize 等待完整 Loader 就绪。
- overlay 只包含固定 JSON.parse 表达式与生成的环境变量名；prompt、MCP 凭据放子进程环境，
  不插值执行用户文本、不写入 patch。stop 和失败回滚均清理临时目录。
- 保留现有单消费者事件、工具/文本/思考映射和同一进程内多轮会话。

## 经源码与真机核实的限制

[PyPI 0.1.5rc1](https://pypi.org/project/deepseek-harness-sdk/0.1.5rc1/) 对应的
[SDK server](https://github.com/deepseek-ai/deepseek-harness/blob/c291e7961a515f6d7af9304e7fd1d257929aef26/packages/sdk/server/src/server.ts)
仅分派 initialize、session/prompt、shutdown；没有 steer/abort/pause/resume/ask-user 的 SDK 请求通道。
原生 AgentRegistry 的 resume 与 Web/ACP 控制接口存在，不代表 SDK server 已接入。

曾验证跨进程复用持久 session ID：新 runtime 首次 prompt 实际报 `session already exists`。
server.createSession 无条件调用 agents.create，没有调用 agents.resume。因此不声明 CHECKPOINT 或
PERSISTENT_SESSION，不发布只能指向磁盘文件却无法让 SDK 恢复的 checkpoint。后续需要上游 SDK
server 增加 resume/load 入口，或单独评估 ACP 接入；不通过私有 runtime 对象补丁伪造此能力。

system-prompt.personaPrefix 遵循 DSH 原生模板语法，未知 `{{variable}}` 会报错。
MCP 只在启动时装配，不支持 IN_PROCESS/热更新。自动 overlay 不适用于 launch_args_override。

## 验证

- fake SDK：MCP config 映射、名称/transport 验证、overlay 不含 prompt/凭据、失败/stop 清理。
- 本机真实 DSH：文本、读文件、follow-up、能力拒绝、manifest factory、同进程会话记忆、system prompt、
  MCP 工具调用；使用本机默认 provider/model。
