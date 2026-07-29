"""派对策划助手 —— 一个最小但完整的 SwarmFlow 演示,覆盖当前支持的全部能力。

场景简单、LLM 只需结构化输出(无需外部工具或复杂推理):

* 无状态 agent :一次性构思派对主题(`await agent(...)`)。
* 有状态 agent :主厨会话(`agent_session` + `.send()`),跨轮记住自己已提的主菜,
  再据此设计甜点 —— 第二轮 prompt 不必重述第一轮的菜单。
* 无状态 human :主办人对最终方案一次性签核(`await human(...)`)。
* 有状态 human :每位嘉宾一个 `human_session` + `.send()`,同一人跨两轮答口味与忌口,
  保留上下文。
* 并发:
  - `pipeline(GUESTS, ask_flavor, ask_avoid)` 流式征询多位嘉宾 —— 两个阶段(问口味 →
    问忌口)**无栅栏**:嘉宾 A 在问忌口(stage2)时,嘉宾 B 可能还在问口味(stage1)。
    每位嘉宾是**独立**会话,stage1 造好会话并随结果传给 stage2,从而跨阶段保留上下文。
  - `parallel([...])` 让两个互不依赖的无状态 agent(歌单 / 布置)并发跑、栅栏汇合。
* 嵌套 workflow:`await workflow(<同目录 invitation_card.py>, {...})` 生成邀请函。

离线 MockBackend 下人类答案也是确定性合成的,整个流程可 `--journal` / `--resume`。

运行:`uv run wf examples/party_planner.py --args "小明的生日"`
"""
from pathlib import Path

from swarmflow import (
    agent,
    agent_session,
    compact,
    human,
    human_session,
    log,
    parallel,
    phase,
    pipeline,
    workflow,
)

# Locate the sibling sub-workflow relative to this script so the nested
# workflow() call resolves regardless of the process working directory.
_INVITATION_CARD = str(Path(__file__).resolve().parent / "invitation_card.py")

META = {
    "name": "party-planner",
    "description": "派对策划:构思→并行征询嘉宾→拟菜单→并发筹备→签核→邀请函,覆盖全部 SwarmFlow 能力",
    "phases": [
        {"title": "构思", "detail": "无状态 agent 构思主题"},
        {"title": "征询嘉宾", "detail": "pipeline 流式征询多位嘉宾(各为有状态 human)"},
        {"title": "拟菜单", "detail": "有状态 agent 跨轮设计主菜与甜点"},
        {"title": "筹备", "detail": "parallel 并发跑歌单与布置两个无状态 agent"},
        {"title": "审批", "detail": "无状态 human 一次性签核"},
        {"title": "邀请函", "detail": "嵌套 workflow 生成邀请函"},
    ],
}

GUESTS = ["小红", "小刚", "阿珍"]  # 三位嘉宾,各自一个有状态 human 会话,并行征询

# ---------- 结构化输出 schema(JSON Schema 字面量)----------
THEME = {
    "type": "object",
    "additionalProperties": False,
    "required": ["theme", "vibe"],
    "properties": {
        "theme": {"type": "string", "description": "派对主题名"},
        "vibe": {"type": "string", "description": "一句话氛围描述"},
    },
}
GUEST_FLAVOR = {
    "type": "object",
    "additionalProperties": False,
    "required": ["flavor"],
    "properties": {"flavor": {"type": "string", "description": "想要的蛋糕口味"}},
}
GUEST_AVOID = {
    "type": "object",
    "additionalProperties": False,
    "required": ["avoid"],
    "properties": {"avoid": {"type": "string", "description": '忌口/过敏食材;没有就填 "无"'}},
}
MENU = {
    "type": "object",
    "additionalProperties": False,
    "required": ["dishes"],
    "properties": {
        "dishes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "3 道派对主菜",
        }
    },
}
DESSERT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["dessert", "reason"],
    "properties": {
        "dessert": {"type": "string", "description": "1 道甜点"},
        "reason": {"type": "string", "description": "为何契合主菜与嘉宾偏好"},
    },
}
STRING_LIST = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {"type": "array", "items": {"type": "string"}, "description": "3 条建议"}
    },
}
APPROVAL = {
    "type": "object",
    "additionalProperties": False,
    "required": ["approved", "note"],
    "properties": {
        "approved": {"type": "boolean"},
        "note": {"type": "string", "description": "一句话批注"},
    },
}


