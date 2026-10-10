---
name: sft-supervisor-rollouter-swe
description: Run SWE-bench teacher rollouts in isolated YuanRong or local-Docker sandboxes and collect SFTOnlineRail trajectories.
---

# SWE SFT Teacher Trajectory — SandboxAPI rollout

此 Skill 通过 `SandboxAPI` 统一执行 rollout 和 official eval。默认后端通过
`POST <yuanrong.endpoint>/api/agent` 创建独立的 `runtime_spec.sandbox_type=docker` 实例；
`sandbox.backend=local_docker` 时改用宿主机 Docker CLI。主 Agent 不使用 Docker SDK；
本地后端只在明确配置并且宿主机具备 Docker 权限时访问 Docker daemon。SWE 镜像由 case 的
`docker_image` 指定，JiuwenSwarm/OpenJiuwen 运行时以只读 mount 注入，实例结束后清理。

部署前的宿主机准备、凭据边界以及 `local_docker`/YuanRong 两种后端的完整样例见
[README.md](README.md)。随包提供的 `/path/to/host/*` 是部署占位符，执行前必须替换为
目标环境中的真实绝对路径。

在 AgentOS Web 主沙箱中，`/home/root` 是用户工作区的沙箱视图，而 YuanRong API 的
`workspace` 必须使用宿主机可见路径。Skill 只接受 AgentOS 自动注入的
`SFT_SWE_HOST_WORKSPACE_ROOT` 作为映射锚点，不允许 prompt 或命令行覆盖该路径；直接在宿主机
运行时则不做路径转换。

## 配置与运行

所有输入都属于所选 `test_suite`。不要在 Skill 顶层创建或使用 `cases/`、`config/`、
`data/` 目录；每个测试集必须自包含 `config.json`、`supervisor.json`、`testcase.json`
及其 `data/`。用户侧通过 `scripts/run_sft_testsuite.py --test-suite ...` 选择测试集，wrapper
会生成仅本次运行使用的临时 runner 配置。

每个 suite 的 `config.json` 必须设置：

- `yuanrong.endpoint`：YuanRong frontend；同机部署的默认样例为
  `http://127.0.0.1:8888`，跨主机部署时替换为主 Agent 可达的地址；
- `docker.host_site_packages`：宿主机包含 `yr` 和兼容 protobuf 的 site-packages，例如
  `/path/to/host/site-packages`；
- `docker.runtime_prefix`：宿主机上已经安装新版 JiuwenSwarm、OpenJiuwen 与
  `SFTOnlineRail` 的完整 Python/Conda prefix。YuanRong 会把该目录只读挂载到子容器的
  `/opt/jiuwenswarm-runtime`；路径必须对 YuanRong executor 可见。通过 5173 触发时该宿主
  路径通常不会出现在 AgentOS 主沙箱的 mount namespace 中，这是正常现象；主 Agent 不得
  因本地 `test -d`/`Path.exists()` 返回 false 而中止或要求用户确认，应由本脚本的 YuanRong
  smoke/真实创建结果判断；
- `docker.image_python`：子容器内用于启动 JiuwenSwarm 的解释器，默认
  `/opt/jiuwenswarm-runtime/bin/python`，它必须与上述 prefix 的挂载目标对应。不要把宿主机
  Python 可执行文件直接挂入并在 SWE 镜像内运行，因为宿主机与 SWE 镜像的 glibc 版本可能
  不兼容（例如报 `GLIBC_2.38 not found`）；
- `jiuwenswarm.python`：已安装 JiuwenSwarm、OpenJiuwen 和在线轨迹模块的解释器；按部署
  环境填写，例如 `/path/to/host/jiuwenswarm/bin/python`；
- `model.profile`：值固定为同目录的 `supervisor.json`；wrapper 会把该文件安全地物化为
  仅本次运行的受保护 profile，不使用按 suite 名生成的别名，也不会覆盖全局 profile。
  实际模型可为
  GLM 或 JiuwenSwarm 支持的其他模型，与 MiniMax-M3 主会话配置相互独立；
- `gateway.host_url`：运行 Skill runner 的网络命名空间在评测后上传 WAL 时使用的地址。
  直接在宿主机运行时通常使用 `http://127.0.0.1:18081`；从 5173 的 AgentOS 主沙箱
  触发时，loopback 指向主沙箱自身，必须改为该沙箱可达的宿主机或 Gateway 地址。
