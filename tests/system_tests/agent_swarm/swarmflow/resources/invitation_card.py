"""子工作流:根据派对主题生成一段邀请函文案。

被 `party_planner.py` 通过 `await workflow("examples/invitation_card.py", {...})`
嵌套调用,演示 SwarmFlow 的 workflow 嵌套能力(单层)。它本身也是一个合法的、
可独立运行的 SwarmFlow 文件。

运行:`uv run wf examples/invitation_card.py`
"""
from swarmflow import agent, log

META = {
    "name": "invitation-card",
    "description": "根据派对主题与日期生成一段简短邀请函文案",
    "phases": [{"title": "邀请函"}],
}

CARD = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "body"],
    "properties": {
        "title": {"type": "string", "description": "邀请函标题,一行"},
        "body": {"type": "string", "description": "邀请函正文,2-3 句,热情友好"},
    },
}


async def run(args):
    args = args or {}
    theme = args.get("theme", "生日派对")
    date = args.get("date", "本周六晚 7 点")
    host = args.get("host", "主办人")

    card = await agent(
        f"请为一场主题为「{theme}」的派对写一张邀请函。时间:{date},主办人:{host}。"
        "给出一行标题(title)与 2-3 句热情友好的正文(body)。",
        label="写邀请函", phase="邀请函", schema=CARD,
    )
    log(f"邀请函已生成:{card['title'] if card else '(生成失败)'}")
    return card
