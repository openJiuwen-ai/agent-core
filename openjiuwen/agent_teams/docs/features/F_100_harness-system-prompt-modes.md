# Codex / DSH 系统提示词保留与替换模式

- 日期：2026-09-11
- Refs: #751

## 接口

```python
create_harness(manifest, provider="codex", config={"system_prompt_mode": "append"})
create_harness(manifest, provider="dsh", config={"system_prompt_mode": "append", "dsh_home": "/path/to/home"})
```

两者默认 `replace`，兼容此前调用。Claude 已有相同配置，默认仍为 `append`。
`ExternalCliAgentSpec.system_prompt_mode` 可将模式传到团队 Claude/Codex provider；未指定则保留各自默认值。

## Codex

append 在创建/恢复 thread 前通过 app-server `config/read(cwd)` 读取生效配置，包含 CLI/project 配置层；
若显式提供 thread_config.developer_instructions，则以该值为追加基底。只追加一次宿主提示词，
连接重建/fallback/resume 都重新读取基底，不以先前合并过的 thread 字段继续叠加。
读取失败则关闭新 client 并报告启动失败，不静默丢弃原指令。
replace 保留原先直接传 developer_instructions 的行为。两者都不设置 base_instructions。
空宿主提示词不做覆盖或追加；不自行扫描配置文件，也不把 AGENTS.md 当作 developer_instructions 字段拼接。

## DSH

append 使用临时 Cordis plugin 注册 `openjiuwen:host-instructions` section，顺序在原生 sections 之后，
保留全部已有 prefix/suffix。通过单次模板变量替换传入宿主内容，原始 `{{...}}` 不会被再次解析。
replace 通过 `system-prompt/assemble` hook 仅替换 `deployment:persona-prefix` 文本，保留其它 sections；
不用 config 对象覆盖，因为 Cordis 不对该字段做深度 merge。replace 新文本保留原生模板语法。

插件文件只包含固定代码和生成的环境变量名；用户文本仍只通过环境 JSON 传输。停止或启动失败均清理。
显式 system_prompt_env_var 是旧 custom composition 路径，与 append 不可同时设置。

## 验证

- 单测：有效配置读取与合并、重复连接不重复追加、thread config 优先、读取失败关闭 client、配置校验、
  DSH 独立 section/变量注册及文件不含宿主文本。
- 真机：Codex 原 developer codeword 在 append 中保留、replace 中消失；DSH prefix codeword 在 append
  中保留、replace 中消失，suffix codeword 两种模式均保留；append 的未注册模板字面量不触发解析错误。
