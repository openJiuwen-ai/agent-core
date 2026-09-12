# skill_train/envs：新数据集快速适配指南

本文说明如何把一个新的 QA / item-list 风格数据集接到 `skill_train`。公共运行时（dataloader 接线、并行 resume rollout、默认 reflect）已抽出，新数据集主要补「数据与评分」。

模板优先参考：[`searchqa/`](./searchqa/)（结构最简单）。多模态见 [`docvqa/`](./docvqa/)，工具环路见 [`officeqa/`](./officeqa/)。

## 何时可以快速适配

适合（复用 `DatasetEnvAdapter` + `run_parallel_rollout`）：

- 样本是带 `id` 的 dict 列表
- rollout 对每条样本独立执行后打分
- 结果至少包含 `id` / `hard` / `soft`

需要额外工作：

- 工具多轮环路（参考 officeqa）
- 多模态特殊输入（参考 docvqa）
- 自定义 reflect、非 item-list env manager

## 目录骨架

在 `openjiuwen/agent_evolving/skill_train/envs/<myenv>/` 下创建：

```text
myenv/
├── __init__.py          # lazy export XxxAdapter
├── adapter.py           # DatasetEnvAdapter 子类
├── dataloader.py        # SplitDataLoader 子类
├── evaluator.py         # hard / soft 评分
├── rollout.py           # process_one + 薄包装 run_batch
├── prompts/
│   └── rollout_system.md
└── skills/
    └── initial.md
```

可选：`prompts/analyst_error.md`、`analyst_success.md`（缺省回落到 `skill_train/prompts/`）。

## 接入步骤

### 1. DataLoader：行规范化与加载

继承 `SplitDataLoader`，实现 `normalize_row`（或私有 `_normalize_row`）与 `load_split_items`。

CSV / JSON 目录可直接用：

```python
from openjiuwen.agent_evolving.skill_train.envs.io_helpers import load_csv_or_json_split

def load_split_items(self, split_path: str) -> list[dict]:
    return load_csv_or_json_split(split_path, _normalize_row, env_label="MyEnv split")
```

若有 id-split 需物化，在 `setup` 中调用对应 `ensure_materialized_*`（模式同现有三个 env）。

规范化后建议至少有：`id`、`question`、答案字段（如 `answers`）、可选 `task_type`。

### 2. Evaluator：hard / soft

在 `evaluator.py` 中实现对本数据集的评分语义（EM/F1、ANLS 等），由 `process_one` 调用。约定：

| 字段 | 含义 |
|------|------|
| `hard` | 0/1，门禁与准确率统计 |
| `soft` | 0.0–1.0，连续分 |

### 3. Rollout：`process_one` + 薄 `run_batch`

`process_one(item, out_root, skill_content, ...)` 负责：

1. 拼 system / user（可用 `format_skill_section`）
2. 调 LLM
3. 写预测产物（可用 `write_prediction_artifacts`）
4. 调 evaluator，返回含 `id` / `hard` / `soft` 的 dict

`run_batch` 只组装闭包并委托公共层：

```python
from openjiuwen.agent_evolving.skill_train.envs.rollout_batch import run_parallel_rollout

def run_batch(items, out_root, skill_content, *, workers=16, task_timeout=600, ...):
    def _run_one(item: dict) -> dict:
        return process_one(item, out_root, skill_content, ...)

    return run_parallel_rollout(
        items,
        out_root,
        process_one=_run_one,
        workers=workers,
        task_timeout=task_timeout,  # 不需要 per-task timeout 时传 None
        make_timeout_result=_timeout_result,  # 可选
        make_error_result=_error_result,      # 可选
    )
```

公共层会：读/写 `out_root/results.jsonl` 做 resume、线程池并发、进度日志、可选超时。

### 4. Prompt 与初始 Skill

- `prompts/rollout_system.md`：含 `{skill_section}` 占位符
- `skills/initial.md`：训练起点 skill 文本

### 5. Adapter：只留专有逻辑

```python
from openjiuwen.agent_evolving.skill_train.envs.dataset_adapter import DatasetEnvAdapter
from openjiuwen.agent_evolving.skill_train.envs.myenv.dataloader import MyEnvDataLoader
from openjiuwen.agent_evolving.skill_train.envs.myenv.rollout import run_batch

class MyEnvAdapter(DatasetEnvAdapter):
    def __init__(self, ..., workers: int = 16, ...):
        self.workers = workers
        # analyst_workers / failure_only / minibatch_size / edit_budget 供默认 reflect
        self.dataloader = MyEnvDataLoader(...)

    def rollout(self, env_manager, skill_content: str, out_dir: str, **kwargs) -> list[dict]:
        items: list[dict] = env_manager  # build_*_env 返回的是 item list
        return run_batch(items=items, out_root=out_dir, skill_content=skill_content, workers=self.workers, ...)

    def get_task_types(self) -> list[str]:
        return self.collect_task_types("myenv")
```

`setup` / `get_dataloader` / `build_train_env` / `build_eval_env` / `build_env_from_batch` 已由基类实现，一般不必重写。默认 `reflect` 也已接好，无需 override。

### 6. 注册

在 [`../registry.py`](../registry.py) 的 `_register_builtins` 中加一行：

```python
_try_register("myenv", "openjiuwen.agent_evolving.skill_train.envs.myenv.adapter", "MyEnvAdapter")
```

训练侧用 `get_env_adapter("myenv", **cfg)` 实例化；未知 kwargs 会按 `__init__` 签名过滤。

## 复用 vs 自写

| 自写 | 复用 |
|------|------|
| `_normalize_row` / 加载 | `DatasetEnvAdapter` |
| `evaluator` | `run_parallel_rollout` |
| `process_one` | `format_skill_section` / `write_prediction_artifacts` |
| `rollout_system.md` + `initial.md` | `SplitDataLoader` / `load_csv_or_json_split` |
| registry 一行 | 默认 `EnvAdapter.reflect` |

## 结果字段约定

每条 rollout 结果至少：

```python
{"id": "...", "hard": 0, "soft": 0.0}
```

常用扩展：`question`、`predicted_answer`、`fail_reason`、`agent_ok`、`n_turns`、`task_type`、`phase`（`timeout` / `error`）。未知字段可进下游 `RolloutResult.extras`。

## 自检清单

- [ ] `get_env_adapter("myenv")` 可导入
- [ ] dataloader `setup` 后能产出 train/val/test items
- [ ] 单条 `process_one` 写出 `predictions/<id>/` 产物
- [ ] `run_batch` 中断后重跑会跳过 `results.jsonl` 已有 id
- [ ] 结果含 `id` / `hard` / `soft`，trainer 可统计准确率

## 相关代码

- [`dataset_adapter.py`](./dataset_adapter.py) — 数据集 adapter 基类
- [`rollout_batch.py`](./rollout_batch.py) — 并行 resume rollout
- [`io_helpers.py`](./io_helpers.py) — skill 段、预测产物、CSV/JSON 加载
- [`base.py`](./base.py) — `EnvAdapter` 抽象接口与默认 reflect
