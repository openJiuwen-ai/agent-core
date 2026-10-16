# 已安装 provider 的热替换：按 RECORD 指纹重新导入

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-10-13 |
| 范围 | `openjiuwen/harness_providers/factory.py`（`resolve_provider` / `_forget_replaced_distribution` / `_top_level_packages`）；skill `references/packaging.md` |
| 关联 spec | S_19（不变量 11） |
| 关联 feature | F_07（entry point 发现） |
| 测试基线 | `tests/unit_tests/harness_providers/test_provider_discovery.py` 12 通过（新增 2 条，撤回调用后"替换后重新导入"失败）；真实 kimi 旧 / 新 wheel 按 jiuwenswarm 的方式原地替换，同一进程内第二次解析拿到新代码 |
| Refs | #2006 |

## 背景

jiuwenswarm 上传新版三方 provider 时把 wheel 解压到同一个 `custom-agents/<name>/site-packages` 并原地
替换目录，`sys.path` 不变。`resolve_provider` 每次都重新读 entry point 元数据，但 `entry.load()` 走
`import_module`，模块已在 `sys.modules` 时直接返回旧模块。成员运行在常驻 worker 进程里，于是会话
`web_1a134e6c188_011a33a0ba1f` 装了新包仍跑旧代码，必须重启整个 worker 才生效。

## 决策

1. **以发行包 RECORD 的哈希为指纹**：RECORD 列出每个文件及其哈希，任何代码变化都会改变它。首次通过
   entry point 加载时记下指纹；之后再解析同一发行包（按安装目录 + 包名区分），指纹变了才处理。
2. **只移除该发行包自己的顶层包**：名单取 `top_level.txt`（setuptools 写），缺省时取 RECORD 里的顶层
   路径；从 `sys.modules` 移除这些包及子模块，`importlib.invalidate_caches()` 后由 entry point 重新导入。
   不碰 openjiuwen 与其它包。
3. **新老并存，不打断运行中的成员**：已创建的 harness 持有旧模块的对象，继续用旧代码跑完；新 session /
   新成员拿到新代码。旧模块在没有引用后回收。
4. **只作用于 entry point 来源**：内置 provider 封闭不变；进程内 `register_harness_provider` 注册的工厂由
   注册方负责。

## 拒绝的方案

- **每次解析都强制重新导入**：每次 spawn 都重复导入、多份模块常驻内存，且同一 provider 的两个新会话会拿
  到不同的类对象。
- **`importlib.reload`**：只重载单个模块，子模块与模块间引用会新旧混杂；也无法处理被删除的模块。
- **宿主侧（jiuwenswarm）在安装时清理模块**：安装发生在 web / 控制进程，成员跑在 worker 进程，跨进程
  无法清理；放在解析处对所有宿主（冻结版、pip）都成立。
- **每个三方成员独立子进程**：彻底隔离但代价大，与现有进程内驱动方式冲突，不是这个问题的最小解。

## 已知遗留

- 包导入时有全局副作用（monkeypatch openjiuwen、导入时注册 provider）或在函数里延迟导入本包模块时，热替换
  会重复副作用或新旧混杂；`packaging.md` 写明了约束。
- C 扩展无法重新加载；editable 安装改源码不改 RECORD，仍需重启；openjiuwen 自身升级仍需重启进程。
- jiuwenswarm 上传页还没有提示"新会话生效，运行中的成员需重启"。
