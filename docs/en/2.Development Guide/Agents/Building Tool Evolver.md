# Tool Optimizer (Tool Description Self-Evolution)

`Tool Optimizer` is the tool self-evolution module in openJiuwen agent-core.

Its core goals are:
1. Given a tool description and an executable callable;
2. Automatically generate executable call samples plus instruction/answer samples;
3. Iteratively rewrite the description based on evaluation results;
4. Produce a clearer, more executable, and better-bounded tool description JSON.

## Overview

This module optimizes whether a description helps models call tools correctly, using a two-stage loop:
1. **Example stage**: Generate and filter high-quality calling samples;
2. **Description stage**: Rewrite descriptions using positive/negative samples.

Results are persisted in each round, and finally passed through a reviewer for structuring, deduplication, and consistency checks to produce the final schema-aligned description.

## Quick Start

```python
import os
from dotenv import load_dotenv

from openjiuwen.agent_evolving.optimizer.tool.base import ToolOptimizerBase
from openjiuwen.agent_evolving.optimizer.tool.utils.default_configs import (
    default_config_desc,
    default_config_eg,
)
from openjiuwen.agent_evolving.optimizer.tool.utils.callable_fortest import (
    tool,
    gaode_map_mcp_generic as callable_func,
)

load_dotenv()

optimizer = ToolOptimizerBase(
    max_turns=2,
    config_eg=default_config_eg,
    config_desc=default_config_desc,
    llm_api_key=os.getenv("OPENAI_API_KEY", ""),
    path_save_dir="./tool_optimizer_results",
    tool_name=tool["name"],
)

result = optimizer.optimize_tool(tool, callable_func)
print(result)
```

## Input/Output Contract

### Input 1: `tool`

Minimum required fields:
- `name`: tool name (string)
- `description`: tool description (recommended as a JSON string in OpenAI function schema format)

Recommended `description` shape:
- Includes fields like `type/function/name/description/parameters/required`;
- More accurate parameter descriptions usually make early-round search more stable.

### Input 2: `tool_callable`

Expected signature:
- `tool_callable(arguments: Dict[str, Any]) -> Any`

Notes:
- The wrapper passes the generated function-call object as a whole into the callable (not kwargs expansion);
- Exceptions raised by the callable are recorded as failed-call samples.

### Output: `final_desc`

`optimize_tool` returns the reviewer-processed description JSON (dict). Its structure aligns with the original schema, while textual content is optimized.

## Runtime Flow (`optimize_tool`)

Each round (`max_turns`) runs the following:

1. **Example stage**
- Calls `customized_pipeline("example", ...)`;
- Uses `APICallToExampleMethod + BeamSearch` to generate API calls, user instructions, answers, and scores;
- Writes results to `examples/<tool_name>.json`.

2. **Description stage**
- Calls `customized_pipeline("description", ...)`;
- Uses `ToolDescriptionMethod + BeamSearch` to rewrite descriptions from positive/negative samples;
- Writes results to `descriptions/<tool_name>.json`.

3. **Post-processing stage (final)**
- `ToolDescriptionReviewer.process(steps=["clean", "cross_check", "translate"])`;
- `extract_schema` extracts the original structural template;
- `reviewer.format` remaps optimized text into the schema to produce final output.

## Configuration

Default configs are in `utils/default_configs.py`:
- `default_config_eg`: Example stage
- `default_config_desc`: Description stage

Key parameters:
- `gen_model_id`: generation model (e.g., `gpt-5-mini`)
- `eval_model_id`: evaluation model
- `beam_width`: number of candidates kept per layer
- `expand_num`: number of expanded branches per node
- `max_depth`: search depth
- `top_k`: number of returned candidate paths
- `num_workers`: number of parallel workers
- `num_refine_steps`: instruction refinement steps in Example stage
- `num_feedback_steps`: history window used for reflection/rewrite
- `num_examples_for_desc`: sample count loaded in Description stage
- `score_eval_weight`: whether to add extra evaluation weight (default 0)

