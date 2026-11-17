# NetGuard 宿主出站适配

`PermissionManager` 保持 SDK 现有实现。配置写入侧用 `update_config` 发布完整配置；
工具权限 rail 和 HTTP 层只传已发布的 key，不使用一次性 `config=` 或新建引擎。
普通 fetch 的 URL 列表逐项判定，聚合保留最严格结果、warnings 和 external_paths。

Manager 的未知 key 仍抛 KeyError；权限 before_tool_call 边界将普通异常转换成
`AbortError`，避免真实回调框架记日志后继续执行工具。转换时不设置 `AbortError.cause`，
因为框架会改为抛出 cause，嵌套回调可能再次吞掉普通异常。已有 HITL AbortError 保持原样。
场景校验失败同样中止；普通 rail 提供默认的基线 key 和投影元数据接口供宿主工厂使用。

`PermissionInterruptRail` 在 NetGuard 启用时把选中的 key 写入本次工具上下文。
`AbilityManager` 仅在实际工具执行期间绑定 ContextVar，异常和取消均恢复；
`asyncio.to_thread` 会继承该上下文。没有此上下文的原有 HTTP 路径不改变。

`outbound_http` 为宿主网页/搜索提供同步 requests 和异步 aiohttp 传输：

- 拒绝歧义 URL，统一执行 URL 的 ASCII/IDNA 表示；宿主解析完整 DNS 地址快照。
- 仅调用 `check_permission("network_connect", {url, resolved_addresses, connect_address}, key)`。
  只有 ALLOW 可以连接，ASK/UNDETERMINED 不视为完成授权，未知 key 直接失败。
- 固定连接到校验过的地址，保留 URL 原域名对应的 Host、TLS SNI 和证书主机名校验。
- 逐跳检查重定向，跨源移除认证头；不自动重试其它地址或移除代理后直连。
- 受保护路径强制 TLS 验证；现有代理接口不能保证目标 IP 固定，因此明确拒绝代理。

requests 路径保留 `verify` 的 CA 文件/目录和 `cert` 客户端证书；固定连接池仍由
`HTTPAdapter.send -> cert_verify` 配置证书。aiohttp 路径使用独立、强制验证的默认 TLS
上下文，不继承调用方 session connector 的自定义 SSLContext 或客户端证书配置。

宿主必须在请求、会话 overlay、Skill 激活/撤销和团队配置变更时发布/删除对应 key。
AgentServer 用实例 owner 和会话代次区分注册；请求、后台子 Agent 持有引用，最后一个
使用者在 finally 中回收。容量回收仅淘汰空闲配置，不移除在途 key；SDK 配额仍满时拒绝
新请求。`permission_persist_context(ctx)` 只在“始终允许”的写入阶段绑定审批会话，
普通 rail 默认使用空上下文；正常工具判定仍只传 key，不携带/临时拼装配置。
不要把宿主连接检查误认为沙箱内子进程的运行时拦截；模型和 MCP 传输仍需各自适配。

验证入口：`tests/unit_tests/harness/security/test_outbound_permissions.py`，包括本地 HTTP、
TLS、DNS 重解析、混合地址、重定向、URL 歧义和取消隔离测试。测试不访问外部服务。