- `gateway.container_url`：YuanRong Docker 子容器访问宿主机 Gateway 的地址；本机
  通过 Docker bridge 使用 `http://172.17.0.1:18081`。旧配置只填 `gateway.url` 时，该值
  同时作为两个场景的回退地址。
- `sandbox.backend`：沙箱传输后端。默认 `yuanrong` 通过 YuanRong Frontend REST 创建
  `sandbox_type=docker`；设置为 `local_docker` 时由宿主机上的 Docker CLI 直接创建同样的
  task/eval 容器。两种后端共用 suite、镜像、挂载、环境变量和评测逻辑；本地后端不需要
  `yuanrong.endpoint`，但运行 Skill 的宿主机必须能执行 Docker CLI 并访问 Docker daemon。

评测和 golden patch 文件随各个 suite 一起提供。每个 suite 的配置应保持以下结构（路径
相对于该 suite 根目录）：

```json
"eval": {
  "enabled": true,
  "python": "/path/to/host/python/bin/python3",
  "dataset": "data/eval.json"
},
"gold": {
  "enabled": true,
  "source": "data/gold.json",
  "patch_field": "patch"
}
```

- `eval.dataset` 是当前 suite 用例的官方元数据，包含 `image`、`eval_script`、
  `FAIL_TO_PASS` 和 `PASS_TO_PASS`；路径必须位于该 suite 的 `data/` 内；
- `gold.source` 是严格对应同一批 50 道题的 golden patch 数据，也放在 suite 自己的
  `data/` 内（例如 `data/gold.json`）；
- `eval.python` 是控制面 Python 的兼容/自检字段，应填主 Agent 可见的绝对路径，例如
  `/path/to/host/python/bin/python3`。YuanRong eval 路径读取 suite 内置 JSON，不依赖该
  解释器中的 `swebench`、`pyarrow` 或 Docker SDK；不要填写主 Agent 不可见的宿主机路径；
- `eval.enabled=false` 会 fail-closed；正常运行没有跳过 eval 的命令行或 prompt 参数。

受保护 profile 的结构（示意；真实 key 不得提交到 Skill 仓库或写入 prompt）：

```json
{
  "model_name": "<provider model id>",
  "provider": "<JiuwenSwarm provider>",
  "api_base": "https://<provider endpoint>",
  "api_key": "<secret>"
}
```

例如切换 GLM 时只替换 profile 中的四个值；无需修改脚本、Skill 名称或主会话的
MiniMax-M3 配置。`api_base` 必须是无凭据、无 query/fragment 的 HTTPS 地址。

## Test Suite 入口

推荐使用 `scripts/run_sft_testsuite.py` 选择测试集。Skill 自带两个 suite：

```text
test_suite/mini5/
  config.json
  supervisor.json
  testcase.json
  data/eval.json
  data/gold.json
test_suite/mini50/
  config.json
  supervisor.json
  testcase.json
  data/eval.json
  data/gold.json
```

`supervisor.json` 是当前 suite 的模型配置，必须只包含以下四个字段：

```json
{
  "model_name": "deepseek-chat",
  "provider": "deepseek",
  "api_base": "https://api.deepseek.com/v1",
  "api_key": "${SUPERVISOR_API_KEY}"
}
```

`api_key` 在技术上可以直接写明文，也可以使用 `${ENV_NAME}` 由运行环境提供密钥。明文
只适用于不纳入 Git 的本机部署副本，并应将文件权限设为 `0600`；随代码分发的配置推荐
保留环境变量引用。不要把真实密钥提交到 Git 或写进 Web prompt。wrapper 会从该文件创建
一个仅本次进程使用的 `supervisor.json` profile，不使用 `supervisor-default`，也不修改
全局 profile 文件。

`config.json` 是该 suite 的运行配置；其中 `swe.case_file` 会强制使用 suite 根目录的
`testcase.json`，`eval.dataset` 和启用时的 `gold.source` 必须指向同一 suite 的 `data/`
目录。suite 内随包分发的文件路径统一以 suite 根目录为基准（如 `data/eval.json`），
不会相对于 shell 当前目录或 Skill 根目录回退；宿主机 Python、Conda prefix 等运行时字段
可以继续使用绝对路径，见下文“宿主机路径与预准备”。不同 suite 的 `instance_id`、eval
metadata 和 golden patch 不得混用。

