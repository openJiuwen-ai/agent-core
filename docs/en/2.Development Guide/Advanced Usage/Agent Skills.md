# Agent Skills

Agent Skills are modular capability packages that provide an agent with specialized knowledge, workflows, and/or reusable resources. They enable domain-specific task execution while minimizing unnecessary context usage. Unlike general documentation, skills function as concise execution manuals designed for direct agent use.

## Skills Structure

A skill directory consists of:

```swift
skill-name/
├── SKILL.md (required)
├── scripts/ (optional)
├── references/ (optional)
└── assets/ (optional)
```

### SKILL.md (Required)

The SKILL.md file includes:

- YAML headers `name` and `description` (required)
  - `name` is a unique identifier for the skill
  - `description` defines the skill's purpose and lists a few trigger conditions
- The rest of `SKILL.md` body (required), which includes:
  - Practical execution instructions
  - Detailed descriptions of how to utilize the skill
  - References to other script files, reference files, and asset files

### Optional Files

- `scripts/`
  - Contains longer scripts too lengthy to fit within `SKILL.md`
  - Can be executed without being viewed
- `references/`
  - Additional situational/niche knowledge that's loaded only when required
  - E.g. Extended documentation, schemas, or technical material.
- `assets/`
  - Static files designed to be used as-is
  - E.g. templates, images, font files, etc.

## Progressive Disclosure

Skills minimize context consumption through three loading layers:

- **Metadata** (~100 tokens)
  - `name` and `description` in the YAML header of `SKILL.md`
  - Always present in context
  - For skill trigger detection
- **Rest of the `SKILL.md` body**
  - Loaded only when the skill is triggered
  - Contains concise operational guidance
- **Referenced Resources** 
  - Accessed only when explicitly required
  - Prevents unnecessary context expansion

This layered approach ensures efficient context usage while preserving full capability depth. This design keeps the base prompt small while preserving access to deeper knowledge when needed.

# Agent Skills Example - Image Resize

This example uses Agent Skills to resize an image. This example demonstrates how to:
- Set up the Environment
- Create a ReActAgent
- Register Agent Skills to the ReActAgent from a local directory
- Run the ReActAgent

## Environment

Set up three directories: `skills`, `inputs`, `outputs`. Then, inside `.env`, set up:

- `SKILLS_DIR`: the file-path to the `skills` directory
- `FILES_BASE_DIR`: the file-path to the `inputs` directory
- `OUTPUT_DIR`: the file-path to the `outputs` directory

**Put the image you wish to resize inside the `inputs` directory.**

(Note that the `inputs` and `outputs` directories can be the same.)

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

- `skills_dir`: The directory containing Agent Skills folders
- `files_base_dir`: The directory containing user-provided files
  - E.g. `image.png`, an image to resize
- `output_dir`: The directory that the agent outputs to
- `max_iterations`: the maximum number of ReAct cycles for the agent. Default `10` cycles.

## Creating ReActAgent

Refer to **Agents/Build ReActAgent.md** for a more detailed walkthrough on creating ReActAgents. 

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

SysOperation controls the file system, code execution, and shell system. There is currently only one OperationMode - `LOCAL`. `OperationMode.LOCAL` runs code, executes commands, and creates files locally. 

```python
async def main():
    # ...

    sysop_card = SysOperationCard(
        mode=OperationMode.LOCAL,
        work_config=LocalWorkConfig(work_dir=None),
    )
    Runner.resource_mgr.add_sys_operation(sysop_card)
```

Then, create the ReActAgent config. Refer to **Agents/Build ReActAgent.md** for more information on ReActAgent configurations.

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

## Register Skills

### Set up the Skills Directory

Create a `skills` folder if it doesn't already exist. Set the file path to the `skills` folder as `SKILLS_DIR` in `.env`.

### Automatically download skills from GitHub

You can use remote skills registration to directly download skills from GitHub to the local skills folder. 

**Note**: While it is possible to use `register_remote_skills` without a GitHub token, there is a small limit to the number of API calls per hour.

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

