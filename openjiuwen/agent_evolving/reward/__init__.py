"""Reward: score an agent's delivered work. Output: one standard JSON (meta, total, categories, items)
and a Markdown reply rendered from it.

A fixed pipeline of prompted model calls (no agent loop):

    criteria (from the Task alone) -> checks per criterion (read / python / browser)
    -> judge every claim (and comment per category) -> score in code (0-100) -> overall verdict

It reads no trajectory and proposes no changes; fast_attribution reads this result (and the trace) for that. The
two packages do not import each other.

Use from code::

    from openjiuwen.agent_evolving.reward import load_config, reward, render

    config = load_config()                                   # or a dict like config.DEFAULT_CONFIG
    result = asyncio.run(reward(task, ["index.html"], config, txt_response=reply, user_note=note))
    print(render(result))

or from the command line: ``python -m openjiuwen.agent_evolving.reward --help``.
"""

from .config import DEFAULT_CONFIG, load_config
from .pipeline import archive_rubric, reward, text_reward, write_criteria
from .result import SCHEMA_VERSION, render

__all__ = ["DEFAULT_CONFIG", "SCHEMA_VERSION", "archive_rubric", "load_config", "reward", "text_reward",
           "write_criteria", "render"]