新测试集按同样结构创建后，可以把整个目录放到 `test_suite/` 下，也可以从任意位置用
绝对目录或 `.zip` 传入。例如 suite 目录必须包含：

```text
my-set/
  config.json                 # eval.dataset: data/eval.json
  supervisor.json
  testcase.json
  data/eval.json
  data/gold.json
```

然后先做无副作用检查：

```bash
python3 scripts/run_sft_testsuite.py --test-suite my-set --dry-run
```

`--test-suite` 支持三种形式：

1. 简单名称（只含字母、数字、`.`, `_`, `-`），从本 Skill 的 `test_suite/` 目录查找；
2. 绝对目录路径，直接使用，目录 basename 作为 suite 名；
3. `.zip` 压缩包（绝对路径或相对于调用进程当前目录）。压缩包会安全解压到
   `run_sft_testsuite.py` 同级的 `test_suite/<zip 文件前缀>/`，去掉 `.zip` 的前缀作为 suite
   名。压缩包内容可以直接包含 `config.json` 等文件，也可以整体包在一个顶层目录中；
   不能包含绝对路径、`..` 越界成员或符号链接。若同名目录已存在，会复用并更新压缩包中
   的文件，不递归删除其中其他文件。

例如：

```bash
python3 scripts/run_sft_testsuite.py --test-suite /srv/swe/my-set --dry-run
python3 scripts/run_sft_testsuite.py --test-suite /srv/swe/my-set.zip --dry-run
```

eval/gold 文件必须位于 suite 的 `data/` 下；模型 API key、Gateway/YuanRong 地址和宿主机
路径不应通过 prompt 覆盖。确认输出中的 case 数、eval/gold 路径和模型信息正确后，再做
单题 smoke：

```bash
python3 scripts/run_sft_testsuite.py --test-suite my-set --limit 1 --smoke
```

实际采集命令如下；`--limit`、`--workers`、`--case-id` 和 `--set` 会覆盖该 suite 的
配置，临时配置用完即删除：

```bash
python3 scripts/run_sft_testsuite.py --test-suite my-set --limit 5 --workers 5
python3 scripts/run_sft_testsuite.py --test-suite my-set \
  --workers 5 --set docker.cpus=2 --set docker.memory='"4g"'
```

`--set` 只允许覆盖资源、并发、超时和网络等非敏感 operational 字段；不能覆盖模型、
凭据、Gateway、YuanRong endpoint 或任何数据路径。wrapper 会拒绝名称中的 `..`、路径穿越
和 suite 外的 data 文件。`run_sft_rollout.py` 是 wrapper 的内部 runner，不是用户选择
用例的入口；不要直接传旧的 `--case-file` 或顶层配置路径。

对应的 5173 Web prompt 可以只指定 suite 名和执行范围：

```text
请使用 sft-supervisor-rollouter-swe 技能，通过
scripts/run_sft_testsuite.py --test-suite my-set --limit 5 --workers 5 在后台运行该测试集。
先检查 suite 的 config.json、supervisor.json、testcase.json 和 data/ 是否完整，再执行
YuanRong Docker rollout、official eval、golden hint 清洗、仅 resolved 的 sft-sample-v1
上传和实例清理。不要在 prompt 中传模型密钥、API 地址、Gateway 地址或宿主机文件路径，
不要修改 suite 文件、清空 Redis、启动 Controller 或触发训练；启动后报告 PID、run_id、
实际 case 数和日志路径。
```

## 宿主机路径与预准备

随包的 `config.json` 使用 `/path/to/host/*` 占位符；这些占位符不是可运行默认值。
部署时需要按下表逐项替换，且不能混淆宿主机路径和容器路径：

