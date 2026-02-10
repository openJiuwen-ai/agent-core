# Agent Skills

Agent Skills 是模块化的能力包，为 Agent 提供特定领域的知识、工作流和/或可复用资源。它们使 Agent 能够执行领域特定的任务，同时尽量减少不必要的上下文消耗。与通用文档不同，Skill 更像是为 Agent 直接执行而设计的精简操作手册

## Skills 结构

一个 Skill 目录包含如下结构：

```swift
skill-name/
├── SKILL.md (必需)
├── scripts/ (可选)
├── references/ (可选)
└── assets/ (可选)
```

### SKILL.md（必需）

SKILL.md 文件包含：

- YAML 头部字段 `name` and `description` (必需)
  - `name` ：skill的唯一标识符
  - `description` ：定义skill用途，并列出若干触发条件
- SKILL.md 的正文内容（必需），包括
  - 实际执行指引
  - 如何使用该 skill 的详细说明
  - 对 script 文件、reference 文件和 asset 文件的引用

### 可选文件说明

- `scripts/`
  - 存放不适合直接写进 SKILL.md 的较长脚本
  - 可以在不查看脚本内容的情况下直接执行
- `references/`
  - 仅在需要时才加载的补充 / 场景化知识
  - 例如：扩展文档、schema、技术材料
- `assets/`
  - 设计为可直接使用的静态文件
  - 例如：template、image、font 文件等

## 渐进式披露

Agent Skills 通过 三层加载机制 来最小化上下文消耗

- **元数据层** (~100 tokens)
  - 来自 `SKILL.md` YAML 头部的 `name` 和 `description`
  - 始终保留在上下文中
  - 用于 skill 触发判断
- **`SKILL.md` 正文**
  - 仅在 skill 被触发时才加载
  - 包含精炼的操作与执行指导
- **引用资源** 
  - 仅在明确需要时才访问
  - 防止上下文无谓膨胀

这种分层设计在保持完整能力深度的同时，最大限度降低了基础 Prompt 的体量

# Agent Skills 示例 - 图片缩放

该示例展示了如何使用 Agent Skills 对图片进行缩放，涵盖以下内容：

- 环境配置
- 创建一个 ReActAgent
- 从本地目录向 ReActAgent 注册 Agent Skills
- 运行 ReActAgent

## 环境

请准备三个目录： `skills`, `inputs`, `outputs`. 并在 `.env`文件中配置:

- `SKILLS_DIR`:  `skills` 目录的路径
- `FILES_BASE_DIR`: `inputs` 目录的路径
- `OUTPUT_DIR`: `outputs` 目录的路径

**将你需要缩放的图片放入 `inputs` 目录中**

(注意：`inputs` 和 `outputs` 目录可以是同一个。)

```python
import os
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.skills import GitHubTree
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig

async def main():
    # Load environment
    load_dotenv()

    skills_dir = Path(os.getenv("SKILLS_DIR", "")).expanduser().resolve()
    files_base_dir = os.getenv("FILES_BASE_DIR", str(Path(__file__).resolve().parent))
    output_dir = os.getenv("OUTPUT_DIR", "")
    max_iterations = int(os.getenv("MAX_ITERATIONS", 10))
```

- `skills_dir`: 包含 Agent Skills 各个文件夹的目录
- `files_base_dir`: 包含用户提供文件的目录
  - 例如 `image.png`, 一张需要 resize 的图片
- `output_dir`: agent 输出文件的目录
- `max_iterations`: agent 的 ReAct 循环最大次数，默认 10 次循环

## 创建 ReActAgent

更详细的 ReActAgent 创建流程请参考：**智能体/构建ReActAgent.md** 

```python

async def main():
    # ...

    api_base = os.getenv("API_BASE", "")
    api_key = os.getenv("API_KEY", "")
    model_name = os.getenv("MODEL_NAME", "")
    model_provider = os.getenv("MODEL_PROVIDER", "")
    verify_ssl = os.getenv("LLM_SSL_VERIFY", "False")
    # Note: Set LLM_SSL_VERIFY to "True" during production

    # Create ReActAgent instance
    agent = ReActAgent(card=AgentCard(name="skill_agent", description="Skill Agent"))

    # Create ReActAgent's configurations
    system_prompt = (
        "You are an intelligent assistant.\n"
        f"All user-provided files are located at '{files_base_dir}'\n"
        f"Put all generated files into {output_dir}\n"
    )
```

