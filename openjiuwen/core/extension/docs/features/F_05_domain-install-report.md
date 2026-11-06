# 按域加载与共享安装报告

| 项 | 内容 |
| --- | --- |
| 最近修订 | 2026-11-06 |
| 关联计划 | EX-05，负责人李志远 |
| 当前状态 | Core 已实现并通过模块回归；Gateway 产品接线未在本次实施 |
| 契约 | [包加载规约](../specs/S_01_package-loading.md)、[Registry 规约](../specs/S_02_registry-and-bus.md) |

## 1. 改动前与当前流程

改动前，Registry 只能查询本地 Runtime，Swarm 自行选择启动包路径。两个进程共用目录时无法等待对方依赖，也无法共同保护卸载。

当前由宿主传安装根目录和报告路径。Core 完整发现、选版、检查依赖并稳定排序，再按可选 domain 加载。本地 Handler 仍只注册到本地 Bus，报告只同步包状态。

```text
open_install_dir：绑定目录、报告、域 → 原子登记进程并持有运行锁
load_installed：全目录发现 → 选版、检查依赖及环 → 发布完整计划 → 筛本域队列
逐包加载：登记操作 → 等依赖 → 复核 → 复制、准备并发布 → 提交报告
```

启动选择每个 ID 的最高合法版本；仍存活执行者的包使用实际生效版本，选中版本失败不降级。无依赖时按 SHA-256(ID)、ID 排序，再从完整顺序中筛域。受管目录的 ID、版本须与 manifest 一致。返回结果包含全局发现拒绝项及本实例执行包的报告。

## 2. 确定接口与职责

```python
async def open_install_dir(
    self, install_dir: Path, *, report_path: Path, host_version: str | None = None,
    domain: str | None = None, allowed_domains: Collection[str] | None = None,
    poll_interval: float = 0.5, dependency_timeout: float = 300.0, lock_timeout: float = 5.0,
) -> None: ...

async def load_installed(self) -> tuple[ActivationReport, ...]: ...
async def begin_shutdown(self) -> None: ...
async def close(self) -> None: ...
```

以上方法均属于 Core 的 Registry；实际调用先 init，再 await open。open 不返回包路径列表，report_path 必传。三个时间参数统一用秒；Core 不读取产品配置。

Manifest 和 ActivePackageRecord 的末尾增加 domain: str | None = None。未启用域限制时允许旧包缺域；传入 domain 或 allowed_domains 后，package.domain 必须是非空字符串。匹配不自动修正大小写或空白。SDK 不包含产品域名，allowed_domains 由宿主提供。

| 模块 | 当前职责 |
| --- | --- |
| manifest.py、schema | 解析域字段，保留原有声明、入口和 ZIP 约束 |
| package_manager.py | 物理发现、来源打开、自己的受管副本复制与删除 |
| selection.py::build_startup_plan | 全目录选版、跨域冲突、依赖检查及稳定排序 |
| install_report.py::PackageInstallReport | YAML、锁、进程登记、操作占用、依赖等待及提交 |
| management/handlers.py | 调用报告服务，执行原有安装、重载、卸载和查询流程 |
| Runtime | 真实 Handler、generation、在途调用和资源清理，不伪造远端生效记录 |

共享目录中 package.id 全局唯一。同 ID 的磁盘版本不能分属不同域；域迁移须先完成原域卸载。显式安装批次重复 ID 仍整批拒绝。

`extension.plan` 和 `extension.apply` 的请求必须显式携带同一次报告登记返回的 `PackageOperationToken`。Handler 按 token 校验 package、operation、process 和 operation_id，不依赖调用任务的上下文变量。因此，事件可以由其它异步任务转发；`operation_scope` 只用于授权当前 apply 或文件操作，不再承担事件间传递身份的职责。

## 3. 报告保存什么

报告路径由宿主传入，单进程同样写报告。文件名和产品默认目录不在 Core 写死。

| 位置 | 字段及用途 |
| --- | --- |
| 报告头 | schema_version、run_id、install_dir、startup_order、processes |
| processes[process_id] | domain、state；state 为 starting/running/stopping |
| packages[package_id] | domain、process_id、active_version、requires、status、reason、pending |
| reason | null 或包含 code、message 的映射 |
| pending | operation_id、operation、version、requires；没有未完成操作时为 null |

包 ID 用作键，不再重复保存 id。active_version/requires 记录当前真实生效版本及依赖；pending 记录本次目标版本及依赖。重载失败可以保留有效旧版本，因此两组字段不能合并。

status 取 pending、waiting、installing、reloading、uninstalling、success、failed、unknown。ActivationReport 仍只有 SUCCESS/FAILED，不混用这两类状态。卸载全部成功后删除包项；取消、结果未知或未清理完时保留 pending。

查询先检查执行者是否仍持运行锁。已退出或 stopping 执行者的旧 active_version 不作为有效成功；重载明确失败而旧版仍有效时，旧版仍可被查询和依赖。

## 4. 两把锁与重启清空规则