| 字段 | 所在位置/用途 | 是否需要宿主机预先准备 |
| --- | --- | --- |
| `docker.host_site_packages` | 宿主机 `site-packages`，包含 YuanRong 的 `yr`/protobuf 等依赖，按 mount 注入 task/eval 容器 | 是；目录必须存在且 YuanRong executor 可读 |
| `docker.runtime_prefix` | 宿主机已经安装 JiuwenSwarm、OpenJiuwen、SFTOnlineRail 的 Conda/venv prefix，按只读 mount 注入 `/opt/jiuwenswarm-runtime` | 是；目标环境先安装并确认 `bin/python` 可用 |
| `jiuwenswarm.python` | 控制面用于检查预安装 JiuwenSwarm runtime 的 Python 解释器 | 是；必须是控制面可见的绝对路径 |
| `eval.python` | 控制面评测/数据读取 Python；使用 parquet 时需带 `pyarrow`，内置 JSON 评测不要求 `swebench` | 是；填写当前执行进程可见的绝对路径 |
| `docker.workspace_root` | 每个 case 的宿主机 workspace/artifact 根目录 | 是；YuanRong executor 可写、与主 Agent 路径映射一致 |
| `docker.output_root` | run、patch、WAL、eval 报告和上传回执的宿主机输出目录 | 是；可写并保留足够空间 |

`docker.image_python` 是容器内部路径（通常为
`/opt/jiuwenswarm-runtime/bin/python`），不是宿主机文件；它必须与
`docker.runtime_prefix -> /opt/jiuwenswarm-runtime` 的 mount 目标一致。不要把宿主机
Python 直接挂入 SWE 镜像，以免 glibc/架构不兼容。

以下不是文件路径，但同样属于部署环境配置：`yuanrong.endpoint` 必须是控制面可达的
YuanRong Frontend；`gateway.host_url` 必须是 runner 所在网络命名空间可达的 Gateway，
`gateway.container_url` 必须是 YuanRong task/eval 容器可达的地址。它们应写在受控的
suite/config 或部署配置中，不能由 Web prompt 传入。

宿主机启动前请准备：Docker/SWE task 镜像和 official eval 所需镜像（aarch64 不能直接
假定 x86 官方镜像可用）、Git 或本地 repo cache、已安装 SFTOnlineRail 的 JiuwenSwarm
runtime、RL Gateway 及 Redis。使用 `sandbox.backend=yuanrong` 时还要准备 YuanRong API；
使用 `sandbox.backend=local_docker` 时，当前用户必须能执行 Docker CLI 并访问 Docker
daemon（可先检查 `docker info`、Docker 组权限和 `docker.api_version`）。若从 5173 主 Agent 触发，确认
`SFT_SWE_HOST_WORKSPACE_ROOT` 已将 `/home/root` 映射到同一宿主机目录；不要在 prompt
中填写主沙箱不可见的宿主机路径。

先做不调用模型的 API 和 runtime 探针，再执行真实 rollout：

```bash
python3 scripts/run_sft_testsuite.py --test-suite mini5 --limit 1 --smoke
python3 scripts/run_sft_testsuite.py --test-suite mini5 --limit 5 --workers 5
python3 scripts/run_sft_testsuite.py --test-suite mini50 --limit 50 --workers 5
```

本地 Docker 后端可在不修改 suite 文件的情况下覆盖后端：

```bash
python3 scripts/run_sft_testsuite.py --test-suite mini5 --limit 1 --smoke \
  --set sandbox.backend=local_docker
python3 scripts/run_sft_testsuite.py --test-suite mini5 --limit 2 --workers 2 \
  --set sandbox.backend=local_docker
```

`run.json` 的 `case_file`、`case_ids` 和选中的 suite 会记录实际输入；`--case-id` 可限制
单题，`--keep-container` 会保留 YuanRong 实例用于诊断，正常运行不要启用。结果在所选
suite 的 `docker.output_root`；随包占位值为 `/path/to/host/sft-runs`，执行前必须替换。

Web prompt 只传 suite 名（或经审核的绝对 suite 目录/zip）和执行范围，不传 API Key、API
Base、模型名、凭据路径或宿主机路径。50 题 Web 触发示例：

```text
请使用 sft-supervisor-rollouter-swe 技能，通过
scripts/run_sft_testsuite.py --test-suite mini50 --limit 50 --workers 5 在后台运行 mini50。
执行完整的 YuanRong Docker rollout、official eval、golden hint 清洗、仅 resolved 的
sft-sample-v1 上传和实例清理。不要修改 suite 文件、清空 Redis、启动 Controller 或触发
训练；启动后报告 PID、run_id、run 目录和日志路径。
```

