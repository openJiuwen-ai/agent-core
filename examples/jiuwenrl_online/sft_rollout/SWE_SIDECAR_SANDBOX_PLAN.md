# SWE Sidecar 沙箱方案

## 目标

把每个 SWE 用例的原始镜像继续作为主镜像使用，再额外启动一个 sidecar 辅助镜像。

sidecar 的职责只有三件事：

1. 安装并运行 jiuwenswarm / agent-core。
2. 接收训练相关环境变量，驱动主镜像里的 SWE 用例执行。
3. 采集轨迹并上传到 gateway / scheduler。

主镜像不需要安装 jiuwenswarm，不需要改 SWE 用例镜像本身。

## 方案总览

### 总体结构

每个 case 对应两个容器：

- 主容器：SWE 用例镜像，保留原始 repo、依赖和测试环境。
- sidecar 容器：轻量 Python 镜像，包含 jiuwenswarm、agent-core 和必要运行时。

两者通过同一个 Docker network 互通。sidecar 读取任务元数据后，去驱动主容器执行命令、读写文件、生成 patch，并把轨迹上传到 gateway。

### 推荐的 V1 运行方式

V1 推荐用 `docker exec` 作为主容器执行后端。

原因：

- 实现最简单。
- 不需要修改 SWE 镜像。
- 不需要 monkeypatch jiuwenswarm。
- sidecar 和主容器职责清晰。

代价：

- 需要 sidecar 能访问 Docker API 或 docker socket。
- 权限比纯网络方案更高。

### 后续可选增强

如果后面要收紧权限，可以把主容器里的命令执行改成一个很小的 helper 服务：

- 主容器启动后附带一个极简命令入口。
- sidecar 通过 HTTP 或 websocket 调用 helper。
- sidecar 不再直接依赖 Docker socket。

这个增强版适合后续做安全收敛，不作为第一版主路径。

## sidecar 镜像构建模式

需要两种构建模式。

### 1. 开发态

目的：直接基于本地开发中的 jiuwenswarm / agent-core 代码构建 sidecar 镜像。

构建特点：

- Dockerfile 基础镜像尽量简单，优先用 `python:3.11-slim` 或同等级 slim 镜像。
- 通过 build context 把本地源码拷进镜像。
- 用 `pip install -e` 或等价方式安装本地代码。

适用场景：

- 开发调试。
- 验证最新代码改动。
- 快速迭代 sidecar runtime。

### 2. 发布态

目的：基于官方发布包构建 sidecar 镜像。

构建特点：

- 基础镜像仍然保持简洁。
- 只通过官方 pip 源安装发布版本的 jiuwenswarm / agent-core。
- 通过版本号和 index URL 固化依赖。

适用场景：

- CI / 发布验证。
- 生产环境。
- 需要稳定可复现的镜像。

## 建议的 Dockerfile 形态

### Dockerfile.dev

```dockerfile
FROM python:3.12
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/workspace/agent-core:/workspace/jiuwenclaw
WORKDIR /workspace/agent-core
COPY agent-core/ /workspace/agent-core/
COPY jiuwenclaw/ /workspace/jiuwenclaw/
```

### Dockerfile.release

```dockerfile
FROM python:3.12
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/workspace/agent-core:/workspace/jiuwenclaw
WORKDIR /workspace/agent-core
COPY agent-core/ /workspace/agent-core/
COPY jiuwenclaw/ /workspace/jiuwenclaw/
```

说明：

- 这两个 Dockerfile 只负责把当前源码封到 sidecar 镜像里，不负责把 Python 运行时依赖重新打包。
- 运行时通过挂载宿主机 `/data1/lll/miniconda3`，再激活 `openjiuwen-sft` / `openjiuwen-rl` 来提供实际依赖。
- 这版实现的 dev / release 差异，主要留给后续替换为“本地源码 COPY”与“pip 发布包安装”两条构建链。

## 运行时契约

sidecar 启动时，建议显式传入这些信息：

- `SFT_TASK_PROMPT`
- `SFT_DOCKER_IMAGE`
- `SFT_INSTANCE_ID`
- `SFT_DATASET_CASE_JSON`
- `TRAJECTORY_GATEWAY_URL`
- `TRAJECTORY_GATEWAY_API_KEY`
- `API_BASE`
- `API_KEY`
- `MODEL_NAME`
- `TARGET_CONTAINER_NAME`
- `TARGET_WORKDIR`
- `TARGET_EXEC_MODE`

其中：

- 实际落地版没有单独的 sidecar helper API，controller sidecar 直接调用现有 `run_swe_task_rollout.py`。
- 控制器进程通过挂载宿主机的 Docker socket 和 `/usr/bin/docker`，继续使用当前 rollout 后端。
- `openjiuwen-sft` 通过挂载宿主机 conda 根目录在 sidecar 容器内激活。

## 执行流程

1. 按 SWE 数据集镜像拉起主容器。
2. 拉起 sidecar 容器，并注入任务元数据和 gateway / supervisor 配置。
3. sidecar 读取 case 的 prompt 和镜像信息。
4. sidecar 读取当前任务 case，调用现有 rollout 脚本启动 SWE 容器。
5. SWE 容器内的 jiuwenswarm 生成轨迹并上传。
6. gateway 接收轨迹，scheduler 后续消费训练数据。

## 与现有 rollout 后端的关系

当前已有两条路：

- `docker`：主容器内直接挂载本地代码并执行 jiuwenswarm。
- `local_repo`：跳过 SWE 镜像，直接在宿主机 checkout repo。

sidecar 方案应该作为第三条后端：

- `docker_sidecar`

它的定位是：

- 保留 SWE 主镜像。
- 把 jiuwenswarm 移出主镜像。
- 把训练采集逻辑单独放在 sidecar。

## 推荐实施顺序

1. 先补 sidecar 构建脚本，支持 dev / release 两种模式。
2. 再把 controller sidecar 和现有 rollout CLI 对齐。
3. 如果后续要更严格隔离，再把主容器执行层抽成 helper。
4. 最后接入 SFT 轨迹上传链路并做 E2E 验证。

## 风险点

- Docker socket 权限较高，需要明确只在开发 / 受控环境启用。
- controller sidecar 需要宿主机 `/data1/lll` 的整棵挂载，路径不能乱改。
- dev 模式的本地源码构建上下文需要显式 staging，不然 Docker build 读不到兄弟目录。
- 运行时还是依赖宿主机 conda 环境，image 本身只是源码壳。

## 结论

这个方案的核心思路是：

- 主镜像保持原样。
- sidecar 负责启动和调度。
- 开发态用本地源码 COPY。
- 运行时用宿主机 conda + Docker socket。

先按这个最小可用实现把链路跑通，再考虑把 helper 和发布态安装链路补完整。