报告旁的 .yaml.lock 是稳定的读写锁，每次读写都短暂持有；逻辑上只改目标包，物理上锁整份 YAML，避免两次整文件写入互相覆盖。最终目录改名、删除与发现共用此锁。复制暂存文件、加载扩展、事件执行和等待都在锁外；文件工作在线程中执行，取消会等线程结束。

报告旁的 .locks/ 中，每个执行范围有一把运行锁，从登记持有到关闭；进程崩溃由操作系统释放。锁文件还在磁盘上不表示进程仍在运行。同域重复执行者、全量执行者与按域执行者混用均拒绝。

初始化在一次读写锁内完成：探测已有运行锁 → 没有存活执行者时生成新 run_id 和空 packages → 取得自己的运行锁 → 登记随机 process_id → 原子写入。空报告是合法 YAML，startup_order=null、packages={}，不是零字节文件。

两个旧进程都已退出时，第一个新进程看到所有运行锁已释放，建立空报告并登记自己；第二个新进程取得报告锁时看到第一个已持运行锁，因此加入同一 run_id，不再次清空。只重启一侧时，另一侧运行锁仍在，只重建重启域的包状态，保留存活域。新进程接管已退出执行者留下的包项时，先清空旧 `active_version` 和 `requires`，再登记新的 pending；否则新的存活 process_id 会让旧版本被误判为仍然生效。

不能判断清楚的 I/O 错误直接报错。报告损坏不覆盖；报告丢失但仍有运行锁时拒绝重建。已经有启动队列执行时，后加入者沿用已发布顺序及目标版本；队列执行期间在线写入返回忙碌。

## 5. 等待、失败与关闭

wait_for_dependencies 使用单调时钟，默认每 500ms 读报告，最多等 300 秒；锁获取预算为 5 秒。均可由宿主配置。完整计划先写出所有域的待安装包：依赖未开始或正在安装时等待，成功且版本满足才继续；缺失、明确失败或执行者已退出则拒绝。超时只结束当前等待，不把仍在安装的依赖改成失败。

begin_operation 同时检查全局 ID、依赖图并登记 pending.requires。卸载检查其它存活包的 active 和 pending 依赖；重载还检查目标版本是否兼容。发布前复核，成功提交时把目标依赖转为 active 依赖，避免保护空档。

本地发布与报告提交不构成跨进程原子事务。若本地动作已经得到 `ActivationReport`，随后提交报告失败，Handler 记录异常并返回该本地结果，不让提交异常覆盖单包结果或中断同批其它包。共享报告保留 pending/unknown；后续 `package.query` 根据本地 Runtime 补正报告，不重跑扩展。没有可确认结果时保留 unknown，显式清理后才释放占用。宿主关闭先 begin_shutdown，撤销本地 Handler 和资源后 close 释放运行锁。

## 6. 验证记录与边界

2026-11-05，在仓库 Python 3.11 环境运行：

```text
python -m pytest tests/unit_tests/core/extension tests/unit_tests/core/events
  --cov=openjiuwen.core.extension --cov-branch --cov-report=term-missing
  --override-ini=addopts='' --override-ini=log_cli=false -q
```

结果为 461 passed、2 skipped；扩展模块语句与分支合并覆盖率为 85%，install_report.py 为 85%。开发计划 EX-05 引用本记录。test_install_report.py 覆盖任意 SDK 域、兼容无域、缺域、全局 ID、最高版本与稳定顺序、跨域环和等待、反向依赖、失败重载、取消、报告损坏、锁超时、重启与查询核对，以及死执行者接管、精确磁盘 manifest 优先和损坏版本隔离。test_package_events.py 覆盖 plan/apply 携带显式 token 后跨异步任务触发。

真实 Python 子进程覆盖依赖方先启动、依赖先启动、同时启动、同时争用同 ID，以及强制结束进程后的旧成功失效。它验证本机文件协议，不代表 Gateway 网络管理入口已经接通。

只支持同主机、支持锁和原子替换的本地文件系统。Windows 已实测，Linux/macOS 未在本次环境执行。报告不保存函数对象，也不恢复崩溃前的在途调用或任意外部副作用。

占用写入已原子替换、随后刷盘报错时，调用方尚未取得 token，不执行扩展代码；服务尽力写 unknown。若此时报告仍不可写，本地保留该操作已放弃的事实，文件恢复后允许显式卸载清理；不将其当成仍在执行的安装。

启动扫描时，另一存活进程已经登记、但尚未完成目录复制的安装目标，也参与依赖图；当前包等待其报告结果，不把“目录暂未出现”误判为依赖永久缺失。

启动计划以磁盘 manifest 为包声明事实来源。报告命中相同 `(package.id, version)` 时，保留磁盘对象及其完整字段；仅当报告已经登记目标、而对应目录尚未可见时，才用报告中的最小字段补出临时节点。相同版本的报告 domain 或依赖与磁盘声明冲突时拒绝该包，不用报告覆盖磁盘声明。

枚举受管版本时，各版本目录独立校验。合法的其它域目录参与全局版本判断；缺域、声明损坏或身份不合法的旧目录被跳过，不再使整个包枚举失败，也不能仅凭目录名获得删除授权。只有报告或 Runtime 已知的版本可进入损坏副本清理路径。