## 容器注入与轨迹

YuanRong runtime 启动 Docker 容器前会先导入宿主机的 `yr`，因此 YuanRong 后端的 payload
必须同时挂载：

1. `docker.host_site_packages` → `/root/.local/lib/python3.11/site-packages`；
2. JiuwenSwarm Conda prefix → `/opt/jiuwenswarm-runtime`；
3. 每题 artifact/workspace → `/home/root`，本脚本在创建实例前复制到该目录中的
   `/home/root/run_sft_rollout.py`。不要再额外使用与 workspace 重叠的单文件 mount，当前
   executor 下这种重叠可能让脚本变成 0 字节文件。

容器内的 AgentServer 使用固定内部端口（默认 18092），端口只在该 Docker 网络命名空间
内使用；每题实例有独立网络和文件空间，不会产生并发冲突。SFTOnlineRail 配置为：

```text
TRAIN_BACKEND=SFT
SFT_ONLINE_UPLOAD_MODE=sample
TRAJECTORY_FORCE_WAL=1
RL_ONLINE_SESSION_DONE_ON_INVOKE_END=1
```

因此轨迹先落入每题 `sft-online-wal/`。rollout 后，Skill 使用内置 Verified metadata
指定的官方镜像和 `eval.sh`，通过所选 `SandboxAPI` 后端拉起 eval Docker；下载官方测试日志后，
按 SWE-bench 的 FAIL_TO_PASS/PASS_TO_PASS 规则判定 resolved。只有 resolved 且 WAL 完整的
`sft-sample-v1` 才上传；gold hint 清洗和 Gateway 上传
逻辑与 docker-lite 相同。完整的 golden hint 区块会从所有字符串字段删除；assistant
推理中仅复述并加引号的 marker 名称会被脱敏，用户、系统或工具字段中的孤立/未闭合 marker
仍按异常数据拒绝。Skill 不清空 Redis、不启动 Scheduler、不触发训练。

`upload-receipts/` 按 case 和 WAL 文件记录成功回执。对同一 run 重新执行上传时，脚本会重新
校验所有 WAL，但跳过已有成功回执的样本，避免正常重试造成重复上传；
`upload-summary.json` 分别记录本次新增上传数、复用回执数和累计成功数。

## 常见故障

- 配置/宿主依赖错误（如 `pre-installed JiuwenSwarm Python is missing`、
  `YuanRong host site-packages missing`、`Docker mount source is missing` 或
  `eval.python is not visible`）：先检查所选 suite 的 `config.json` 中路径是否为当前
  执行面可见的绝对路径，`docker.host_site_packages` 是否包含 `yr`，
  `docker.runtime_prefix/bin/python` 是否存在，`docker.workspace_root` 和
  `docker.output_root` 是否可写；本地后端再运行 `docker info` 验证 daemon 权限，YuanRong
  后端则验证 `yuanrong.endpoint` 可达。不要把 5173 主沙箱不可见的宿主机路径直接填入
  主沙箱侧需要读取的配置字段。
- `DeadlineExceeded`：先检查 `docker.host_site_packages` 是否存在 `yr`；没有该挂载时
  YuanRong runtime 会在容器中报 `ModuleNotFoundError: No module named 'yr'`，前端最终
  重试并返回超时。
- 容器启动后立即报 `GLIBC_x.y not found`：检查 `docker.image_python` 是否误指向了宿主机
  Python。应使用挂载 prefix 内、并已确认与 SWE 镜像兼容的解释器。
- WebSocket 502：检查 `runtime_spec.rootfs.ports` 与 `AGENT_SERVER_PORT` 是否一致，
  并确认 AgentServer 已启动。
- Gateway 连接失败：不要在 YuanRong Docker 子容器中配置 `127.0.0.1`，改用宿主机
  可路由 IP 或已配置的 host-gateway 地址。从 5173 触发时还要确认
  `gateway.host_url` 不是 `127.0.0.1`；健康检查使用 `/health` 或 `/v1/rl/health`，
  `/v1/gateway/health` 不是 Gateway 健康检查路由。