Additional `ToolOptimizerBase` parameters:
- `max_turns`: total number of iterations (default 5)
- `llm_api_key`: LLM key
- `path_save_dir`: intermediate result directory (default `./tool_optimizer_results`)
- `tool_name`: naming for negative sample file (`<tool_name>.json`)

## Directory Structure

```text
tool/
├── base.py                           # Optimization entry: ToolOptimizerBase
├── __init__.py
├── readme.md
└── utils/
    ├── default_configs.py            # Default configs
    ├── customized_pipline.py         # Unified two-stage pipeline
    ├── beam_search.py                # Beam Search implementation
    ├── toolcall_example_method.py    # Example-stage method
    ├── description_example_method.py # Description-stage method
    ├── customized_eval.py            # Call accuracy + output effectiveness eval
    ├── customized_api.py             # Callable wrapper
    ├── customized_revivwer.py        # Final description post-processing reviewer
    ├── schema_extractor.py           # Schema extractor
    ├── rits.py                       # LLM call wrapper
    ├── format.py                     # JSON/prompt utilities
    ├── callable_fortest.py           # Sample tool and callable
    ├── draft.json
    └── draft.ipynb
```

## Core Components

### 1) `ToolOptimizerBase` (`base.py`)
- Unified external interface: `optimize_tool`
- Orchestrates Example/Description two stages
- Final reviewer cleanup and schema alignment

### 2) `customized_pipeline` (`utils/customized_pipline.py`)
- Chooses method class by `stage`:
  - `example` -> `APICallToExampleMethod`
  - `description` -> `ToolDescriptionMethod`
- Unified `BeamSearch` integration
- Unified result persistence logic

### 3) `APICallToExampleMethod` (`utils/toolcall_example_method.py`)
- Generates executable function calls from descriptions
- Executes tools to obtain real outputs
- Automatically generates user instructions, answers, and batch reflections
- Produces high-value samples for description optimization

### 4) `ToolDescriptionMethod` (`utils/description_example_method.py`)
- Loads positive (successful) and negative (failed) samples
- Critiques first, then rewrites the tool description
- Re-evaluates description quality via `SimpleEval`

### 5) `SimpleEval` (`utils/customized_eval.py`)
- Evaluates two parts:
  - Function call accuracy (function name + parameter matching)
  - Output effectiveness (LLM scoring or fallback rule)
- Returns `score_avg/score_std/fn_call_accuracy/output_effectiveness`

### 6) `ToolDescriptionReviewer` (`utils/customized_revivwer.py`)
- Cleans redundancy, cross-checks with original description, and translates when needed
- Forces remapping into target schema
- Ensures output structure is directly usable as a tool definition

## Artifacts and Intermediate Files

Default output directory: `./tool_optimizer_results`

- `examples/<tool_name>.json`
  - Search history from Example stage
- `descriptions/<tool_name>.json`
  - Candidate descriptions and scores from Description stage
- `<tool_name>.json` (optional)
  - Externally provided negative sample file (preferred by Description stage)

## Development and Extension Suggestions

1. **Replace models**
- Update `gen_model_id/eval_model_id` in `default_config_eg/default_config_desc`.

2. **Reduce search cost**
- Lower `beam_width/expand_num/max_depth/num_workers`.

3. **Enhance evaluation criteria**
- Adjust `fn_call_weight/output_effectiveness_weight` in `SimpleEval`;
- Or extend scoring prompts in `_evaluate_output_effectiveness`.

4. **Inject external negative samples**
- Put negative samples at `path_save_dir/<tool_name>.json`;
- `ToolDescriptionMethod.get_negative_examples` will prefer this file.

## Prerequisites

1. Python environment can import `openjiuwen`;
2. Available OpenAI-compatible model configuration;
3. Set `OPENAI_API_KEY` (or pass `llm_api_key` in constructor).

Example:

```bash
set OPENAI_API_KEY=your_key
python base.py
```

## Known Limitations

1. The `fn_call_path` branch in `customized_pipeline` is not implemented yet (raises `NotImplementedError`);
2. There is no automated test script in this directory currently; a small smoke test is recommended when onboarding a new tool;
3. This module depends on LLM quality/stability. Fixing model version and controlling randomness is recommended.

## License

Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
