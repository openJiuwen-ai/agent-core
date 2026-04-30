# coding: utf-8

from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent
from openjiuwen.harness.tools.base_tool import ToolOutput


def test_tool_message_content_omits_multimodal_payload() -> None:
    result = ToolOutput(
        success=True,
        data={
            "content": "Image file read: /tmp/a.png",
            "multimodal": [
                {
                    "type": "image",
                    "data_url": "data:image/png;base64,abc",
                }
            ],
        },
    )

    assert AbilityManager._build_tool_message_content(result) == "Image file read: /tmp/a.png"


def test_react_agent_builds_multimodal_user_message_from_tool_result() -> None:
    result = ToolOutput(
        success=True,
        data={
            "content": "Image file read: /tmp/a.png",
            "multimodal": [
                {
                    "type": "image",
                    "source_path": "/tmp/a.png",
                    "data_url": "data:image/png;base64,abc",
                }
            ],
        },
    )

    messages = ReActAgent._build_multimodal_tool_result_messages(result)

    assert len(messages) == 1
    assert messages[0].role == "user"
    assert messages[0].content[0]["type"] == "text"
    assert messages[0].content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,abc"},
    }
