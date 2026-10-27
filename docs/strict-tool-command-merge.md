# 宿主可选的工具与命令从严合并

宿主在运行时同时注入 `defer_unmatched: true` 和 `strict_tool_command_merge: true` 时，shell 工具权限和命令规则独立判定，再按 `DENY > ASK > UNDETERMINED > ALLOW` 合并。两个字段都必须是布尔值 `true`。缺省或关闭时保留原策略；单独使用 `defer_unmatched` 的宿主不切换策略。

例如工具 `bash: allow`、命令无规则覆盖时返回 `UNDETERMINED`；工具 `bash: ask`、命令白名单允许时返回 `ASK`。默认兜底配置不填补未覆盖结果。非 shell 工具不新增命令检查。

命令检查内部继续沿用既有内置／用户规则层级，以及宿主 `composition.source_order`。未命中的单条规则不参与严格比较，只有整个命令检查缺少结论时才产生未确定结果。

可解析的复合命令需要覆盖所有子命令。选中规则来源中的完整字符串精确允许，可以补足子命令未匹配；不能覆盖子命令 `ASK`／`DENY`，也不能补足工具未匹配。正则片段、通配模式、解析失败和复杂结构不取得该完整串覆盖能力。原 shell 结构防护仍有效。

全部防护合并后，新策略允许范围匹配的 `exact_operation` 授权复用到 `ASK` 和 `UNDETERMINED`。`DENY` 不可被授权覆盖。会话授权和旧命令审批记录保持既有范围与防护限制。Core 不负责云端交互，由宿主处理剩余未确定结果。

`allow_with_scope/subcommands` 则生成多条 `shell_subcommand` 授权，在 Shell 命令检查内逐条复用，随后继续合并其他 Guard。子命令共享原调用的初始目录、工具和解释器；例如从 `E:\work` 调用 `cd repo && git status`，两条授权均绑定 `E:\work`。覆盖全部子命令后可解除 Shell 的 ASK/未确定结果，不能覆盖 DENY、更高优先级来源的 ASK 或整条命令的结构风险，也不解除 FileGuard 等其他 Guard 的 ASK。无法可靠拆分时只提供完整命令精确授权；客户端省略范围字段时由服务端默认选择可用范围，无法拆分则回退到 `exact`；显式 `exact` 保留完整命令授权。

验证入口：`tests/unit_tests/harness/security/test_strict_tool_command_merge.py`。同时回归整个 `tests/unit_tests/harness/security`，确保未启用新策略的宿主、规则来源排序、授权和其他防护行为不变。
