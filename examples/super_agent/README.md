# Super Agent Usage

## Setup
1. Install [uv](https://docs.astral.sh/uv/) if it is not already on your PATH.
2. From the repo root run `uv sync` to create/update the virtual environment with the pinned dependencies in `uv.lock`.
3. Run `uv pip install tiktoken` separately
4. Configure your LLM credentials in a .env script:
   - `API_BASE`
   - `API_KEY`
   - `MODEL_NAME`
   - `MODEL_PROVIDER`

## Usage
- Run the primary Super Agent demo (covers tool usage, context management, streaming, token accounting, and sub-agent delegation):
  ```
  uv run python examples/super_agent/test/super_react_agent_example.py
  ```
  The script prints the progress of each scenario and exits once all seven demonstrations complete.

- Run the Super Agent demo with MCP coverage: 
  ```
  uv run python examples/super_agent/test/super_react_agent_example_mcp.py
  ```
  Do note that you will need MCP servers to run in order to run this example. The MCP servers need to be SSE and not stdio.
  Optionally, feed the output into a .txt file. 
  e.g. uv run python examples/super_agent/test/super_react_agent_example_mcp.py >> example_result.txt

- In order to run batches of datapoints with MCP (e.g. for GAIA dataset):
1. Provide a JSONL task file to your own harness (see `examples/super_agent/data/test.jsonl`)
2. Run the Super Agent test run script that allows for running multiple questions.
  ```
  uv run python examples/super_agent/test/super_react_agent_test_run.py
  ```
  Optionally, feed the output into a .txt file. 
  e.g. uv run python examples/super_agent/test/super_react_agent_example_mcp.py >> example_result.txt