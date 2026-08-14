---
name: sft-optimize
description: Launch SWE dataset Docker cases with a supervisor LLM, collect direct SFT samples through SFTOnlineRail, upload them to the online-RL gateway, and optionally trigger SFT training. Use this when the user asks to optimize/fine-tune a model with SFT from SWE task cases.
allowed_tools: [mcp_exec_command, bash]
---

# SFT Optimize

Use this skill when the user asks to fine-tune or optimize a model with SFT from a dataset mapping and a supervisor LLM endpoint.

## Mandatory Action

When this skill is selected, the user has already requested execution. Do not
ask for confirmation and do not only summarize the parameters. Immediately run
this command from the `agent-core` repository with the available shell command
tool (`mcp_exec_command` or `bash`):

```bash
python examples/jiuwenrl_online/skills/sft-optimize/scripts/run_sft_optimize_skill.py \
  --request '<原始用户消息>'
```

Use the full original user request as `--request`. The wrapper owns all parsing,
defaults, Docker rollout, sample upload, and optional training-task creation.
After the command finishes, summarize the command result and mention the tenant,
sample count, training task id, or error shown by the script.

## Trigger Message

Typical user request:

```text
我想要用 sft-optimize 技能对模型进行微调：数据集是 examples/jiuwenrl_online/sft_e2e/data/sft_short_10_cases.json，10 个用例，并发 2，supervisor LLM 是 http://172.17.0.5:18002，模型 Qwen3-0.6B。
```

When the user asks for `sft-optimize`, `SFT 优化`, `SFT 微调`, `supervisor replay`, or `教师模型采集轨迹`, run the wrapper script below. Do not ask the user to run `run_sft_optimize.py` manually.

Install this skill into local jiuwenswarm with:

```bash
bash examples/jiuwenrl_online/skills/install_sft_optimize_skill.sh
```

## Public Inputs

- Dataset mapping path, usually named `dataset_mapping.json`, JSONL, or a SWE Docker image Markdown list.
- Limit / offset when the user wants a subset.
- Concurrency when the user wants more than the default.
- Whether to only collect trajectories or also trigger SFT training.

## Internal Defaults

Do not expose these unless the user explicitly asks. The wrapper reads environment variables first, then falls back to the current local debug defaults:

- `RL_GATEWAY_URL` / `TRAJECTORY_GATEWAY_URL`: default `http://172.17.0.5:18080`.
- `RL_SCHEDULER_URL`: default `http://127.0.0.1:18080`.
- `SUPERVISOR_URL`: default `http://172.17.0.5:18002`.
- `SUPERVISOR_TOKEN`: default `EMPTY`.
- `SUPERVISOR_MODEL`: default `Qwen3-0.6B`.
- `RL_ONLINE_TENANT_ID` / `WEB_USER_ID`: default `local-web-user`.
- `SFT_OPTIMIZE_DATASET_MAPPING`: default `examples/jiuwenrl_online/skills/sft-optimize/data/sft_short_10_cases.json`.
- `SFT_OPTIMIZE_LIMIT`: default `10`.
- `SFT_ROLLOUT_CONCURRENCY`: default `2`.
- `SFT_TASK_ROLLOUT_TIMEOUT`: default `900`.
- `SFT_OPTIMIZE_PYTHON`: Python executable for the rollout CLI. If unset, the wrapper tries `openjiuwen-sft`, then `openjiuwen-rl`, then the current Python.

## Rollout Backend

The wrapper defaults to `docker` when `--backend` and `SFT_ROLLOUT_BACKEND` are not set.

- `docker`: SWE-bench Docker cases, used by `examples/jiuwenrl_online/sft_e2e/data/sft_short_10_cases.json`.
- `local_program`: local Python exercise cases, used by `examples/jiuwenrl_online/skills/sft-optimize/data/sft_short_10_cases.json` and the local E2E runbook.
- `akernel`: Yuanrong/AKernel sandbox backend. It derives the sandbox image from the SWE image as `swe.cn-east-3.myhuaweicloud.com/openyuanrong/swe-<original-image-name>` unless `AKERNEL_SANDBOX_IMAGE` or `AKERNEL_SANDBOX_IMAGE_TEMPLATE` is set. `local_repo` remains as a compatibility alias.

If the user explicitly wants the non-Docker local Python flow, pass `--backend local_program`.

## Command

The command is repeated here for reference. Run from the `agent-core` repository with the original user request as `--request`:

```bash
python examples/jiuwenrl_online/skills/sft-optimize/scripts/run_sft_optimize_skill.py \
  --request '<原始用户消息>'
```

The wrapper delegates to the skill-local rollout entrypoint in
`examples/jiuwenrl_online/skills/sft-optimize/scripts/run_sft_optimize.py`.

If the user says "只采集", "仅采集", "不训练", or "不要训练", pass no training trigger. Otherwise requests containing "微调", "训练", "优化模型", or "触发训练" trigger `/v1/training/tasks` after replay.

## Behavior

- The local jiuwenswarm continues using its business LLM.
- The rollout session launched by `task_rollouter` uses `SUPERVISOR_URL` / `SUPERVISOR_MODEL` as the teacher LLM.
- Each SWE Docker task container uses the supervisor LLM settings passed in the command.
- Docker task containers load `SFTOnlineRail` through environment variables and set `SFT_ONLINE_UPLOAD_MODE=sample`.
- The uploaded payloads are `sft-sample-v1` training samples, so scheduler does not need a raw replay stage for this direct optimize flow.
- The older raw replay flow remains available when `SFT_ONLINE_UPLOAD_MODE=raw`.
- For cross-machine training, omit `--trigger-training`, export pending samples
  with `examples/jiuwenrl_online/sft_transfer/export_sft_samples.py`, then import
  them on the training machine with
  `examples/jiuwenrl_online/sft_transfer/import_sft_samples.py --trigger-training`.

## Examples

Collect and train with defaults:

```bash
python examples/jiuwenrl_online/skills/sft-optimize/scripts/run_sft_optimize_skill.py \
  --request '我想要用 sft-optimize 技能对模型进行微调：数据集是 examples/jiuwenrl_online/sft_e2e/data/sft_short_10_cases.json，10 个用例，并发 2'
```

Only collect samples:

```bash
python examples/jiuwenrl_online/skills/sft-optimize/scripts/run_sft_optimize_skill.py \
  --request '用 sft-optimize 只采集 supervisor replay 轨迹，数据集 examples/jiuwenrl_online/sft_e2e/data/sft_short_10_cases.json，limit=10，并发=2'
```

## Validation Checklist

- The selected dataset cases show the expected `instance_id`, Docker image, and task prompt metadata.
- Gateway stats show pending SFT samples for the target tenant after rollout.
- If training is triggered, `/v1/training/tasks` returns one task and scheduler consumes the pending samples.
