from typing import List
from openjiuwen.core.utils.llm.messages import BaseMessage


def build_model_input(messages: List[BaseMessage],
                      history_messages: List[BaseMessage] | str,
                      prompt: str) -> List[dict]:
    history = ""
    if isinstance(history_messages, str):
        history = history_messages
    elif isinstance(history_messages, List):
        if history_messages and len(history_messages) > 0:
            for msg in history_messages:
                history += f"{msg.role}: {msg.content}\n"
    conversation = ""
    for msg in messages:
        conversation += f"{msg.role}: {msg.content}\n"
    model_input = [{
        "role": "system",
        "content": prompt
    }]

    user_input = ""
    if history != "":
        user_input += "如果当前输入与历史消息有关联，可参考历史消息(historical_messages)，历史消息如下：\n" + history + "\n"
    user_input += "现在开始：请根据设定的规则处理以下输入(current_messages)并生成出输出：\n" + conversation
    model_input.append({
        "role": "user",
        "content": user_input
    })
    return model_input
