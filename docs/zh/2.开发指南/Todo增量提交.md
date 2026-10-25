# Todo 增量提交与版本化快照

`TodoModifyTool.invoke` 保留 update/delete/cancel/append/insert_before/insert_after 的工具参数及批量语义。原生本地后端现在将读取、增量变换和条件原子写入包在同一事务中；跨实例协调按真实存储对象，而不是 session 短 ID。

默认仍写数组。`OPENJIUWEN_TODO_V2=1` 显式激活同文件 envelope，含 store_id、plan_id、writer_epoch、十进制字符串 revision、last_commit_id 和 todos。首次采用旧数组保留 ID/扩展字段；缺 ID 只在成功迁移时固定。关闭开关后已有 v2 文件可读、写入被拒绝，不能自动降级。

宿主在新执行前调用 `prepare_context(session, new_execution=True)`，在 model/tool 边界捕获 `todo_call_context_v2`，旧回调保留旧 binding。SDK session 使用 get_state/update_state。`todo_create` 默认 initialize；活动计划需明确 `plan_action=new|rebuild`，每次 invoke 仅创建一次。普通调整使用 modify，普通新一轮保留计划身份。

SDK 写入方改用 `update_todos(session_id, mutation, session=session)` 或 `sync_plan_statuses`；mutation 不得执行外部副作用。旧 `load_todos` 返回携带快照前置条件的 list，`save_todos` 只接受该前置条件或不存在的新文件，过时快照失败。不得把读出的 list 复制成无条件覆盖入口。

结果仍为模型可见 dict，内部 `todo_receipt` 属性包含本次提交快照，不序列化到模型响应，也不挂共享 last_result。宿主应在锁外据 receipt 发事件，不另读拼版本。冲突最多三次重读后重算原意图；结果未知不自动重放。默认 v1 自定义后端只承诺进程内协调；v2 需要明确后端能力且全部 reader/writer 配套，不能混入旧 writer。

JiuwenBox需配套其`file-snapshot`协议：SDK provider/client向实际daemon查询能力，并通过同一协议读取原字节hash/存在性、条件原子写入。比较与替换在沙箱内部完成，不在宿主路径上替换；普通upload/download不变。旧服务/旧daemon不支持时拒绝激活，传输异常与不支持协议分开报告；条件写不自动重建沙箱或重放。TaskPlanningRail以及宿主Flash/Code回调在准备失败时请求错误收尾，防止回调框架记录异常后继续调用模型。

回退优先停止新写入并保留 v2 reader。若必须降到旧二进制，先停掉所有 writer，导出最新 todos 到隔离副本，并用目标旧 SDK 完成一次读取、修改、保存的字段对照；不得恢复旧备份覆盖新提交。旧 SDK 可能在写回时丢掉未知扩展字段：存在这类字段且不能无损往返时，禁止直接降级，保留 v2 reader 和原文件。通过字段核对也会失去版本隔离保证，实际部署和依赖升级由宿主安排。
