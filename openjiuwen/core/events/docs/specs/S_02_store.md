# store 持久化规约

最近修订：2026-11-02。设计与验证入口：[F_14](../features/F_14_ops-store.md)。

## 1. 作用域与不变量

- ST-01：数据按真实 package_id 隔离，与 session_id、包版本及工作目录无关。
- ST-02：Core 接收宿主提供的目录 Path，数据文件为 `<directory>/<package_id>.json`；没有应用默认目录。
- ST-03：值仅支持 JSON null、bool、整数、有限浮点数、字符串、数组及字符串键对象；读写均复制。
- ST-04：单包完整 JSON UTF-8 文件最多 4 MiB，保留 key 插入顺序；损坏或超限文件不当空数据覆盖。
- ST-05：稳定侧文件锁保护读取最新文件、修改和原子提交；get 后计算再 set 不构成事务。
- ST-06：重载、停机保留数据，成功卸载删除包文件；锁文件保留以维持跨进程互斥。
- ST-07：set/delete 返回 False 只能表示确定未提交，提交不确定必须抛异常；取消仍向上传播。

## 2. 方法与事件

| 方法 | 事件及输入类型 | CORE 返回 |
| --- | --- | --- |
| `await ops.store.get(key, default=None)` | `store.get` / `StoreGetInput(package_id, key, default=None)` | JSON 值；缺失时原样返回 default |
| `await ops.store.set(key, value)` | `store.set` / `StoreSetInput(package_id, key, value)` | bool |
| `await ops.store.delete(key)` | `store.delete` / `StoreDeleteInput(package_id, key)` | bool；不存在也为 True |
| `await ops.store.keys()` | `store.keys` / `StoreKeysInput(package_id)` | `list[str]`，按插入顺序 |

输入类型从 `openjiuwen.core.events.builtins.event_types.store` 导入，均为 frozen dataclass。
key 为字符串，允许空字符串；package_id 必须是可安全用作单个文件名的包标识。
get.default 可以是任意对象，只有 key 不存在时使用，不序列化，也不混淆 JSON null。

Matcher 支持精确字符串字段 `package_id`、`key`，keys 只支持 package_id；空模式匹配所有合法请求。
未知字段、错误字段类型抛 `EVENTS_MATCH_INVALID`，未命中为 False。同步输入 Validator 检查请求类型、
包 ID、key 和 set 的 JSON 值。输出 Validator 对 set/delete 要求 bool，对 keys 要求字符串列表；
get 允许任意默认值。普通 Handler 故障与输出校验沿用 [Events S_01](S_01_events-contract.md)。

Ops 在每次调用中通过 `await ops.package.name()` 取得包 ID，要求存在真实包信息和有效的 Handler 调用。
通过 Registry 发布的 Handler 结束后，Runtime 拒绝旧 Ops 的 store 调用。
独立 Bus 的调用生命周期由宿主负责。方法绑定原 Bus，沿用当前 SessionContext，也允许没有会话。不增加新的 EventCallContext。
CORE 的 Ops 无包信息，使用请求中的 package_id 访问存储。

package_id 是原调用归属，拦截器可用替代请求改业务值，不得改归属；frozen 不等于身份认证。
同进程扩展按受信协议工作，EventBus 不认证手工构造请求。监听 store 事件时使用 next 取得默认结果，
不要调用同名 Ops 递归。可短路返回 False 拒绝写入；未执行 CORE 时不能宣称 CORE 已提交文件。

## 3. 文件与错误

`JsonStore(directory)` 从 `openjiuwen.core.extension.data` 导入，公开异步方法 get/set/delete/keys
与上表相同，但第一个参数为 package_id；另有 `cleanup_package(package_id, *, reason="uninstall")`。
读空包不创建 JSON，首次成功 set 才创建。删除最后一个 key 保留空对象文件，卸载删除整个 JSON。
读取失败、无效身份、非法参数、越界路径和提交未知走统一 BaseError/StatusCode 异常通道。
确定未提交的可识别写入 I/O 失败记录原因并返回 False；读取失败不返回默认值或空列表。
替换前 flush/fsync 临时文件；替换失败后核对目标内容，能确认原内容未变才返回 False，
已变成目标内容则成功，无法可靠判断抛 `EXTENSION_DATA_COMMIT_UNKNOWN`。取消不代表没有提交。

锁文件是 `<package_id>.json.lock`，与数据文件分离且卸载不删除。所有锁与阻塞 I/O 在线程中完成，
取消调用仍等待线程退出。路径检查拒绝符号链接、重定向到根目录外及非普通数据文件。
容量按完整序列化文件计量。没有缓存，不承诺多调用事务、进程间通知或网络文件系统锁语义。
文件锁不承担全局卸载准入；多个 Registry 或进程共用目录时，宿主须先停止所有相关资源使用者。

## 4. 装配与生命周期

`Registry.init(bus, *, store=JsonStore(directory), cleanup_callbacks=...)` 在既有 Build 之前绑定服务。
仅使用 Bus 时可调用 `openjiuwen.core.events.ops_impl.store.configure_store(bus, service)`，然后正常 Build。
未配置服务时事件可登记和 Build，但访问存储报错；构建不会创建目录或读写文件。
宿主回调先执行，Core store 删除随后执行，安装副本最后删除；任一失败不得报告卸载成功。
reload/shutdown 原因的 store 清理不删除数据。具体应用的装配和真实业务接入由宿主维护。