# ---------- pipeline 的两个阶段(签名均为 (prev, item, index))----------
# 每位嘉宾独立流过 stage1→stage2,阶段间无栅栏:这位在问忌口时,那位可能还在问口味。
async def ask_flavor(_prev, name, _i):
    """stage1:为该嘉宾新建一个有状态 human 会话,问口味,并把会话随结果传给下一阶段。"""
    g = human_session(
        label=f"嘉宾·{name}",
        instructions="你是受邀嘉宾,请如实、简短地回答主办方关于饮食偏好的问题。",
    )
    flavor = await g.send("你想要什么口味的生日蛋糕?", schema=GUEST_FLAVOR)
    # 把会话对象往下传,stage2 复用它 → 同一个人跨阶段保留上下文。
    return {"name": name, "session": g, "flavor": flavor["flavor"] if flavor else "不限"}


async def ask_avoid(prev, _name, _i):
    """stage2:复用 stage1 的同一会话(故记得刚才的口味),再问忌口。"""
    g = prev["session"]
    avoid = await g.send("好的。那你有什么忌口或过敏的食材吗?", schema=GUEST_AVOID)
    return {
        "name": prev["name"],
        "flavor": prev["flavor"],
        "avoid": avoid["avoid"] if avoid else "无",
    }


async def run(args):
    occasion = args or "一场生日派对"

    # ===== 构思:无状态 agent(一次性,无需保留上下文)=====
    phase("构思")
    theme = await agent(
        f"为「{occasion}」构思一个派对主题。给出主题名(theme)与一句话氛围描述(vibe)。",
        label="构思主题", phase="构思", schema=THEME,
    )
    theme_name = theme["theme"] if theme else "欢乐派对"
    log(f"派对主题:{theme_name}")

    # ===== 征询嘉宾:pipeline 流式征询多位嘉宾(每位 = 一个有状态 human,跨阶段保留上下文)=====
    phase("征询嘉宾")
    prefs = compact(await pipeline(GUESTS, ask_flavor, ask_avoid))
    fav_summary = "、".join(f"{g['name']}爱{g['flavor']}" for g in prefs)
    avoid_all = "、".join(sorted({g["avoid"] for g in prefs if g["avoid"] and g["avoid"] != "无"})) or "无"
    log(f"嘉宾口味:{fav_summary};需避开:{avoid_all}")

    # ===== 拟菜单:有状态 agent(主厨跨两轮,第二轮记得自己提过的主菜)=====
    phase("拟菜单")
    chef = agent_session(
        label="主厨",
        instructions=f"你是派对主厨,正在为「{theme_name}」设计菜单。回答简洁。",
    )
    menu = await chef.send("请先给出 3 道适合派对的主菜(dishes,字符串数组)。", schema=MENU)
    dessert = await chef.send(
        f"很好。现在基于你上面提的主菜,再设计 1 道甜点(dessert)。"
        f"嘉宾口味偏好「{fav_summary}」,全场需避开忌口「{avoid_all}」,"
        f"请避开这些食材并说明理由(reason)。",
        schema=DESSERT,
    )
    dishes = menu["dishes"] if menu else []
    dessert_name = dessert["dessert"] if dessert else "时令水果拼盘"
    log(f"菜单:主菜={dishes};甜点={dessert_name}")

    # ===== 筹备:parallel 让两个互不依赖的无状态 agent 并发(栅栏汇合)=====
    phase("筹备")
    playlist, decor = await parallel([
        lambda: agent(
            f"为「{theme_name}」派对推荐一个歌单(items,3 首歌名)。",
            label="歌单", phase="筹备", schema=STRING_LIST,
        ),
        lambda: agent(
            f"为「{theme_name}」派对给出现场布置点子(items,3 条)。",
            label="布置", phase="筹备", schema=STRING_LIST,
        ),
    ])
    songs = playlist["items"] if playlist else []
    decorations = decor["items"] if decor else []
    log(f"歌单 {len(songs)} 首,布置点子 {len(decorations)} 条")

    # ===== 审批:无状态 human(主办人一次性签核)=====
    phase("审批")
    decision = await human(
        f"主题「{theme_name}」,主菜 {dishes},甜点「{dessert_name}」。是否批准该方案?",
        label="主办人签核", schema=APPROVAL,
    )
    approved = bool(decision and decision["approved"])
    log(f"主办人{'批准' if approved else '驳回'}了方案")

    # ===== 邀请函:嵌套 workflow(单层)=====
    phase("邀请函")
    card = await workflow(
        _INVITATION_CARD,
        {"theme": theme_name, "date": "本周六晚 7 点", "host": "小明"},
    )

    return {
        "occasion": occasion,
        "theme": theme_name,
        "guests": prefs,
        "avoidAll": avoid_all,
        "menu": dishes,
        "dessert": dessert_name,
        "playlist": songs,
        "decorations": decorations,
        "approved": approved,
        "invitation": card,
    }
