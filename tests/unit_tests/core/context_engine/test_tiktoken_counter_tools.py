import pytest


def test_tiktoken_counter_count_tools_supports_pydantic_model_class():
    pytest.importorskip("tiktoken")

    from pydantic import BaseModel, Field

    from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter
    from openjiuwen.core.foundation.tool import ToolInfo

    class Args(BaseModel):
        q: str = Field(default="")

    counter = TiktokenCounter(model="gpt-4")
    n = counter.count_tools([ToolInfo(name="search", description="d", parameters=Args)])
    assert isinstance(n, int) and n > 0
