# openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env

The `openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env` module provides Docker container environment management functionality.

---

## class openjiuwen.agent_evolving.evaluator.evaluator_pipeline.docker_env.DockerEnvironment

```
class DockerEnvironment(image_tag: str, container_name: str | None, cpus: int, memory_mb: int, timeout: int)
```

Docker environment management class that encapsulates container build, start, execute, and stop operations.

**Parameters:**

* **image_tag**(str): Docker image tag.
* **container_name**(str, optional): Container name. Default: `None`.
* **cpus**(int, optional): CPU core limit. Default: `1`.
* **memory_mb**(int, optional): Memory limit in MB. Default: `2048`.
* **timeout**(int, optional): Timeout in seconds. Default: `900`.

**Properties:**

### is_running -> bool

Whether the container is running.

### container_id -> str | None

Container ID.

### container_name -> str

Container name.

### build(dockerfile_path: Path, build_context: Path, build_timeout: int, no_cache: bool, build_args: dict[str, str] | None) -> str

Build Docker image.

**Parameters:**

* **dockerfile_path**(Path): Path to Dockerfile.
* **build_context**(Path): Build context path.
* **build_timeout**(int, optional): Build timeout in seconds. Default: `600`.
* **no_cache**(bool, optional): Whether to disable cache. Default: `False`.
* **build_args**(dict[str, str], optional): Build arguments. Default: `None`.

**Returns:**

**str** - image tag.

**Exceptions:**

* **FileNotFoundError**: Dockerfile not found.
* **RuntimeError**: Build failed or timed out.

### async start() -> None

Start container.

**Exceptions:**

* **RuntimeError**: Failed to start container.

### async stop() -> None

Stop and remove container.

### async exec(command: str, timeout: int, workdir: str | None, env: dict[str, str] | None) -> ExecResult

Execute command inside container.

**Parameters:**

* **command**(str): Command to execute.
* **timeout**(int, optional): Command execution timeout in seconds. Default: `300`.
* **workdir**(str, optional): Working directory. Default: `None`.
* **env**(dict[str, str], optional): Environment variables. Default: `None`.

**Returns:**

**ExecResult** - command execution result.

### async copy_to(src: Path, dst: str) -> bool

Copy file to container.

**Parameters:**

* **src**(Path): Local source file path.
* **dst**(str): Destination path inside container.

**Returns:**

**bool** - whether copy was successful.

### async copy_from(src: str, dst: Path) -> bool

Copy file from container to local.

**Parameters:**

* **src**(str): Source path inside container.
* **dst**(Path): Local destination path.

**Returns:**

**bool** - whether copy was successful.

**Example:**

```python
>>> from openjiuwen.agent_evolving.evaluator.evaluator_pipeline import DockerEnvironment
>>> 
>>> # Create Docker environment
>>> env = DockerEnvironment(
...     image_tag="my_image:latest",
...     cpus=2,
...     memory_mb=4096,
... )
>>> 
>>> # Build image
>>> env.build(Path("Dockerfile"), Path("."))
>>> 
>>> # Start container
>>> await env.start()
>>> 
>>> # Execute command
>>> result = await env.exec("echo 'Hello World'")
>>> print(result.stdout)
Hello World
>>> 
>>> # Stop container
>>> await env.stop()
```