# S_07 Skill 库开关状态

## 元信息

| 项 | 值 |
|---|---|
| 类型 | spec |
| 关联模块 | `openjiuwen/harness/skills/`（2 文件） |
| 最近一次修订日期 | 2026-08-23 |
| 关联 feature | N/A |

## 范围 / 边界

本规约定义 Skill 库开关状态（`skills/`）。`skills_state.json` 的解析点与
`collect_disabled_skills` 出口供单 agent rail 装配与 team Skill rail 两处消费。

具体覆盖：

- `skills/__init__.py`：`SKILLS_STATE_FILENAME` / `collect_disabled_skills`。
- `skills/library_state.py`：`skills_state.json` 读取器（`SkillLibraryState` 解析）。

不在本规约范围内：
- Skill rail（`SkillUseRail` / `SkillCreateRail`）—— `S_04`。
- `SKILLS_STATE_FILENAME` 的写入方（marketplace / install）—— 另一子系统。
- KVC 亲和钩子（`kv_cache/`）—— `S_16`。

## 不变量

1. **`library_state.py` 是 skill 开关的唯一解析点**：`skills_state.json` 格式
   `{"skill_configs": {"<name>": {"enabled": false}}}`；读取**防御性**——文件缺失 /
   不可读 / 格式损坏一律视为"没有关闭任何 skill"（损坏文件不得悄悄清空整个 skill 视图）。
2. **`collect_disabled_skills` 是唯一出口**：`skills/__init__.py` 导出；
   `SKILLS_STATE_FILENAME` 固定常量。单 agent rail 装配与 team Skill rail 都从这里
   fold 进 `disabled_skills`——一处解析，两处消费。

## 接口契约

```python
# skills/__init__.py
SKILLS_STATE_FILENAME = "skills_state.json"
def collect_disabled_skills() -> list[str]
```

错误 / 返回语义：

- `collect_disabled_skills` 状态文件损坏 → `[]`（不抛）。

## 数据结构

### skills_state.json

| 字段 | 语义 |
|---|---|
| `skill_configs` | `{skill_name: {"enabled": bool}}` |
| 缺失 / 损坏 | 全部视为已启用（防御式读取） |

## 与其它 spec 的关系

- `collect_disabled_skills` 消费方：`SkillUseRail` / team Skill rail —— `S_04`；
  skill 工具 `ListSkillTool` / `SkillTool` —— `S_05`。
