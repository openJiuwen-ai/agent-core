The openJiuwen framework supports multi-dimensional intelligent prompt optimization methods. In intelligent dialogue systems, the quality of prompts directly affects the accuracy, relevance, and stability of large model outputs. To systematically improve prompt effectiveness, openJiuwen provides a progressive prompt optimization mechanism covering the following three core stages:

- Generate prompts: Based on the user’s original prompt, automatically generate structured, semantically clear prompts, realizing the transformation from “fuzzy intent” to “structured instruction.”
- Optimize prompts based on feedback: Based on user-provided feedback, perform targeted optimization on the original prompt to improve prompt quality.
- Optimize prompts based on error cases: Generate targeted improvement strategies based on typical error cases to optimize the original prompt and enhance model performance in edge scenarios.

# Prompt Generation

The prompt generation module aims to transform the user’s initial input into a structured prompt framework. By calling a large language model, this module expands simple original inputs (e.g., “You are a name extraction assistant”) into complete prompts with clear structure, explicit role definitions, task goals, and output specifications. This reduces the barrier to writing prompts, provides a standardized starting template, and removes the “don’t know how to write” obstacle.

Generate the initial prompt via the `MetaTemplateBuilder.build` function

```python
import os
import asyncio

from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.dev_tools.prompt_builder import MetaTemplateBuilder

API_BASE = os.getenv("API_BASE", "https://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

# 1. Create model configuration
model_config = ModelRequestConfig(model=MODEL_NAME)
model_client_config = ModelClientConfig(
    client_provider=MODEL_PROVIDER,
    api_base=API_BASE,
    api_key=API_KEY,
)

# 2. Create MetaTemplateBuilder object
builder = MetaTemplateBuilder(model_config, model_client_config)

# 3. Execute prompt generation (build is an async method)
async def main():
    response = await builder.build(prompt="You are a name information extraction assistant")
    return response

print(asyncio.run(main()))
```
Expected return
```text
## Persona
You are a name information extraction assistant  
Your professional skills include:  
- Accurately identifying and extracting personal names from text  
- Distinguishing real names from nicknames, positions, titles, etc.  
- Supporting multilingual name recognition (e.g., Chinese, English, etc.)  
- Recognizing and handling personal name information in complex contexts  

## Task Description
Your task is to extract all personal name information that appears in the user-provided text and present it clearly. This will help users quickly obtain the personnel information involved in the text for subsequent analysis or organization.  

## Constraints
1. Output according to <Output Format>  
2. Execute step-by-step according to <Execution Steps>  
3. Extract only clear personal names, excluding positions, titles, company names, etc.  
4. If there are no extractable names in the text, output “No personal names found”  

## Execution Steps
1. Read through the user-provided text and understand the overall context  
2. Identify all potential personal names in the text, including Chinese, English, etc.  
3. Exclude non-name information (such as positions, company names, nicknames, etc.)  
4. Organize and output the extraction results according to <Output Format>  

## Output Format
- Output an unordered list, with each item being an extracted personal name  
- Present names in Chinese or English as is, without translation or modification  
- Example format:  
  - Zhang San
  - John Smith
```

# Prompt Optimization Based on Feedback

The initially generated prompt often cannot fully meet practical application needs. The feedback optimization module iteratively improves the prompt through user feedback and test results.

Optimize prompts based on feedback via the `FeedbackPromptBuilder.build` function

```python
import os
import asyncio

from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.dev_tools.prompt_builder import FeedbackPromptBuilder

API_BASE = os.getenv("API_BASE", "https://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

# 1. Create model configuration
model_config = ModelRequestConfig(model=MODEL_NAME)
model_client_config = ModelClientConfig(
    client_provider=MODEL_PROVIDER,
    api_base=API_BASE,
    api_key=API_KEY,
)

# 2. Create FeedbackPromptBuilder object
feedback_builder = FeedbackPromptBuilder(model_config, model_client_config)
# Prompt generated in the previous step
prompt = '''## Persona Define the role or identity you will play: name extraction assistant list the role's...'''

# 3. Execute prompt feedback optimization (build is an async method)
async def main():
    response = await feedback_builder.build(prompt=prompt, feedback="Do not extract people who do not exist in real history")
    return response

print(asyncio.run(main()))
```
Expected return
```text
## Persona Define the role or identity you will play: name extraction assistant, only list characters who actually exist in real history...
```

# Prompt Optimization Based on Error Cases

The error case optimization module focuses on handling edge cases and exceptional scenarios. By analyzing failure cases, it identifies weak points in the prompt and performs targeted improvements.

Execute error case optimization via the `BadCasePromptBuilder.build` function

```python
import os
import asyncio

from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig
from openjiuwen.dev_tools.prompt_builder import BadCasePromptBuilder
from openjiuwen.dev_tools.tune import EvaluatedCase, Case

API_BASE = os.getenv("API_BASE", "https://api.openai.com/v1")
API_KEY = os.getenv("API_KEY", "sk-fake")
MODEL_NAME = os.getenv("MODEL_NAME", "")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "")

# 1. Create model configuration
model_config = ModelRequestConfig(model=MODEL_NAME)
model_client_config = ModelClientConfig(
    client_provider=MODEL_PROVIDER,
    api_base=API_BASE,
    api_key=API_KEY,
)

# 2. Create BadCasePromptBuilder object
badcase_builder = BadCasePromptBuilder(model_config, model_client_config)

BAD_CASES = [
    EvaluatedCase(
        case=Case(
            inputs={"query": "Pan Zhiheng (approx. 1536—1621), courtesy name Jingsheng, style names Luanxiaosheng and Binghuasheng, a native of She County and Yansi, Anhui; he resided in Jinling (present-day Nanjing, Jiangsu)"},
            label={"output": "[PanZhiheng]"},
        ),
        answer={"output": "[PanZhiheng, Binghuasheng]"},
        reason="Mistakenly identified the style name 'Binghuasheng' as a personal name",
    ),
    EvaluatedCase(
        case=Case(
            inputs={"query": "Guo Zaoxing (1532—1593), courtesy name Jianchu, style name Haiyue, from Huananli, Fuqing County, Fujian (now Fuqing City), younger brother of Guo Yuqing"},
            label={"output": "[GuoZaoxing, GuoYuqing]"},
        ),
        answer={"output": "[GuoZaoxing, GuoYuqing, WuYue]"},
        reason="Mistakenly identified the place name 'WuYue' as a personal name",
    ),
]

# 3. Execute prompt error case optimization (build is an async method)
async def main():
    response = await badcase_builder.build(
        prompt="You are a professional personal name information extraction assistant",
        cases=BAD_CASES,
    )
    return response

print(asyncio.run(main()))
```
Expected result
```text
You are a professional personal name information extraction assistant. Please extract the legal names of all real people from the text and return them as a list. A “name” refers to the formal name of a real individual and does not include courtesy names, pen names, place names, region names, organization names, or other non-person entities. Make sure to extract only the legal names that clearly refer to real people.
```