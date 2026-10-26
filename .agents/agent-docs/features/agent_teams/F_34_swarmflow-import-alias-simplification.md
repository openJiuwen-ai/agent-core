# Swarmflow Import 别名机制简化

## 元信息

| 项 | 值 |
|---|---|
| 日期 | 2026-06-05 |
| 范围 | `workflow/engine/{facade,loader,runner}.py`、`workflow/runner.py`、`workflow/engine/aliases.py`（删除）、`tests/unit_tests/agent_teams/workflow/test_engine.py` |
| 测试基线 | `tests/unit_tests/agent_teams/workflow/` 18 passed |
| Refs | #751 |

## 背景

swarmflow 脚本用 `from swarmflow import agent, parallel, ...` 引入原语，但磁盘上
**没有** `swarmflow` 包——这个名字是运行时映射到 `engine/facade.py` 的别名。

原实现（`engine/aliases.py`）把这层映射当作**运行时临时资源**来管理：

- `facade_aliases(names)` 是个 contextmanager，进入时 `sys.modules[name] = facade`、
  退出时还原；
- 配一套**引用计数** `_refs` + 快照 `_saved`，理由是"`sys.modules` 是进程全局，
  多个 run 并发时安装必须可组合而非互相覆盖"；
- `loader._import_module` 在 `with facade_aliases(["swarmflow"])` 里 `exec_module`，
  别名只在**导入脚本模块那一瞬间**存在；
- `run_workflow(import_as=...)` 公共参数 + 外层 `with` 额外映射 legacy 名
  `jiuwenswarm.swarmflow`（唯一调用方 `workflow/runner.py` 传固定常量 `_IMPORT_AS`）。

两个问题暴露了这套设计的过度复杂：

1. **延迟 import 静默失败**：别名只在 `exec_module`（模块顶层）期间存在，而 `run()`
   是之后才执行的。脚本若把 `from swarmflow import agent` 写在 `run` 体内，到执行时
   别名已卸载 → `ModuleNotFoundError`，且零提示。
2. **默认名与自定义名行为不一致**：`import_as` 名贯穿整个 run 执行期（外层 `with`），
   `swarmflow` 却被显式从外层剔除、只剩导入期短命别名——同样写法两种结果。

## 数据结构

关键洞察：**进程内的别名映射是编译期已知的固定常量**，不是运行时动态资源。脚本只认
**单一包名** `swarmflow`：

```
swarmflow → facade   （固定，进程内唯一映射）
```

它永远指向**同一个** facade 对象。`grep` 证实 `facade_aliases` 仅 loader/runner 两处调用，
`import_as` 唯一真实传入者是模块级常量 `_IMPORT_AS = "jiuwenswarm.swarmflow"`（上游 dw/wf
引擎的原始包名），无任何动态值、无外部调用方。

既然映射固定且唯一，"不同 run 装不同映射互相覆盖"这个引用计数所防的问题**根本不存在**——
所有 run 装的是完全相同的映射，安装本身幂等。整套引用计数 + save/restore 在管理一个伪状态。
而 legacy 名 `jiuwenswarm.swarmflow` 既无脚本使用、又是 `import_as` 存在的唯一理由，一并移除，
脚本格式收敛到单一包名。

## 决策

把映射从"每次 run 临时安装/卸载"改成"facade 模块导入时一次性静态登记"：

1. **`facade._register_aliases()`**：模块加载末尾执行一次，用 `sys.modules.setdefault`
   把唯一包名 `swarmflow` 登记到 facade。固定映射、进程内常驻、无 per-run 安装/卸载。
2. **`loader._import_module`**：删 `with facade_aliases(...)`，改 lazy `from . import facade`
   触发注册后直接 `exec_module`。
3. **`engine/runner.run_workflow`**：删 `facade_aliases` 导入、`import_as` 参数、外层 `with`。
4. **`workflow/runner`**：删 `_IMPORT_AS` 常量与两处 `import_as=` 传参。
5. **删除 `engine/aliases.py`**（72 行）。
6. **收敛为单一包名**：移除 legacy `jiuwenswarm.swarmflow` 映射，脚本只支持 `from
   swarmflow import ...`。

代码层面体现：永久登记天然让顶层 import 与 `run` 体内延迟 import **同样生效**——延迟
import 不再是"打补丁支持的特性"，而是固定映射的自然结果。

## 拒绝的方案

- **方案 B：保留 `import_as` 公共参数，仅删引用计数**。把 `aliases.py` 退化成幂等永久
  安装的普通函数，保留 API。拒绝原因：`import_as` 是"伪灵活性"——唯一调用方传固定常量，
  无外部用户。保留它等于保留一个永远只取一个值的参数，违背"不为不存在的需求加配置"。
- **方案 C：保持现状（仅让延迟 import 生效）**。即把 `swarmflow` 也纳入贯穿全程的外层
  `with`。拒绝原因：那是在"运行时管理临时状态"这个错误框架里打补丁。根因是映射本就该是
  静态事实，框架本身应被移除而非修补。

## 验证

- `tests/unit_tests/agent_teams/workflow/` 19 passed。
- 手动复现（MockBackend）：顶层 import 与 `run` 体内延迟 import 两种写法均成功。
- 单测：`test_lazy_import_inside_run_resolves`（`run` 体内延迟 `from swarmflow import`）。

## 已知遗留

- `setdefault` 语义下，若进程内已存在真实的 `swarmflow` 包则不覆盖；当前无此场景，
  未做冲突告警。如未来需在同进程接入同名真包，再评估。
