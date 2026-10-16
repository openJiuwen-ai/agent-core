# sft-supervisor-rollouter-swe

这个 Skill 在每个 SWE-bench case 中启动一个独立的 Docker 沙箱，运行
JiuwenSwarm supervisor，执行 official eval，并将通过评测的
`sft-sample-v1` 轨迹上传到 RL Gateway。沙箱后端有两种：

- `local_docker`：runner 直接调用宿主机 Docker CLI；
- `yuanrong`：runner 调用 YuanRong Frontend REST API，由 YuanRong 在宿主机上创建
  `sandbox_type=docker` 的 task/eval 容器。

两种后端共用 suite、模型 profile、挂载、Gateway 和评测逻辑。随包的
`test_suite/mini5` 与 `test_suite/mini50` 配置使用 `/path/to/host/*` 占位符，必须在目标
环境替换为真实路径后才能执行真实 rollout。

## 第一章：部署前必须保证的配置

### 1. 准备 JiuwenSwarm runtime

推荐在宿主机用独立 Conda 环境安装 JiuwenSwarm、OpenJiuwen 以及启用
SFTOnlineRail 所需的 agent-core 依赖。示例（路径仅为示意）：

```bash
conda create -p /srv/conda/envs/jiuwenswarm python=3.11 -y
conda activate /srv/conda/envs/jiuwenswarm
# 按目标版本的安装文档安装 jiuwenswarm、openjiuwen 和 agent-core
python -c 'import sys; print(sys.executable)'
```

将该 Conda prefix 填入 suite 的 `docker.runtime_prefix`，例如：

```json
{
  "docker": {
    "runtime_prefix": "/srv/conda/envs/jiuwenswarm",
    "image_python": "/opt/jiuwenswarm-runtime/bin/python"
  },
  "jiuwenswarm": {
    "python": "/srv/conda/envs/jiuwenswarm/bin/python"
  }
}
```

启动子容器时，runner/YuanRong 会把 `runtime_prefix` 以只读方式挂载到容器内的
`/opt/jiuwenswarm-runtime`。因此：

- `docker.runtime_prefix` 是宿主机路径；
- `docker.image_python` 是容器内路径，必须以
  `/opt/jiuwenswarm-runtime/bin/python` 开头；
- 不要把宿主机的 Python 可执行文件直接当成 SWE 镜像中的解释器，宿主机与镜像可能有
  不同的架构或 glibc 版本；
- 在目标宿主机上确认 `runtime_prefix/bin/python` 可以导入 JiuwenSwarm、OpenJiuwen
  和 SFTOnlineRail。

### 2. 准备 YuanRong Python 运行时依赖

`docker.host_site_packages` 是宿主机上包含 YuanRong `yr`、protobuf 等依赖的目录。它会
以只读方式挂载到 task/eval 容器的 root user site-packages；该目录必须真实存在，并且
YuanRong executor 有读取权限。例如：

```json
{
  "docker": {
    "host_site_packages": "/srv/conda/envs/yuanrong/lib/python3.11/site-packages"
  }
}
```

这个目录不能用 SWE 镜像内的任意 site-packages 替代。缺少 `yr` 时，容器可能在 bootstrap
阶段就失败，前端通常只表现为 `DeadlineExceeded`。

### 3. 准备 workspace、输出目录和镜像

`docker.workspace_root`、`docker.output_root` 是宿主机可见的绝对路径，必须可写，并且：

- `local_docker` 进程能访问它们；
- YuanRong executor 所在宿主机也能访问它们；
- 从 5173 AgentOS 主沙箱触发时，路径必须与
  `SFT_SWE_HOST_WORKSPACE_ROOT` 的映射一致，不能填写主沙箱看得见而 Docker/YuanRong
  看不见的路径。

SWE task 镜像由每条 case 的 `docker_image`/官方 metadata 指定；official eval 镜像也必须
  已在对应架构上可用。aarch64 环境不能默认使用 x86_64 官方镜像。

### 4. 准备模型 profile 和 API key

每个 suite 目录都有一个 `supervisor.json`，只包含以下字段：

```json
{
  "model_name": "deepseek-chat",
  "provider": "deepseek",
  "api_base": "https://api.deepseek.com/v1",
  "api_key": "${SUPERVISOR_API_KEY}"
}
```

`api_key` 技术上可以直接写明文，例如：

```json
"api_key": "sk-your-key"
```

但明文只适合本机部署副本：文件应设置为 `0600`，并且不得提交 Git、打包发布或写到
5173 Web prompt。推荐保留 `${ENV_NAME}`，启动前通过环境变量注入：

```bash
export SUPERVISOR_API_KEY='sk-your-key'
chmod 600 test_suite/mini5/supervisor.json
```

wrapper 会在本次运行中生成受保护的临时 profile，日志和 dry-run 输出不会打印 key。
`api_base` 必须是不含凭据、query 或 fragment 的 HTTPS URL。

### 5. 准备 Gateway、Redis 和测试集目录

RL Gateway 与 Redis 需要在 rollout 前启动；本 Skill 只上传轨迹，不启动训练，也不清空
Redis。Gateway 对 runner 和 Docker 子容器可能有不同地址：

```json
{
  "gateway": {
    "host_url": "http://127.0.0.1:18081",
    "container_url": "http://172.17.0.1:18081"
  }
}
```

从 5173 主沙箱触发时，`host_url` 不能盲目使用 `127.0.0.1`，应替换为主沙箱可达的
Gateway 地址；`container_url` 应是 Docker 容器能访问的宿主机地址。

每个测试集必须自包含以下文件，suite 内的文件路径从 suite 根目录解析：