SysOperation 用于控制文件系统、代码执行以及 shell 系统。目前仅支持一种 OperationMode - `LOCAL`. `OperationMode.LOCAL` 表示在本地环境中运行代码、执行命令并创建文件。

```python
async def main():
    # ...

    sysop_card = SysOperationCard(
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=None),
    )
    Runner.resource_mgr.add_sys_operation(sysop_card)
```

接下来，创建 ReActAgent 的配置。有关 ReActAgent 配置的更多信息，请参考 **智能体/构建ReActAgent.md** 

```python
async def main():
    # ...

    cfg = (ReActAgentConfig()
           .configure_model_client(
                provider=model_provider,
                api_key=api_key,
                api_base=api_base,
                model_name=model_name,
                verify_ssl=verify_ssl,
            )
           .configure_prompt_template([{"role": "system", "content": system_prompt}])
           .configure_max_iterations(max_iterations)
           .configure_context_engine(
                max_context_message_num=None,
                default_window_round_num=None
           )
        )
    cfg.sys_operation_id = sysop_card.id
    agent.configure(cfg)
```

## 注册 Skills

### 设置 Skills Directory

如果还没有 `skills` 文件夹，请先创建一个。然后在 `.env` 里将 `skills` 文件夹的路径设置为 `SKILLS_DIR`。

### 从 GitHub 自动下载 skills

你可以使用 remote skills registration，直接从 GitHub 下载 skills 到本地的 skills 文件夹。

**注意**: 虽然不使用 GitHub token 也可以调用 `register_remote_skills`，但每小时可用的 API 调用次数会有较小的限制。

```python
github_token = os.getenv("GITHUB_TOKEN", "")

# Download image_resizer skill from GitHub
await agent.register_remote_skills(
    skills_dir=skills_dir, 
    github_tree=GitHubTree(
        repo_owner="dreamofapsychiccat",
        repo_name="remote-skills-test",
        tree_ref="HEAD",
        directory="skills/image_resizer",
    ), 
    token=github_token
)
```

