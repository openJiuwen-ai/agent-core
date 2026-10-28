# skillsbench

把本地 SkillsBench 检出接到 `skill_train`。根目录用环境变量 `SKILLSBENCH_ROOT` 指定（指向 `tasks/` 的上一级），未设置时报错；也可用 `--split-dir` / `data_path` 显式指定。只扫描 `<root>/tasks/` 里带 `task.md` 的子目录，不包含 `tasks-extra/`。

## 样本

每条 dict：

- `id`：任务目录名
- `question`：`task.md` 去掉 frontmatter 后的正文
- `task_type`：`metadata.task_type`
- `task_dir`、`timeout_sec`、`verifier_timeout_sec`、`verifier_script`
- `skills`：`environment/skills/<name>/SKILL.md`

训练不切验证集。`skill-train --env skillsbench` 对每个任务单独进化：优先加载任务包 `environment/skills/<name>/SKILL.md` 作为命名 skill 包（联合 rollout / 按 `skill_name` 归因更新）；若任务无自带 skill，则回退到 `skill_inits` 或 `skills/initial.md`。最多 5 次执行。官方 verifier 通过就停；失败且反思打出可应用补丁时，写下该任务的版本（多 skill 为 `skills/<name>/skill_v{k}.md`）并用于下一轮。没有梯度则停，不写新版本。

评测仍可按 `split_mode=ratio`（默认 `7:1:2`）使用已有的 `train/` `val/` `test/` `items.json`。

## 评分

`SKILLSBENCH_VERIFIER_MODE`（或 launch `verifier_mode`）控制打分方式，**默认且推荐 `local`**（agent 产物与打分同一 workdir）：

| 模式 | 行为 |
|------|------|
| `local` | 把 verifier 里 pytest 文件的 `/root` 改写成 agent 工作目录，用当前 Python 跑 pytest，写 `reward.txt`（需已安装 `pytest`；docx 类任务还需 `python-docx`） |
| `docker` | 用任务 `environment/Dockerfile` 建镜像，容器里跑 `verifier/test.sh`（可选） |

`reward.txt` 仍然决定二值 `hard`：

- `reward` 为 `1`：`hard=1`
- 其他情况：`hard=0`

`soft` 是 verifier 的真实通过率（0.0–1.0），按优先级取值：

1. pytest `-rA` 输出的逐项结果（parametrize 会展开成多条，`SKIPPED` 计入分母但不计入通过），要求日志里出现 pytest 结尾的 `... in 0.49s` 统计行，否则视为不完整输出而放弃；
2. 任务的 `ctrf.json`（`results.summary.passed` / `tests`，容器里由 `test.sh` 写出）；
3. 都拿不到时回退到二值：`hard=1 → soft=1.0`，否则 `soft=0.0`。

拿到的计数会一并写进 rollout 行（`tests_passed` / `tests_total`），并在 `[EVALUATION RESULT]` 里显示成 `soft: 0.9474 (18/19)`，让 reflector 能区分「只差一格」和「全错」。

评分不调用模型。Rollout 会把 `environment/` 下除 `Dockerfile` / `skills/` 外的输入文件拷进工作目录，并允许 agent 改文件；任务描述里的 `/root/...` 对应工作目录根（`/root/workspace/x` → `workdir/workspace/x`）。

本地打分额外适配：

- 打分前合并 seed 输入时**不覆盖/不删除** agent 已写出的文件
- 把 `workdir` 与 `workdir/workspace` 加入 `PYTHONPATH`，兼容产物写在根目录或 `workspace/` 子目录

## 运行

```bash
# 默认本地 pytest 打分
jiuwenswarm skill-train --env skillsbench --backend jiuwenswarm_cli_exec

# 改回 Docker verifier
set SKILLSBENCH_VERIFIER_MODE=docker
jiuwenswarm skill-train --env skillsbench --backend jiuwenswarm_cli_exec

# 只跑单个任务：把该任务放到一个只有它的 tasks 目录，再用 --eval-only
jiuwenswarm skill-train --env skillsbench --split-dir E:\path\to\tasks_offer_letter --eval-only --split train
```

## 全量分数（Docker verifier，不切分、不训练）

Agent 仍在宿主机经 Gateway 执行；**仅打分进 Docker**。一次跑完 `tasks/` 下全部带 `task.md` 的任务（约 87 条），不做 7:1:2 切分、不跑 ReflACT 训练：

```powershell
.\.venv\Scripts\Activate.ps1
# Gateway 需已启动：jiuwenswarm-start app
$env:SKILLSBENCH_ROOT = "skillsbench目录"
$env:SKILLSBENCH_VERIFIER_MODE = "docker"
powershell -ExecutionPolicy Bypass -File .\scripts\skillsbench_docker_full_eval.ps1
# 或直接：
# .\.venv\Scripts\python.exe .\scripts\skillsbench_docker_score_all.py
```

结果在 `outputs\skillsbench_docker_score_all_<时间>\summary.json`（`hard` / `soft` / 每条 `fail_reason`）。整体可能要很多小时。
