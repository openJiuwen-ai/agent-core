---
id: B_20261127_1458_jiuwen-memory-uninit-event-loop
title: JiuwenMemoryRail 卸载阻塞事件循环修复
type: B
author: Wang Tao
author_email: zzyyww0808@gmail.com
modules:
- openjiuwen/harness/rails/memory
files:
- openjiuwen/harness/rails/memory/jiuwen_memory_rail.py
- tests/unit_tests/harness/rails/test_jiuwen_memory_rail.py
designs: []
design_note: 按已确认的最小方案修复卸载实现，补充 Work Rail 的等待收尾入口，不改变通用生命周期机制。
---

# JiuwenMemoryRail 卸载阻塞事件循环修复

## 现象与根因

关联 JiuwenSwarm #7130。配置热更新触发 JiuwenMemoryRail 重建时，后端已经应用权限，
却因卸载旧 Rail 固定阻塞约 15 秒，响应超过前端超时，界面继续显示旧权限。
`uninit()` 使用 `get_running_loop()` 取得当前循环，再调用
`run_coroutine_threadsafe(...).result(timeout=15)`，阻塞了执行关闭协程所需的同一线程。
即便 Provider 已提前关闭、再次关闭立即返回，也必须先让出循环才能执行。

## 修复决策与边界

保留同步 `uninit(agent)`，所属工具、提示词和轮次缓存立即撤销。异步资源清理在运行中循环
使用 `create_task()`，保存任务引用；无运行中循环时用 `asyncio.run()` 执行相同流程。
同一挂载周期重复卸载只调度一次。先保存会话末抽取条件，再清空初始化标志，避免后台执行时
因标志已清空而遗漏 `on_session_end`。Provider 关闭保留 10 秒超时与失败日志。

不延长前端/测试超时，不把配置保存改成提前确认，不改通用 DeepAgent/Rail 接口。
直接转移卸载到任意线程可能改变异步资源所属循环，因此未采用。
宿主提前排空写入和关闭资源的耗时仍然存在，本修复仅消除循环线程上的固定同步等待。

## 回归证据

两条目标分支的定向回归均为 **97 passed**，覆盖记忆 Rail、交互中断恢复和 CLI 装配：

```bash
python -m pytest tests/unit_tests/harness/rails/test_jiuwen_memory_rail.py tests/unit_tests/harness/rails/test_jiuwen_memory_interrupts.py tests/unit_tests/harness/test_cli_jiuwen_memory_rail.py -q -o addopts='' -p no:cacheprovider
```

新增覆盖运行中循环卸载、慢关闭期间循环可推进、会话末抽取开关、重复卸载、关闭异常/超时
以及同步卸载后再次挂载。原始实现的隔离证据为：即时关闭的 Provider 在 uninit 返回前
未执行，循环心跳延迟约 15 秒；与 LLM、网络和存储响应速度无关。

修复后相同隔离检查的卸载立即返回，50 毫秒循环心跳正常执行。使用模拟 Provider、真实
Swarm 排空函数及 Core 注册/卸载接口验证替换链路，两条分支分别约 4 毫秒、5 毫秒完成，
旧 Rail 已移除，新 Rail 和记忆工具正常注册。宿主提前关闭与 Rail 后台关闭仍是既有的两次调用，
本次的去重仅针对同一 Rail 挂载周期内重复调用 `uninit()`。

两个本地 Swarm 页面完成真实模型对话后，连续切换权限、刷新读取均成功，配置保存响应约
0.4～1.1 秒，无请求超时。当前基线的这些页面操作未观察到记忆 Rail 重建，因此这部分验证
仅证明页面权限保存正常；原故障的卸载路径由上述隔离检查与真实注册/卸载链路覆盖，
不能据此声称已完整复现测试部署的端到端触发条件。

## 契约同步与适用范围

当前约束见 S_04 的 JiuwenMemoryRail 卸载清理章节及 rails/AGENTS.md。
本次不改变 Coding/Team Rail 的共享所有者和关闭协议，不处理无关的消息丢失问题。

## 异步收尾与重挂载补充

非阻塞卸载仍需要宿主显式等待收尾。已复现 `asyncio.run()` 在卸载后立即结束时取消
尚未完成的关闭或会话末抽取；也已复现旧关闭任务在重新初始化后关闭 Provider，造成
Rail 标记已初始化而 Provider 已关闭。旧版同步等待同循环 Future 也不保证关闭成功，
不能将该问题表述成从“保证完成”退化为“尽力而为”。

新增 `wait_closed()` 供宿主在卸载后等待，使用 `asyncio.shield()` 避免等待者取消连带
取消清理。`init()` 在旧任务仍执行时先抛出 `RuntimeError`，不改动挂载状态；等待结束后
可重新挂载。拒绝并发重挂载使单任务引用足够，无需增加清理任务集合或取消正在进行的写入。

宿主调用约定见 S_04；此入口不会自动改变 DeepAgent、CLI 或 Swarm 的退出流程。
不同 Rail 共用 Provider 时仍由宿主负责先等待旧 Rail，不能把同实例检查视为全局资源锁。
本轮回归覆盖退出前等待、会话末抽取开关、取消等待者、关闭中的重挂载拒绝及等待后重新初始化。


补充后的两条分支定向回归均为 **100 passed**。使用真实 DeepAgent 的 `register_rail()` /
`unregister_rail()` 与模拟 Provider 验证：关闭期间注册失败且未挂入回调/工具，等待结束后
重新注册、初始化、抽取和关闭均成功。此次未重跑 Swarm 页面，也未修改其退出调用方。
