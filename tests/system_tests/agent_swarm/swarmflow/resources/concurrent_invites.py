"""并发子工作流演示:用 parallel 同时发起多个嵌套 workflow。

为多位嘉宾各生成一张邀请函,每位对应一次 `workflow(invitation_card.py)` 嵌套调用,
通过 `parallel` **并发**发起。这验证 swarmflow 支持在脚本里用并发原语并发执行子工作流:
各子工作流相互独立、互不阻塞、都能跑完,不会因嵌套深度守卫被误跳过(返回 None)。

子工作流路径用 `__file__` 定位同目录的 `invitation_card.py`,与进程工作目录无关。

运行:`uv run wf concurrent_invites.py`
"""
from pathlib import Path

from swarmflow import log, parallel, workflow

# 同目录的子工作流,按 __file__ 定位(与 cwd 无关)。
_INVITATION_CARD = str(Path(__file__).resolve().parent / "invitation_card.py")

META = {
    "name": "concurrent-invites",
    "description": "并发生成多张邀请函:parallel 同时跑多个 invitation_card 嵌套 workflow",
    "phases": [{"title": "邀请函", "detail": "parallel 并发发起多个子工作流"}],
}

# (主题, 主办人) —— 每对触发一个并发子工作流。
INVITES = [
    ("海洋探险派对", "小明"),
    ("森林奇遇派对", "小红"),
    ("星空露营派对", "阿珍"),
]


async def run(args):
    # 每个 thunk 用默认参数绑定当前循环变量(避免推导式里的晚绑定 footgun),
    # parallel 并发发起全部子工作流并在栅栏处汇合。
    cards = await parallel([
        (lambda theme=t, host=h: workflow(
            _INVITATION_CARD,
            {"theme": theme, "date": "本周六晚 7 点", "host": host},
        ))
        for t, h in INVITES
    ])
    ok = [c for c in cards if c]
    log(f"并发生成 {len(ok)}/{len(INVITES)} 张邀请函")
    return {"requested": len(INVITES), "cards": cards}