更多关于如何使用 remote skills（例如如何创建 GitHub token）的细节，请参见 [remote skills 章节](#remote-skills)

### 将已下载的 skills 添加到 ReActAgent

对 skill 目录调用 `agent.register_skill`，即可将已下载的 skills 注册到 ReActAgent 中。

```python
async def main():
    # ...
    
    if skills_dir.exists():
        github_token = os.getenv("GITHUB_TOKEN", "")
        
        # Download image_resizer skill from GitHub
        await agent.register_remote_skills(
            skills_dir=skills_dir, 
            github_tree=GitHubTree(
                repo_owner="dreamofapsychiccat",
                repo_name="remote-skills-test",
                tree_ref="HEAD",
                directory="skills/image_resizer",
            ), 
            token=github_token
        )

        await agent.register_skill(str(skills_dir))
```

### skill 手动安装

你也可以选择手动安装 skills，将 skill 目录直接添加到 skills 目录中。  
从 GitHub 下载 [image_resizer](https://github.com/dreamofapsychiccat/remote-skills-test/tree/main/skills) skill 目录，并确保将整个 `image_resizer` 文件夹移动到 `.env` 中 `SKILLS_DIR` 指定的 skills 目录下。

然后，对 skills 目录调用 `register_skill`，以注册这些 skills：

```python
async def main():
    # ...

    if skills_dir.exists():
        await agent.register_skill(str(skills_dir))
```


### 注册工具

为了使用 Agent Skills，ReActAgent 需要额外具备以下 tools 的访问权限：

- `view_file`：查看文件内容
- `execute_python_code`：执行 Python 代码
- `run_command`：在 Terminal / Shell 中执行 bash 命令

注册 skill 时会自动将这些 tools 添加到 agent 中。

## 运行 Agent

通过调用 `Runner` 的 `run_agent()` 函数，并传入 agent 和 query 作为参数来运行 agent：

```python
async def main():
    # ...

    # Run the agent
    query = f"Downscale the provided image inside the {files_base_dir} directory by 2x."

    res = await Runner.run_agent(
        agent=agent,
        inputs={"query": query, "conversation_id": "492"},
    )
    logger.info(res.get("output", res))
```

## 使用 Agent Skills 的完整代码示例

```python
import os
import asyncio
from pathlib import Path

from dotenv import load_dotenv
from openjiuwen.core.common.logging import logger
from openjiuwen.core.runner import Runner
from openjiuwen.core.skills import GitHubTree
from openjiuwen.core.sys_operation import SysOperationCard, OperationMode, LocalWorkConfig
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent import ReActAgent, ReActAgentConfig


async def main():
    # Load environment
    load_dotenv()

    skills_dir = Path(os.getenv("SKILLS_DIR", "")).expanduser().resolve()
    files_base_dir = os.getenv("FILES_BASE_DIR", str(Path(__file__).resolve().parent))
    output_dir = os.getenv("OUTPUT_DIR", "")
    max_iterations = int(os.getenv("MAX_ITERATIONS", 10))

    api_base = os.getenv("API_BASE", "")
    api_key = os.getenv("API_KEY", "")
    model_name = os.getenv("MODEL_NAME", "")
    model_provider = os.getenv("MODEL_PROVIDER", "")
    verify_ssl = os.getenv("LLM_SSL_VERIFY", "False")

    # Create ReActAgent instance
    agent = ReActAgent(card=AgentCard(name="skill_agent", description="Skill Agent"))

    # Create ReActAgent's configurations
    system_prompt = (
        "You are an intelligent assistant.\n"
        f"All user-provided files are located at '{files_base_dir}'\n"
        f"Put all generated files into {output_dir}\n"
    )

    sysop_card = SysOperationCard(
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=None),
    )
    Runner.resource_mgr.add_sys_operation(sysop_card)

    cfg = (ReActAgentConfig()
           .configure_model_client(
                provider=model_provider,
                api_key=api_key,
                api_base=api_base,
                model_name=model_name,
                verify_ssl=verify_ssl,
            )
           .configure_prompt_template([{"role": "system", "content": system_prompt}])
           .configure_max_iterations(max_iterations)
           .configure_context_engine(
                max_context_message_num=None,
                default_window_round_num=None
           )
        )
    cfg.sys_operation_id = sysop_card.id
    agent.configure(cfg)

    # Add skills to the agent
    if skills_dir.exists():
        github_token = os.getenv("GITHUB_TOKEN", "")

        # Download image_resizer skill from GitHub
        await agent.register_remote_skills(
            skills_dir=skills_dir, 
            github_tree=GitHubTree(
                repo_owner="dreamofapsychiccat",
                repo_name="remote-skills-test",
                tree_ref="HEAD",
                directory="skills/image_resizer",
            ), 
            token=github_token
        )
        
        await agent.register_skill(str(skills_dir))

    # Run the agent
    query = f"Downscale the provided image inside the {files_base_dir} directory by 2x."

    res = await Runner.run_agent(
        agent=agent,
        inputs={"query": query, "conversation_id": "492"},
    )
    logger.info(res.get("output", res))


if __name__ == "__main__":
    asyncio.run(main())
```

# 其他功能

## <a id="remote-skills"></a>从 GitHub 下载并注册 Skills

`agent.register_remote_skills()` 接收 3 个输入参数：

- `skills_dir`：skill 下载到的本地目录路径  
- `GitHubTree`：用于描述 GitHub 上某个目录树的对象  
  - `repo_owner`：GitHub 仓库的所有者  
  - `repo_name`：GitHub 仓库名称  
  - `tree_ref`：仓库中某个目录的 hash，或使用 `"HEAD"` 表示仓库根目录。不确定时建议使用 `"HEAD"`  
  - `directory`：相对于 `tree_ref` 的目录路径。系统会在该目录下搜索所有包含 `SKILL.md` 的子目录，并下载对应的 skill 目录  
- `github_token`：用于访问公共仓库的 GitHub token  

要使用 remote skill registration，需要创建一个可访问 **Public repositories** 的 GitHub token。  
请前往 [GitHub token 创建页面](https://github.com/settings/personal-access-tokens) 创建访问 token，  
或通过 GitHub → Settings → Developer Settings → Fine-grained tokens 进行创建。  

创建完成后，请将 token 以 `GITHUB_TOKEN` 的名称保存到 `.env` 文件中。

### 示例

例如，要从 `dreamofapsychiccat` 的 `remote-skills-test` GitHub 仓库中下载 `image_resizer` skill，可以将 `image_resizer` skill 的路径指定为 `directory`：

```python
skills_dir = os.getenv("SKILLS_DIR", "")
github_token = os.getenv("GITHUB_TOKEN", "")

await agent.register_remote_skills(
    skills_dir=skills_dir, 
    github_tree=GitHubTree(
        repo_owner="dreamofapsychiccat",
        repo_name="remote-skills-test",
        tree_ref="HEAD",
        directory="skills/image_resizer",
    ), 
    token=github_token
)
```

要从 `dreamofapsychiccat` 的 `remote-skills-test` GitHub 仓库中的 `skills` 目录下载所有 skills：

```python
skills_dir = os.getenv("SKILLS_DIR", "")
github_token = os.getenv("GITHUB_TOKEN", "")

await agent.register_remote_skills(
    skills_dir=skills_dir, 
    github_tree=GitHubTree(
        repo_owner="dreamofapsychiccat",
        repo_name="remote-skills-test",
        tree_ref="HEAD",
        directory="skills", # Not just the image_resizer directory
    ), 
    token=github_token
)
```
**警告**：GitHub 搜索存在 100,000 个文件的数量限制（以及 7 MB 的大小限制）。  
如果仓库中包含超过 100,000 个文件和目录，GitHub 搜索结果将被截断。  
请尝试使用更具体的 `directory` 参数重新执行搜索。

## Skill 构建

`SkillCreator` 类是一个用于创建 skill 目录的 agent。  
当输入以文件形式提供的信息（例如 `.md` 文件）时，该 agent 会调用 `skill_creation` skill 来生成一个完整的 skill 目录。

### 环境配置

在 `.env` 文件中，请进行以下配置：

- `SKILLS_DIR`：可以留空（默认的 skill 目录中已包含 `skill_creation` skill），  
  或者将 `skill_creation` skill 放入该目录中  
- `FILES_BASE_DIR`：将 skill 的参考文档放在该目录中  
  （也可以在运行时从线上下载参考文档）  
- `OUTPUT_DIR`：用于存放生成的 skill 目录的输出路径  

此外，请确保标准的 LLM 环境配置已设置完成，包括：  
`API_BASE`、`API_KEY`、`MODEL_NAME` 和 `MODEL_PROVIDER`。

### Skill 构建流程

使用 `SkillCreator` 的流程如下：

- 创建一个 `SkillCreator` 实例  
- 在 `SkillCreator` 内部初始化 ReActAgent  
- 通过 `generate(query, output_dir)` 运行 `SkillCreator`
  - `query`：agent 的输入指令。需要在 query 中说明你希望创建一个 skill，并指定任何参考文档的位置。  
    注意：参考文档并非必须，但有助于生成更聚焦、更详细的 skills  
  - `output_dir`：agent 会在该目录下创建对应的 skill 文件夹

```python
async def main():
    load_dotenv()
    files_base_dir = os.getenv("FILES_BASE_DIR", str(Path(__file__).resolve().parent))
    output_dir = os.getenv("OUTPUT_DIR", "")

    # Create SkillCreator instance
    skill_creator = SkillCreator()
    # Initialize the agent within SkillCreator
    await skill_creator.create_agent()

    # Run the SkillCreator
    query = "Create a skill based on the file {file-name-here}."
    res = await skill_creator.generate(query, output_dir)
    logger.info(res.get("output", res))
```

### Skill 构建完整示例

```python
import asyncio
import os
from pathlib import Path
import requests

from dotenv import load_dotenv
import markitdown

from openjiuwen.core.common.logging import logger
from openjiuwen.dev_tools.skill_creator.skill_creator import SkillCreator


# Download a PDF file, then convert the PDF to a Markdown file
def download_file_as_markdown(url, files_base_dir):
    filename = url.split("/")[-1]
    os.makedirs(files_base_dir, exist_ok=True)

    pdf_path = files_base_dir / filename
    md_path = pdf_path.with_suffix(".md")

    response = requests.get(url, stream=True)
    response.raise_for_status()

    with pdf_path.open("wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    logger.info("PDF downloaded: " + str(pdf_path))

    md = markitdown.MarkItDown()
    result = md.convert(str(pdf_path))

    with md_path.open("w", encoding="utf-8") as f:
        f.write(result.text_content)

    logger.info("Markdown saved: " + str(md_path))
    return md_path


async def main():
    # Load environment
    load_dotenv()
    files_base_dir = os.getenv("FILES_BASE_DIR", str(Path(__file__).resolve().parent))
    output_dir = os.getenv("OUTPUT_DIR", "")

    skill_creator = SkillCreator()
    await skill_creator.create_agent()

    # Download a manual to base the skill off of.
    url = "http://viewer.media.bitpipe.com/1253203751_753/1284482743_310/11_Best_Practices_for_Peer_Code_Review.pdf"
    files_base_path = Path(files_base_dir)
    md_path = download_file_as_markdown(url, files_base_path)
    
    # Generate skill
    query = f"Create a skill based on the file {md_path}."
    res = await skill_creator.generate(query, output_dir)
    logger.info(res.get("output", res))

if __name__ == "__main__":
    asyncio.run(main())
```