```text
test_suite/my-set/
├── config.json
├── supervisor.json
├── testcase.json
└── data/
    ├── eval.json
    └── gold.json
```

`config.json` 中的 `swe.case_file` 应为 `testcase.json`，`eval.dataset` 和
`gold.source` 应为 `data/` 下的文件。`eval.python` 是控制面 Python 的绝对路径，
例如 `/srv/conda/envs/swe/bin/python3`；`image_python` 则始终是容器内路径，两者不要混淆。

## 第二章：`local_docker` 后端样例

### 2.1 最小配置

在 `test_suite/my-set/config.json` 中设置：

```json
{
  "sandbox": {
    "backend": "local_docker"
  },
  "docker": {
    "binary": "docker",
    "api_version": "1.39",
    "network": "bridge",
    "cpus": 2.0,
    "memory": "4g",
    "pids": 512,
    "workers": 1,
    "host_site_packages": "/path/to/host/site-packages",
    "runtime_prefix": "/path/to/host/jiuwenswarm",
    "image_python": "/opt/jiuwenswarm-runtime/bin/python",
    "workspace_root": "/path/to/host/sft-workspaces",
    "output_root": "/path/to/host/sft-runs"
  },
  "jiuwenswarm": {
    "python": "/path/to/host/jiuwenswarm/bin/python"
  },
  "eval": {
    "enabled": true,
    "python": "/path/to/host/python/bin/python3",
    "dataset": "data/eval.json"
  }
}
```

`api_version` 可保持 `1.39` 以兼容只支持旧 Docker API 的环境。启动前检查：

```bash
docker info
docker version --format '{{.Server.APIVersion}}'
```

如果当前用户没有 Docker 权限，应将用户加入 Docker 组或按部署环境使用受控的 sudo，
而不是把 Docker socket 挂进 JiuwenSwarm 子容器。`local_docker` 不需要
`yuanrong.endpoint`，但 suite 中保留该字段不会被使用。

### 2.2 运行和检查

先执行不启动模型的配置检查：

```bash
python3 scripts/run_sft_testsuite.py \
  --test-suite my-set --dry-run \
  --set sandbox.backend=local_docker
```

再运行单题 smoke 或小批量：

```bash
python3 scripts/run_sft_testsuite.py \
  --test-suite my-set --limit 1 --smoke \
  --set sandbox.backend=local_docker

python3 scripts/run_sft_testsuite.py \
  --test-suite my-set --limit 5 --workers 5 \
  --set sandbox.backend=local_docker
```

`--set` 只适合覆盖 backend、资源、并发、超时和网络等 operational 字段；模型、密钥、
Gateway、宿主机路径和数据文件必须在 suite 配置中预先准备。

## 第三章：YuanRong API 后端样例

### 3.1 最小配置

YuanRong 后端仍然创建 Docker 类型的 task/eval 沙箱；区别仅在于创建、读写和删除实例
通过 YuanRong Frontend REST 完成，而不是 runner 直接执行 Docker CLI：

```json
{
  "sandbox": {
    "backend": "yuanrong"
  },
  "yuanrong": {
    "endpoint": "http://127.0.0.1:8888",
    "request_timeout_seconds": 300,
    "namespace": "dev"
  },
  "docker": {
    "api_version": "1.39",
    "host_site_packages": "/path/to/host/site-packages",
    "runtime_prefix": "/path/to/host/jiuwenswarm",
    "image_python": "/opt/jiuwenswarm-runtime/bin/python",
    "workspace_root": "/path/to/host/sft-workspaces",
    "output_root": "/path/to/host/sft-runs"
  },
  "jiuwenswarm": {
    "python": "/path/to/host/jiuwenswarm/bin/python"
  },
  "eval": {
    "enabled": true,
    "python": "/path/to/host/python/bin/python3",
    "dataset": "data/eval.json"
  }
}
```

`yuanrong.endpoint` 的同机默认值是 `http://127.0.0.1:8888`。如果 Frontend 在另一台
主机或容器中，替换成 runner 能访问的地址；如果 Frontend 在容器中，容器内的
`127.0.0.1` 不是宿主机地址，应该使用正确的服务名或 host-gateway 地址。

YuanRong Frontend 所在的执行面必须能够访问 Docker daemon，并且能读取
`host_site_packages`、`runtime_prefix`、workspace 和 output。请求 payload 的
`runtime_spec.sandbox_type` 会设置为 `docker`，每个 case 与 eval 都是独立实例；正常完成
后 Skill 会删除实例。

### 3.2 运行和检查

先确认 Frontend 可达（具体健康接口以部署版本为准）：

```bash
curl --fail http://127.0.0.1:8888/api/agent
python3 scripts/run_sft_testsuite.py --test-suite my-set --dry-run
```

然后运行一题，再运行并发小批量：

```bash
python3 scripts/run_sft_testsuite.py \
  --test-suite my-set --limit 1 --smoke

python3 scripts/run_sft_testsuite.py \
  --test-suite my-set --limit 5 --workers 5
```

遇到 `DeadlineExceeded`，优先检查 `host_site_packages` 是否真的包含 `yr`、YuanRong
executor 是否能读取两个 runtime mount、workspace/output 是否对 executor 可见，以及
SWE 镜像和当前 CPU 架构是否匹配。遇到 Gateway 连接失败，分别从 runner 和新建的
Docker 容器测试 `gateway.host_url` 与 `gateway.container_url`，不要在容器内使用指向
容器自身的 `127.0.0.1`。

无论使用哪种后端，Web prompt 只应传 suite 名（例如 `mini5` 或 `mini50`）、case 范围和
并发数；不要在 prompt 中传 API key、模型 API 地址、Gateway 地址或宿主机路径。