See [the section on remote skills](#remote-skills) for more details on how to use remote skills. (Such as how to create a GitHub token)

### Add downloaded skills to ReActAgent

Call `agent.register_skill` on the skill directory to register the downloaded skills to the ReActAgent.

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

### Manual skill installation

Alternatively, you can manually add skill directories to the skills directory. Download the [image_resizer](https://github.com/dreamofapsychiccat/remote-skills-test/tree/main/skills) skill directory from GitHub. Ensure you move the entire `image_resizer` folder into the skills directory specified by `SKILLS_DIR` in `.env`.

Then, call `register_skill` on the skills directory to register the skills:

```python
async def main():
    # ...

    if skills_dir.exists():
        await agent.register_skill(str(skills_dir))
```


### Register Tools

To utilize Agent Skills, the ReActAgent needs access to the following additional tools:

- `view_file`: Views the file content of a file
- `execute_python_code`: Runs Python code
- `run_command`: Runs a bash command in a Terminal/Shell

Registering a skill automatically adds these tools to the agent.

## Run Agent

Run the agent by calling `Runner`'s `run_agent()` function with the agent and query as input:

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

## Complete Code Example using Agent Skills

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

# Other Features

## <a id="remote-skills"></a>Downloading and Registering Skills from GitHub

`agent.register_remote_skills()` takes 3 inputs:

- `skills_dir`: The location to download the skill to
- `GitHubTree`: An object denoting a directory tree on GitHub
  - `repo_owner`: The owner of the GitHub repo
  - `repo_name`: The name of the GitHub repo
  - `tree_ref`: The hash of a directory within the repo, or "HEAD" for the root of the repo. When in doubt, use "HEAD".
  - `directory`: Relative file-path with respect to `tree_ref`. Searches all files within `directory` for SKILL.md files and downloads their directories.
- `github_token`: A GitHub token capable of accessing public repositories.

To use remote skill registration, generate a GitHub token for viewing **Public repositories**. Navigate to [GitHub's token creation page](https://github.com/settings/personal-access-tokens) to create an access token. Alternatively navigate to GitHub → Settings → Developer Settings → Fine-grained tokens. Save the token in `.env` under the name `GITHUB_TOKEN`.

### Examples

To download the `image_resizer` skill from `dreamofapsychiccat`'s `remote-skills-test` GitHub repo, specify the path to the `image_resizer` skill as the `directory`:

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

To download all skills from the `skills` directory on `dreamofapsychiccat`'s `remote-skills-test` GitHub repo:

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

**Warning**: GitHub search has a limit of 100,000 files (and a 7 MB size limit). If the repository contains more than 100,000 files and directories, the GitHub search will be truncated. Try repeating the search with a more specific `directory` parameter.

## Skill Creation

The `SkillCreator` class is an agent capable of creating skill directories. Given information in the form of a file (e.g. a `.md` file), the agent calls upon the `skill_creation` skill to write a skill directory.

### Environment

Within the `.env`, follow these configurations:

- `SKILLS_DIR`: Either leave it empty (the default skill directory contains the `skill_creation` skill), or put the `skill_creation` skill inside the folder.
- `FILES_BASE_DIR`: Put the reference document for the skill here. (Alternatively, download a reference document from online)
- `OUTPUT_DIR`: The directory to put the generated skills directory in. 

In addition, ensure the standard LLM environment configurations (`API_BASE`, `API_KEY`, `MODEL_NAME`, and `MODEL_PROVIDER`) are present.

### Skill Creation Workflow

To use the `SkillCreator`:

- Create a `SkillCreator` instance.
- Initialize the ReActAgent within `SkillCreator`
- Run the `SkillCreator` via `generate(query, output_dir)`
  - `query`: The query for the agent. Specify you wish to create a skill and the location of any reference documents within the query. Note that including reference documents isn't strictly necessary, but is useful in creating more focused and detailed skills.
  - `output_dir`: The agent will create the skill folder within the `output_dir` directory.

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

### Skill Creation Full Example

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
