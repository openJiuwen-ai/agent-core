# openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env

`openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env` 模块提供 Docker 容器环境的管理功能。

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env.DockerEnvironment

```
class DockerEnvironment(image_tag: str, container_name: str | None, cpus: int, memory_mb: int, timeout: int)
```

Docker 环境管理类，封装容器的构建、启动、执行和停止操作。

**参数：**

* **image_tag**(str)：Docker 镜像标签。
* **container_name**(str，可选)：容器名称。默认值：`None`。
* **cpus**(int，可选)：CPU 核数限制。默认值：`1`。
* **memory_mb**(int，可选)：内存限制（MB）。默认值：`2048`。
* **timeout**(int，可选)：超时时间（秒）。默认值：`900`。

**属性：**

### is_running -> bool

容器是否正在运行。

### container_id -> str | None

容器 ID。

### container_name -> str

容器名称。

### build(dockerfile_path: Path, build_context: Path, build_timeout: int, no_cache: bool, build_args: dict[str, str] | None) -> str

构建 Docker 镜像。

**参数：**

* **dockerfile_path**(Path)：Dockerfile 路径。
* **build_context**(Path)：构建上下文路径。
* **build_timeout**(int，可选)：构建超时时间（秒）。默认值：`600`。
* **no_cache**(bool，可选)：是否禁用缓存。默认值：`False`。
* **build_args**(dict[str, str]，可选)：构建参数。默认值：`None`。

**返回：**

**str**，镜像标签。

**异常：**

* **FileNotFoundError**：Dockerfile 不存在。
* **RuntimeError**：构建失败或超时。

### async start() -> None

启动容器。

**异常：**

* **RuntimeError**：启动失败。

### async stop() -> None

停止并删除容器。

### async exec(command: str, timeout: int, workdir: str | None, env: dict[str, str] | None) -> ExecResult

在容器内执行命令。

**参数：**

* **command**(str)：要执行的命令。
* **timeout**(int，可选)：命令执行超时时间（秒）。默认值：`300`。
* **workdir**(str，可选)：工作目录。默认值：`None`。
* **env**(dict[str, str]，可选)：环境变量。默认值：`None`。

**返回：**

**ExecResult**，命令执行结果。

### async copy_to(src: Path, dst: str) -> bool

将文件复制到容器内。

**参数：**

* **src**(Path)：本地源文件路径。
* **dst**(str)：容器内目标路径。

**返回：**

**bool**，复制是否成功。

### async copy_from(src: str, dst: Path) -> bool

从容器内复制文件到本地。

**参数：**

* **src**(str)：容器内源文件路径。
* **dst**(Path)：本地目标路径。

**返回：**

**bool**，复制是否成功。

**样例：**

```python
>>> from openjiuwen.agent_evolving.evaluator.evaluator_pipeline import DockerEnvironment
>>> 
>>> # 创建 Docker 环境
>>> env = DockerEnvironment(
...     image_tag="my_image:latest",
...     cpus=2,
...     memory_mb=4096,
... )
>>> 
>>> # 构建镜像
>>> env.build(Path("Dockerfile"), Path("."))
>>> 
>>> # 启动容器
>>> await env.start()
>>> 
>>> # 执行命令
>>> result = await env.exec("echo 'Hello World'")
>>> print(result.stdout)
Hello World
>>> 
>>> # 停止容器
>>> await env.stop()
```