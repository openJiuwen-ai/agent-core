"""Reward pipeline: models scripted, real workspace, real python checks and a real offline browser."""

import asyncio
import json
from pathlib import Path

import pytest

from openjiuwen.agent_evolving.reward import __main__ as cli
from openjiuwen.agent_evolving.reward import checks, evidence_agent, guides, models, pipeline, scoring, workspace
from openjiuwen.agent_evolving.reward import progress as progress_mod
from openjiuwen.agent_evolving.reward import result as result_mod

GAME = """<button id="go">Go</button><p id="score">0</p><script>
var score = 0; var best = Number(localStorage.getItem('best') || 0);
document.getElementById('go').onclick = () => { score++; document.getElementById('score').textContent = score; };
fetch('https://example.com/x').catch(() => {});
</script>"""




class _Chunk:
    def __init__(self, content=None, reasoning=None):
        delta = type("Delta", (), {"content": content, "reasoning_content": reasoning})()
        self.choices = [type("Choice", (), {"delta": delta})()]


class _Stream:
    def __init__(self, parts, stall_after=None):
        self.parts, self.stall_after, self.closed = list(parts), stall_after, False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.stall_after is not None and not self.stall_after:
            await asyncio.sleep(60)
        if self.stall_after:
            self.stall_after -= 1
        if not self.parts:
            raise StopAsyncIteration
        return self.parts.pop(0)

    async def close(self):
        self.closed = True


def _model(streams, **config):
    model = models.Model.__new__(models.Model)
    model.name, model.extra, model.deadline = "m", {}, None
    model.quota = config.get("timeout", 30)
    model.first_chunk = model.idle = config.get("idle_timeout", 0.2)
    calls = []

    async def create(**kwargs):
        calls.append(kwargs)
        return streams.pop(0)

    completions = type("C", (), {"create": staticmethod(create)})()
    model.client = type("Client", (), {"chat": type("Chat", (), {"completions": completions})()})()
    return model, calls


@pytest.mark.asyncio
async def test_stream_keeps_only_the_answer_and_closes():
    stream = _Stream([_Chunk(reasoning="thinking..."), _Chunk('{"a"'), _Chunk(": 1}")])
    model, calls = _model([stream])
    assert await model.json("sys", "user") == {"a": 1}
    assert calls[0]["stream"] is True and stream.closed


@pytest.mark.asyncio
async def test_stalled_stream_is_cut_by_idle_timeout_and_retried_once():
    stalled = _Stream([_Chunk(reasoning="..."), _Chunk('{"a": 1}')], stall_after=1)
    model, calls = _model([stalled, _Stream([_Chunk('{"a": 2}')])])
    assert await asyncio.wait_for(model.json("sys", "user"), 5) == {"a": 2}
    assert len(calls) == 2 and stalled.closed


@pytest.mark.asyncio
async def test_call_quota_covers_the_retry_too():
    model, calls = _model([_Stream([], stall_after=0), _Stream([], stall_after=0)], timeout=0.3, idle_timeout=10)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(model.json("sys", "user"), 5)


def test_score_weights_floor_and_proactive_challenge_and_caps_a_failed_floor():
    criterion = {"claims": [{"level": "floor"}, {"level": "floor"}, {"level": "challenge"}, {"level": "challenge"}]}
    assert scoring.score(criterion, ["met", "met", "met", "met"]) == 100
    assert scoring.score(criterion, ["met", "met", "partial", "partial"]) == 75     # floor met, challenge half
    assert scoring.score(criterion, ["met", "met", "partial", "unmet"]) == 62
    assert scoring.score(criterion, ["met", "unmet", "met", "met"]) == 25     # broken floor caps
    assert scoring.score(criterion, ["met", "unknown", "met", "unknown"]) == 100    # unknown claims are left out
    assert scoring.score(criterion, ["met", "met", "unknown", "unknown"]) == 100    # no challenge settled: floor only
    assert scoring.score(criterion, ["unknown"] * 4) is None  # nothing was seen: unscored, not 0
    assert scoring.score({"claims": [{"level": "floor"}] * 4}, ["met"] * 4) == 100   # e.g. the promises
    only_challenge = {"claims": [{"level": "challenge"}] * 4}
    assert scoring.score(only_challenge, ["met"] * 4) == 100                     # not capped at the challenge share
    assert scoring.score(only_challenge, ["met", "met", "partial", "unmet"]) == 62
    assert scoring.total([{"score": 75}, {"score": 50}, {"score": None}]) == 62




def test_the_result_is_standard_json_and_the_reply_is_rendered_from_it():
    results = [{"id": "scope", "name": "统计范围", "goal": "只统计当天并说明取舍", "score": 70, "summary": "范围对，取舍没逐条说明。",
                "claims": [{"text": "只统计当天工单。", "level": "floor", "status": "met", "reason": "只有当天"},
                           {"text": "逐条说明排除原因。", "level": "challenge", "status": "partial", "reason": "只写了总数"}]},
               {"id": "look", "name": "画面质量", "goal": "像出版物", "score": None, "summary": "",
                "critique": [{"issue": "头和身体分离", "severity": "major"}],
                "claims": [{"text": "主体清楚", "level": "floor", "status": "unknown", "reason": ""}]}]
    meta = {"deliverable": ["daily.md"], "final_reply": True, "inputs": ["a.csv"],
            "models": {"producer": "m1", "judge": "m1"}}
    results[0]["claims"][1]["source"] = "feedback"
    result = result_mod.build("统计当天工单", results, 70, "整体合格，取舍说明不足。", meta)
    assert result["schema_version"] == "reward/3" and result["total"] == {
        "score": 70, "summary": "整体合格，取舍说明不足。"}
    scope, look = result["categories"]
    assert scope == {"category_id": "scope", "category_name": "统计范围", "rubric": "只统计当天并说明取舍",
                     "comment": "范围对，取舍没逐条说明。", "score": 70, "issues": []}
    assert look["issues"] == ["头和身体分离"] and look["comment"] == ""      # major visual weaknesses, as text
    assert result["sub_category_items"][1] == {
        "sub_category_id": "scope#1", "category_id": "scope", "category_name": "统计范围",
        "rubric": "逐条说明排除原因。", "comment": "只写了总数", "score": 50, "status": "partial", "type": "proactive_challenge",
        "gain": 30.0, "from_user_note": True, "from_process_signal": False}   # met would make it 100 (given 70)
    assert [i["score"] for i in result["sub_category_items"]] == [100, 50, None]
    assert "50%" in result["meta"]["scoring"] and "最多 25 分" in result["meta"]["scoring"] and result["meta"]["evaluated_at"]
    json.dumps(result)                                                          # plain JSON
    text = result_mod.render(result, "/r.json")
    for piece in ("**总分 70 / 100**\n整体合格，取舍说明不足。",
                  "交付物 daily.md", "最终回复", "输入材料 1 个", "模型 m1",
                  "1. 统计范围 **70 / 100**：只统计当天并说明取舍", "   评价：范围对，取舍没逐条说明。",
                  "   - ◐ 逐条说明排除原因。（用户建议）——只写了总数", "2. 画面质量 **无证据**", "   - 画面问题：头和身体分离",
                  "计分：", "详细报告：/r.json"):
        assert piece in text, piece
    items = text.split("计分：")[0]                                              # the scoring rule may name the two kinds
    assert "只统计当天工单。" not in items and "基础项" not in items and "挑战项" not in items   # met items hidden; no split


def test_config_yaml_placeholders_are_filled_from_the_environment_or_jiuwenswarm_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# keys\nBAILIAN_KEY='from-file'\nexport OTHER=x\n")
    monkeypatch.delenv("BAILIAN_KEY", raising=False)
    assert models.expand("${BAILIAN_KEY}", env) == "from-file"
    monkeypatch.setenv("BAILIAN_KEY", "from-env")
    assert models.expand("Bearer ${BAILIAN_KEY}", env) == "Bearer from-env"
    assert models.expand("${MISSING_X:-fallback}", env) == "fallback" and models.expand("plain", env) == "plain"
    with pytest.raises(ValueError, match="MISSING_X"):
        models.expand("${MISSING_X}", env)


def test_session_model_comes_from_the_session_and_jiuwenswarm_config(tmp_path, monkeypatch):
    session = tmp_path / "agent/sessions/web_1"
    session.mkdir(parents=True)
    (session / "history.jsonl").write_text("\n".join(json.dumps(r) for r in [
        {"event_type": "context.usage", "model": "qwen3.8-flash"},
        {"event_type": "context.usage", "model": "deepseek-v4-flash"}]))
    (tmp_path / "config").mkdir()
    (tmp_path / "config/config.yaml").write_text("""models:
  defaults:
    - model_client_config: {api_base: https://q.example/v1, api_key: kq, model_name: qwen3.8-flash}
    - model_client_config: {api_base: https://d.example, api_key: kd, model_name: deepseek-v4-flash}
""")
    monkeypatch.setenv("JIUWENSWARM_HOME", str(tmp_path))
    config = models.resolve_models({"producer": "session",
                                         "judge": {"model": "qwen3.8-flash", "base_url": "u", "api_key": "k"}})
    assert config["producer"] == {"model": "deepseek-v4-flash", "base_url": "https://d.example", "api_key": "kd",
                                  "extra_body": {"thinking": {"type": "enabled"}, "reasoning_effort": "low"}}
    assert config["judge"]["base_url"] == "u"                                  # fixed judge left alone
    judge = models.model_config({"model": "session", "timeout": 100})
    assert judge["model"] == "deepseek-v4-flash" and judge["timeout"] == 100


def test_progress_file_moves_through_the_stages(tmp_path, monkeypatch):
    session = tmp_path / "agent/sessions/web_1"
    session.mkdir(parents=True)
    (session / "history.jsonl").write_text("")
    monkeypatch.setenv("JIUWENSWARM_HOME", str(tmp_path))
    progress = progress_mod.Progress.for_session("text")
    files = list((session / "reward").glob("*.json"))
    assert len(files) == 1 and progress.path == files[0]

    def read():
        return json.loads(files[0].read_text())

    assert (read()["stage"], read()["percent"], read()["execution"]) == ("criteria", 0, "running")
    progress.stage("judging", total=4)
    assert read()["percent"] == 10
    progress.tick("counts")
    progress.tick("grounded")
    assert (read()["done"], read()["percent"], read()["detail"]) == (2, 45, "grounded")
    progress.stage("attribution")                     # set by a caller that runs attribution after scoring
    assert read()["percent"] == 80
    progress.finish(64)
    assert (read()["execution"], read()["percent"], read()["score"]) == ("finished", 100, 64)
    progress.fail("boom")
    assert read()["execution"] == "failed" and read()["error"] == "boom"


def test_progress_without_a_session_is_a_no_op(tmp_path, monkeypatch):
    monkeypatch.setenv("JIUWENSWARM_HOME", str(tmp_path / "missing"))
    progress = progress_mod.Progress.for_session("browser")
    progress.stage("evidence", total=2)
    progress.tick("x")
    assert progress.path is None and progress.state["percent"] == 45


@pytest.mark.asyncio
async def test_vision_probe_is_cached_per_model_and_config_can_decide(tmp_path, monkeypatch):
    monkeypatch.setattr(models, "vision_cache_path", lambda: tmp_path / ".vision.json")
    asked = []

    def model(name, answer=None, vision=None, error=None):
        m = models.Model.__new__(models.Model)
        m.name, m.vision = name, vision

        async def ask(system, user, images=()):
            asked.append(name)
            if error:
                raise error
            return answer
        m.json = ask
        return m

    assert await models.can_see(model("qwen-vl", {"color": "red"})) is True
    assert await models.can_see(model("qwen-vl", {"color": "blue"})) is True          # cached
    assert await models.can_see(model("deepseek", error=RuntimeError("image_url not supported"))) is False
    assert await models.can_see(model("guesser", {"color": "blue"})) is False
    assert await models.can_see(model("forced", vision=True)) is True                  # no probe
    assert asked == ["qwen-vl", "deepseek", "guesser"]



def test_config_yaml_is_read_without_pyyaml_too(monkeypatch):
    text = """models:
  defaults:
    - model_client_config:
        api_base: https://q.example/v1
        api_key: "kq"
        model_name: qwen3.8-flash
      model_config_obj:
        context_window: 262144
    - model_client_config:
        api_base: https://d.example
        api_key: kd
        model_name: deepseek-v4-flash
"""
    import sys
    with_yaml = models.jiuwenswarm_models(text)
    monkeypatch.setitem(sys.modules, "yaml", None)
    without = models.jiuwenswarm_models(text)
    assert [(c["model_name"], c["api_base"], c["api_key"]) for c in without] == [
        ("qwen3.8-flash", "https://q.example/v1", "kq"), ("deepseek-v4-flash", "https://d.example", "kd")]
    assert [c["model_name"] for c in with_yaml] == ["qwen3.8-flash", "deepseek-v4-flash"]


# ---------------------------------------------------------------- the generic pipeline

PAGE_CRITERIA = {"criteria": [
    {"id": "click-scores", "scope": "terminal", "goal": "Clicking scores",
     "claims": [{"text": "A click raises the score by one", "level": "floor"},
                {"text": "Scoring has rich feedback", "level": "challenge"}]},
    {"id": "honest", "scope": "process", "goal": "Reply matches the work",
     "claims": [{"text": "Reply matches the files", "level": "floor"},
                {"text": "No failing calls", "level": "challenge"}]}]}


@pytest.fixture(autouse=True)
def scripted_evidence_unless_asked(request, monkeypatch):
    """Most tests script the evidence: instead of the DeepAgent, the scripted model is asked once per criterion for
    its checks ("You plan checks"), which then really run. Tests marked real_agent run the DeepAgent."""
    if request.node.get_closest_marker("real_agent"):
        return

    async def one(criterion, ctx, producer):
        plan = await producer.json("You plan checks (test stand-in for the evidence agent)",
                                   json.dumps({**ctx["plan_input"], "criterion": criterion}, ensure_ascii=False))
        evidence, images = [], []
        for number, check in enumerate([c for c in models.items(plan, "checks") if isinstance(c, dict)], 1):
            try:
                facts, shots = await checks.run_check(check, ctx["root"], ctx["tools"], number)
            except Exception as error:  # noqa: BLE001
                facts, shots = {"could_not_run": f"{type(error).__name__}: {error}"}, []
            evidence.append({"check": number, **facts})
            images += [(f"check {number}, {label}", data) for label, data in shots]
        return evidence, images

    async def scripted(criteria, ctx, route, log, rounds=12, seconds=300):
        producer = models.Model(ctx["config"]["producer"])

        async def timed(criterion):
            try:
                return await asyncio.wait_for(one(criterion, ctx, producer), seconds)
            except asyncio.TimeoutError:
                return [{"could_not_run": "checks ran out of time"}], []
        found = await asyncio.gather(*(timed(c) for c in criteria))
        return {c["id"]: f for c, f in zip(criteria, found)}

    monkeypatch.setattr(evidence_agent, "gather", scripted)


@pytest.fixture(autouse=True)
def isolated_guides(tmp_path, monkeypatch):
    """Only the bundled guides, whatever lives in the user's folder."""
    monkeypatch.setenv("REWARD_HOME", str(tmp_path / "reward_home"))


def dispatcher(answers, seen):
    """Answer by system prompt, since calls for different criteria run concurrently."""
    async def answer(self, system, user, images=()):
        seen.append((system.split("\n", 1)[0], user, [label for label, _ in images]))
        for start, reply in answers:
            if system.startswith(start):
                return reply(user) if callable(reply) else reply
        raise AssertionError("unexpected prompt: " + system[:60])
    return answer


def install(monkeypatch, answers, seen, name="judge-model", vision=True):
    answers = [*answers, ("You read an agent's final reply", {"claims": []}),
               ("You write the overall verdict", {"summary": "Scripted evaluation summary."})]
    monkeypatch.setattr(models.Model, "__init__",
                        lambda self, config: self.__dict__.update(name=name, vision=vision, quota=360))
    monkeypatch.setattr(models.Model, "json", dispatcher(answers, seen))


JUDGED = {"claims": [{"index": 0, "status": "met", "reason": "seen"},
                     {"index": 1, "status": "partial", "reason": "plain", "next": "add a pop"}]}


@pytest.mark.asyncio
async def test_a_page_is_played_in_the_browser_and_judged_with_comments(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    (tmp_path / "index.html").write_text(GAME)
    seen = []
    install(monkeypatch, [
        ("You write evaluation criteria", PAGE_CRITERIA),
        ("You plan checks", {"checks": [{"tool": "browser", "page": "index.html", "note": "click once",
                                         "snapshots": [0], "actions": [{"type": "click", "selector": "#go"},
                                                                       {"type": "probe", "script": "() => ({score, best})"}]}]}),
        ("You judge each claim of one criterion about an agent's delivered work", {**JUDGED, "summary": "能玩，反馈平淡。"}),
        ("You are an art director", {"critique": [], "claims": [], "summary": "No scripted visual verdict."}),
        ("You write the overall verdict", {"summary": "可玩但朴素。"}),
    ], seen)
    root = workspace.make_workspace([tmp_path / "index.html"])
    report = await pipeline.score_workspace("Make a click game", root, {"producer": {}, "include_evidence": True},
                                   txt_response="Done.")
    check = report["evidence"]["click-scores"][0]
    assert check["tool"] == "browser" and check["actions"][1]["value"] == {"score": 1, "best": 0}
    assert not any("SecurityError" in e for e in check["page_errors"])         # real origin, localStorage works
    judged = [x for x in seen if x[0].startswith("You judge each claim of one criterion about an agent's delivered")]
    assert judged[0][2] == ["check 1, loaded", "check 1, after action 0"]       # screenshots reach the judge
    assert "Make a click game" in judged[0][1]                                  # claims are read with the Task in mind
    assert "<button" not in seen[0][1]                                         # criteria see the Task only
    assert [c["category_id"] for c in report["categories"]] == ["click-scores", "visual"]   # no process criterion; a game is looked at
    page = report["categories"][0]
    assert page["score"] == 75 and page["comment"] == "能玩，反馈平淡。"
    assert report["total"]["summary"] == "可玩但朴素。"
    assert report["meta"]["tools"] == {"available": ["read", "python", "browser"], "used": ["browser"]}
    assert "suggestions" not in report and "improvements" not in report      # proposing is attribution's job
    verdict = next(user for system, user, _ in seen if system.startswith("You write the overall verdict"))
    assert "能玩，反馈平淡。" in verdict


@pytest.mark.asyncio
async def test_a_browser_check_can_open_the_file_from_disk_like_a_double_click(tmp_path):
    pytest.importorskip("playwright")
    (tmp_path / "index.html").write_text(GAME)
    probe = {"type": "probe", "script": "() => location.protocol"}
    from_disk, _ = await checks.run_experiment(tmp_path / "index.html", {"via": "file", "actions": [probe]})
    served, _ = await checks.run_experiment(tmp_path / "index.html", {"actions": [probe]})
    assert from_disk["opened_as"] == "file://" and from_disk["actions"][0]["value"] == "file:"
    assert served["opened_as"] == "http://" and served["actions"][0]["value"] == "http:"


@pytest.mark.asyncio
async def test_a_closeup_is_a_sharp_screenshot_of_part_of_an_element(tmp_path):
    pytest.importorskip("playwright")
    (tmp_path / "index.html").write_text(GAME)
    result, shots = await checks.run_experiment(tmp_path / "index.html", {"actions": [
        {"type": "closeup", "selector": "body", "region": [0, 0, 0.5, 0.5]}]})
    assert result["actions"][0]["ok"] and [label for label, _ in shots] == ["loaded", "close-up at action 0"]
    result, shots = await checks.run_experiment(tmp_path / "index.html", {"actions": [{"type": "snapshot"}]})
    assert result["actions"][0]["ok"] and [label for label, _ in shots] == ["loaded", "after action 0"]
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "p1.png").write_bytes(b"png")
    folder, _ = await checks.run_check({"tool": "read", "path": "pages"}, tmp_path, ["read"])
    assert "p1.png" in folder["output"]                                    # a folder is listed, not an error


@pytest.mark.asyncio
async def test_drag_and_wheel_reach_the_page(tmp_path):
    pytest.importorskip("playwright")
    (tmp_path / "view.html").write_text("""<canvas id=c width=400 height=300></canvas><script>
        const c = document.getElementById('c'); window.log = []; let down = false;
        c.onmousedown = () => { down = true; }; c.onmouseup = () => { down = false; window.log.push('drag'); };
        c.onmousemove = () => { if (down) window.moved = (window.moved || 0) + 1; };
        c.onwheel = e => window.log.push('wheel ' + Math.sign(e.deltaY));</script>""")
    result, _ = await checks.run_experiment(tmp_path / "view.html", {"actions": [
        {"type": "drag", "selector": "#c", "from": [0.3, 0.5], "to": [0.8, 0.5]},
        {"type": "wheel", "selector": "#c", "dy": -200},
        {"type": "probe", "script": "() => [window.log, window.moved > 2]"}]})
    assert [a["ok"] for a in result["actions"]] == [True, True, True]
    assert result["actions"][2]["value"] == [["drag", "wheel -1"], True]


@pytest.mark.asyncio
async def test_a_library_pinned_on_a_cdn_loads_and_other_network_stays_blocked(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    fetched = []
    monkeypatch.setattr(checks, "cdn_file", lambda url: fetched.append(url) or b"export const REVISION = '160';")
    (tmp_path / "game.html").write_text("""<script type="importmap">{"imports": {"three":
        "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js"}}</script>
        <script type="module">import {REVISION} from 'three'; window.rev = REVISION;
        fetch('https://cdn.jsdelivr.net/npm/three@latest/build/three.module.js').then(() => window.latest = 'loaded',
                                                                                   () => window.latest = 'blocked');</script>""")
    result, _ = await checks.run_experiment(tmp_path / "game.html", {"actions": [
        {"type": "wait", "ms": 500}, {"type": "probe", "script": "() => [window.rev, window.latest]"}]})
    assert result["actions"][1]["value"] == ["160", "blocked"]                # pinned: served; @latest: not
    assert fetched == ["https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js"]
    assert checks.PINNED_CDN.match("https://cdnjs.cloudflare.com/ajax/libs/three.js/0.160.0/three.min.js")
    assert not checks.PINNED_CDN.match("https://evil.example/npm/three@0.160.0/x.js")


@pytest.mark.asyncio
async def test_a_page_with_a_chinese_file_name_loads_and_a_missing_page_is_a_failed_check(tmp_path):
    pytest.importorskip("playwright")
    (tmp_path / "一键跳跳猫.html").write_text(GAME)
    result, _ = await checks.run_experiment(tmp_path / "一键跳跳猫.html", {"actions": [
        {"type": "probe", "script": "() => document.title"}]})
    assert result["actions"][0]["ok"] and not any("404" in e for e in result["page_errors"])
    with pytest.raises(RuntimeError, match="did not load: HTTP 404"):
        await checks.run_experiment(tmp_path / "missing.html", {"actions": []})


def test_visual_criteria_are_recognised_in_code_and_list_replies_are_accepted():
    written = [{"id": "visual-design", "scope": "terminal", "name": "视觉表现与风格", "goal": "可爱", "claims": [
                   {"text": "有耳朵", "level": "floor"}]},
               {"id": "core", "scope": "terminal", "name": "核心玩法", "goal": "能玩", "claims": []}]
    marked = pipeline.ensure_visual(written, "做一个跳跳猫游戏")
    assert marked[0]["kind"] == "visual" and "kind" not in marked[1] and len(marked) == 2
    added = pipeline.ensure_visual([{"id": "core", "scope": "terminal", "name": "核心玩法", "claims": []}], "做一个跳跳猫游戏")
    assert added[-1]["id"] == "visual" and added[-1]["name"] == "画面质量"
    report_task = pipeline.ensure_visual([{"id": "data", "scope": "terminal", "name": "数据准确", "claims": []}],
                                         "整理工单日报，输出 Markdown")
    assert [c["id"] for c in report_task] == ["data"]                             # not everything is looked at
    named = pipeline.ensure_visual([{"id": "flow", "scope": "terminal", "name": "界面与流程", "claims": []},
                                    {"id": "chart", "scope": "terminal", "name": "Smart chart data", "claims": []},
                                    {"id": "look", "scope": "terminal", "name": "界面美观", "claims": []}], "整理工单日报")
    assert [c.get("kind") for c in named] == [None, None, "visual"]              # UI flow and charts are run, not looked at
    text_answer = pipeline.ensure_visual([{"id": "clarity", "scope": "terminal", "name": "表达清晰", "kind": "visual",
                                           "claims": [{"text": "通顺", "level": "floor"}]}], "算出平均销量并给一条建议")
    assert "kind" not in text_answer[0] and len(text_answer[0]["claims"]) == 1   # nothing to look at: judged as text
    assert models.items([{"tool": "read"}], "checks") == [{"tool": "read"}]
    assert models.items({"checks": [1]}, "checks") == [1] and models.items("oops", "checks") == []


@pytest.mark.asyncio
async def test_metric_guides_are_scanned_and_the_fitting_ones_used(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "metrics").mkdir(parents=True)
    (home / "metrics/game_feel.md").write_text("# Game feel guide\nControls, feedback, progression.")
    (home / "metrics/sql.md").write_text("keywords: sql, database\n# SQL guide")
    monkeypatch.setenv("REWARD_HOME", str(home))
    chosen, undecided = guides.by_keywords("帮我做一本小朋友绘本", {})
    assert [n for n, _ in chosen] == ["picture_book"] and [n for n, _ in undecided] == ["game_feel"]
    assert [n for n, _ in guides.by_keywords("做一个跳跳猫小游戏", {})[0]] == ["game"]      # bundled, by keyword
    assert guides.by_keywords("整理工单日报", {})[0] == []
    assert [n for n, _ in guides.by_keywords("帮我安排一下这个周六", {})[0]] == ["personal_assistant"]
    assert [n for n, _ in guides.by_keywords("做一个贪吃蛇小游戏", {})[0]] == ["game"]
    assert "Words and pictures work together" in chosen[0][1] and not chosen[0][1].startswith("keywords")

    class Picker:
        async def json(self, system, user):
            assert system.startswith("You choose which evaluation guides") and "game_feel" in user
            return {"guides": ["game_feel"]}
    assert [n for n, _ in await guides.for_task("做一个跳跃小游戏", {}, Picker())] == ["game", "game_feel"]
    assert [n for n, _ in await guides.for_task("整理工单日报", {})] == []          # no model: unlabelled ones skipped
    extra = tmp_path / "extra.md"
    extra.write_text("keywords: 工单\n# Ticket guide")
    assert [n for n, _ in await guides.for_task("整理工单日报", {"metric_guides": [str(extra)]})] == ["extra"]


@pytest.mark.asyncio
async def test_the_chosen_guide_reaches_the_criteria_writer_and_the_reply(tmp_path, monkeypatch):
    monkeypatch.setenv("REWARD_HOME", str(tmp_path / "none"))
    seen = []
    install(monkeypatch, [
        ("You write evaluation criteria", {"criteria": [PAGE_CRITERIA["criteria"][0]]}),
        ("You plan checks", {"checks": []}),
        ("You judge each claim", JUDGED),
        ("You are an art director", {"critique": [], "claims": []}),
    ], seen)
    root = workspace.make_workspace(texts={"book.md": "# Mia"})
    report = await pipeline.score_workspace("写一本绘本 picture book", root, {"producer": {}})
    writer = [s for s in seen if s[0].startswith("You write evaluation criteria")][0]
    assert "Domain knowledge" in writer[1] and "The world stays the same world" in writer[1]
    assert report["meta"]["guides"] == ["picture_book"]
    assert "评测知识 picture_book" in result_mod.render(report)


def test_an_html_book_brings_its_own_images_into_the_workspace(tmp_path):
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages/page-01.png").write_bytes(b"png")
    (tmp_path / "cover 1.png").write_bytes(b"png")
    (tmp_path / "outside").mkdir()
    (tmp_path / "book").mkdir()
    page = tmp_path / "book.html"
    page.write_text('<img src="pages/page-01.png"><img src="cover%201.png"><img src="https://x/y.png">'
                    '<div style="background:url(\'missing.png\')"></div><img src="data:image/png;base64,AA">')
    root = workspace.make_workspace([page])
    assert sorted(str(p.relative_to(root)) for p in root.rglob("*.png")) == ["cover 1.png", "pages/page-01.png"]


@pytest.mark.asyncio
async def test_a_python_check_does_not_see_its_own_script(tmp_path):
    (tmp_path / "report.md").write_text("# r")
    facts, _ = await checks.run_check({"tool": "python", "code": "import os, json; print(json.dumps(sorted(os.listdir('.'))))"},
                                      tmp_path, ["python"])
    assert json.loads(facts["stdout"]) == ["report.md"] and sorted(p.name for p in tmp_path.iterdir()) == ["report.md"]


@pytest.mark.asyncio
async def test_python_checks_measure_the_data(tmp_path, monkeypatch):
    seen = []
    count_rows = ("import csv, json\n"
                  "rows = list(csv.DictReader(open('_inputs/export.csv')))\n"
                  "print(json.dumps({'rows': len(rows), 'report_says': open('report.md').read().split()[-1]}))")
    install(monkeypatch, [
        ("You write evaluation criteria", {"criteria": [PAGE_CRITERIA["criteria"][0]]}),
        ("You plan checks", {"checks": [{"tool": "python", "note": "count", "code": count_rows}]}),
        ("You judge each claim of one criterion about an agent's delivered work", JUDGED),
    ], seen)
    report = await pipeline.text_reward("Count the tickets", {"report.md": "Total 3"},
                                        {"producer": {}, "include_evidence": True},
                                        references={"export.csv": "id\n1\n2\n3\n"})
    check = report["evidence"]["click-scores"][0]
    assert check["tool"] == "python" and check["exit_code"] == 0
    assert json.loads(check["stdout"]) == {"rows": 3, "report_says": "3"}
    assert report["meta"]["tools"]["available"] == ["read", "python"]                  # no page: no browser


@pytest.mark.asyncio
async def test_there_is_no_shell_and_python_checks_cannot_leave_their_folder(tmp_path):
    from openjiuwen.agent_evolving.reward import checks
    root = tmp_path / "work"
    root.mkdir()
    (root / "data.csv").write_text("a,b\n1,2\n")
    outside = tmp_path.parent / f"{tmp_path.name}_outside.txt"
    outside.write_text("secret")
    skipped, _ = await checks.run_check({"tool": "shell", "command": "echo hi"}, root, ["read", "python"])
    assert "not available" in skipped["skipped"]                                    # no shell tool at all
    fine, _ = await checks.run_check({"tool": "python", "code": (
        "import csv, json, os\nopen('out.txt', 'w').write('x')\n"
        "print(json.dumps({'rows': len(list(csv.reader(open('data.csv')))), 'files': sorted(os.listdir('.'))}))")},
        root, ["python"])
    assert json.loads(fine["stdout"]) == {"rows": 2, "files": ["data.csv", "out.txt"]}
    for code in ("import subprocess; subprocess.run(['echo', 'hi'])", "import os; os.system('echo hi')",
                 "import socket; socket.create_connection(('127.0.0.1', 9), timeout=1)",
                 f"open({str(outside)!r}, 'w').write('x')", f"print(open({str(outside)!r}).read())",
                 f"import os; os.remove({str(outside)!r})", "import ctypes; ctypes.CDLL(None)"):
        refused, _ = await checks.run_check({"tool": "python", "code": code}, root, ["python"])
        assert refused["exit_code"] != 0 and "PermissionError" in refused["stderr"], code
    assert outside.read_text() == "secret"
    outside.unlink()


@pytest.mark.asyncio
async def test_read_checks_quote_lines_and_cannot_leave_the_workspace(tmp_path, monkeypatch):
    seen = []
    install(monkeypatch, [
        ("You write evaluation criteria", {"criteria": [PAGE_CRITERIA["criteria"][0]]}),
        ("You plan checks", {"checks": [{"tool": "read", "path": "report.md", "pattern": "total"},
                                        {"tool": "read", "path": "../../etc/passwd"}]}),
        ("One check failed", {"checks": [{"tool": "read", "path": "../secret"}]}),
        ("You judge each claim", JUDGED),
    ], seen)
    report = await pipeline.text_reward("t", {"report.md": "intro\nTotal 3 tickets\n"},
                                        {"producer": {}, "include_evidence": True})
    first, second = report["evidence"]["click-scores"]
    assert first["output"] == "2: Total 3 tickets"
    assert "leaves the workspace" in second["could_not_run"]


@pytest.mark.asyncio
async def test_later_rounds_reuse_the_kept_rubric_and_keep_no_scores(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    seen = []
    verdicts = iter([{"claims": [{"index": 0, "status": "met"}, {"index": 1, "status": "unmet"}]},
                     {"claims": [{"index": 0, "status": "met"}, {"index": 1, "status": "met"}]},
                     {"claims": [{"index": 0, "status": "met"}, {"index": 1, "status": "met"}]}])
    install(monkeypatch, [
        ("You write evaluation criteria", {"criteria": [PAGE_CRITERIA["criteria"][0]]}),
        ("You plan checks", {"checks": []}),
        ("You judge each claim", lambda user: next(verdicts)),
        ("You are an art director", {"critique": [], "claims": [], "summary": "No scripted visual verdict."}),
    ], seen)
    rubric = tmp_path / "rubric.json"
    root = workspace.make_workspace(texts={"page.html": "<p>v1</p>"})
    first = await pipeline.score_workspace("做个游戏", root, {"producer": {}}, rubric_path=rubric)
    root = workspace.make_workspace(texts={"page.html": "<p>v2</p>"})
    second = await pipeline.score_workspace("做个游戏（改进版）", root, {"producer": {}}, rubric_path=rubric)
    assert sum(s[0].startswith("You write evaluation criteria") for s in seen) == 1     # written once
    assert [c["category_id"] for c in second["categories"]] == [c["category_id"] for c in first["categories"]]
    assert first["total"]["score"] < second["total"]["score"]
    assert set(second) == {"schema_version", "meta", "total", "categories", "sub_category_items"}  # no history, no previous
    assert set(second["total"]) == {"score", "summary"} and "round" not in second["meta"]
    assert set(json.loads(rubric.read_text())) == {"criteria"}                       # only the criteria are kept
    root = workspace.make_workspace(texts={"page.html": "<p>v3</p>"})
    assert pipeline.archive_rubric(rubric).name == "rubric.1.json"              # the caller moves the old one aside
    renewed = await pipeline.score_workspace("做个游戏", root, {"producer": {}}, rubric_path=rubric)
    assert sum(s[0].startswith("You write evaluation criteria") for s in seen) == 2     # written again
    assert (tmp_path / "rubric.1.json").exists() and rubric.exists() and "rubric_renewed" not in renewed["meta"]


@pytest.mark.asyncio
async def test_looks_are_judged_by_eye_only_and_a_major_flaw_caps_them(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    (tmp_path / "index.html").write_text(GAME)
    seen = []
    visual = {"id": "look", "scope": "terminal", "kind": "visual", "name": "画面", "goal": "好看",
              "claims": [{"text": "主角可辨认", "level": "floor"}, {"text": "主角结构完整", "level": "challenge"},
                         {"text": "达到上架水准", "level": "challenge"}]}
    install(monkeypatch, [
        ("You write evaluation criteria", {"criteria": [visual]}),
        ("You plan checks", {"checks": [{"tool": "read", "path": "index.html"}]}),          # no screenshot taken
        ("You are an art director", {"critique": [{"issue": "头和身体分离", "severity": "major", "claims": [1, 2]}],
                                     "claims": [{"index": 0, "status": "met"}, {"index": 1, "status": "met"},
                                                {"index": 2, "status": "partial"}]}),
    ], seen)
    root = workspace.make_workspace([tmp_path / "index.html"])
    report = await pipeline.score_workspace("做个小游戏", root, {"producer": {}})
    art = [s for s in seen if s[0].startswith("You are an art director")][0]
    assert "<button" not in art[1] and art[2] and art[2][0].startswith("default view")  # pictures, no code
    looks = [i for i in report["sub_category_items"] if i["category_id"] == "look"]
    assert [i["status"] for i in looks] == ["met", "partial", "partial", "unknown"]  # major flaw caps "met"
    assert looks[-1]["rubric"] == pipeline.LOOK_CLAIM["zh"]                            # the published-work claim is added
    assert report["categories"][0]["issues"][0] == "头和身体分离"
    assert "画面问题：头和身体分离" in result_mod.render(report)


@pytest.mark.asyncio
async def test_slow_checks_stop_at_the_deadline_and_the_judge_still_runs(tmp_path, monkeypatch):
    seen = []

    async def hangs(check, root, tools, number=1):
        await asyncio.sleep(60)

    monkeypatch.setattr(checks, "run_check", hangs)
    install(monkeypatch, [
        ("You write evaluation criteria", {"criteria": [PAGE_CRITERIA["criteria"][0]]}),
        ("You plan checks", {"checks": [{"tool": "python", "code": "print(1)"}]}),
        ("You judge each claim", {"claims": [{"index": 0, "status": "unknown"}, {"index": 1, "status": "unknown"}]}),
    ], seen)
    monkeypatch.setattr(models.Model, "__init__", lambda self, config: self.__dict__.update(
        name="m", vision=True, quota=0))
    report = await asyncio.wait_for(pipeline.text_reward("t", {"a.txt": "x"}, {
        "producer": {}, "deadline_seconds": 0.5, "include_evidence": True}), 20)
    assert "ran out of time" in json.dumps(report["evidence"])
    assert report["categories"][0]["score"] is None  # the time limit is a normal stop; nothing seen, nothing scored


@pytest.mark.asyncio
async def test_a_blind_judge_gets_no_screenshots_and_the_reply_warns(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    (tmp_path / "index.html").write_text(GAME)
    seen = []

    async def blind(model, log=None, notes=None):
        return False

    monkeypatch.setattr(models, "can_see", blind)
    install(monkeypatch, [
        ("You write evaluation criteria", {"criteria": [PAGE_CRITERIA["criteria"][0]]}),
        ("You plan checks", {"checks": [{"tool": "browser", "page": "index.html", "actions": [], "snapshots": []}]}),
        ("You judge each claim", {"claims": [{"index": 0, "status": "met"}, {"index": 1, "status": "unknown"}]}),
    ], seen, name="deepseek-v4-flash", vision=None)
    root = workspace.make_workspace([tmp_path / "index.html"])
    report = await pipeline.score_workspace("Make a click game", root, {"producer": {}})
    judged = [x for x in seen if x[0].startswith("You judge each claim")]
    assert judged[0][2] == []
    assert report["categories"][0]["score"] == 100         # an unknown challenge is left out, like an unknown floor
    assert report["categories"][1]["score"] is None        # blind visual criterion is excluded as a whole
    assert report["total"]["score"] == 100
    assert "deepseek-v4-flash 不支持看图" in report["meta"]["warnings"][0]
    assert "⚠️ 当前模型 deepseek-v4-flash 不支持看图" in result_mod.render(report)
    assert "画面维度整项不计分、不计入总分" in result_mod.render(report).split("输入：", 1)[0]


def test_cli_passes_the_product_paths_and_the_text_reply(tmp_path, monkeypatch):
    seen = []

    async def fake_reward(task, artifacts, config, txt_response="", user_note="", process_signal="", rubric_path=None,
                          previous_result=None, log=None, warnings=()):
        seen.append(([Path(p).name for p in artifacts], txt_response, user_note, process_signal))
        return result_mod.build(task, [], None, "", {"deliverable": []})

    import sys
    import types
    monkeypatch.setitem(sys.modules, "openai", types.ModuleType("openai"))
    monkeypatch.setattr(pipeline, "reward", fake_reward)
    page, doc, config = tmp_path / "index.html", tmp_path / "a.md", tmp_path / "c.json"
    page.write_text(GAME); doc.write_text("x"); config.write_text("{}")
    base = ["--task", "t", "--config", str(config), "--out", str(tmp_path / "r.json")]
    cli.main(base + ["--artifact", str(page), str(doc), "--txt-response", "done", "--user-note", "猫太丑",
                     "--process-signal", "TypeError: x is undefined"])
    assert seen == [(["index.html", "a.md"], "done", "猫太丑", "TypeError: x is undefined")]
    written = json.loads((tmp_path / "r.json").read_text())
    assert written["schema_version"] == "reward/3" and written["meta"]["artifacts"] == [str(page), str(doc)]
    for gone in (["--trace", "t.json"], ["--reference", "in"], ["--response", "done"], ["--product", "x"]):
        with pytest.raises(SystemExit):
            cli.main(base + gone)  # no trace, reference or plain response option any more


def test_config_file_or_defaults(tmp_path, monkeypatch):
    from openjiuwen.agent_evolving.reward import config as config_mod
    monkeypatch.delenv("REWARD_CONFIG", raising=False)
    monkeypatch.setenv("REWARD_HOME", str(tmp_path))
    fixed = {"producer": {"model": "m", "base_url": "u", "api_key": "k"}, "criteria_budget": 2}
    (tmp_path / "config.json").write_text(json.dumps(fixed))
    assert config_mod.load_config() == fixed                                   # ~/.reward/config.json
    with pytest.raises(FileNotFoundError):
        config_mod.load_config(tmp_path / "missing.json")                      # a named file must exist
    (tmp_path / "config.json").unlink()
    monkeypatch.setattr(config_mod, "resolve_models", lambda c: c)
    assert config_mod.load_config() == config_mod.DEFAULT_CONFIG               # no file: built-in defaults
    assert "tools" not in config_mod.DEFAULT_CONFIG                            # no shell switch: there is no shell


@pytest.mark.asyncio
async def test_concurrent_runs_keep_their_own_progress_files(tmp_path):
    async def run(name):
        progress_mod.use(progress_mod.Progress(tmp_path / f"{name}.json", "general"))
        await asyncio.sleep(0.01)
        progress_mod.current().finish(len(name))
        return progress_mod.current().path.name

    assert await asyncio.gather(asyncio.ensure_future(run("a")), asyncio.ensure_future(run("bb"))) == ["a.json", "bb.json"]
    assert json.loads((tmp_path / "bb.json").read_text())["score"] == 2


@pytest.mark.asyncio
async def test_work_without_a_deliverable_file_is_scored_from_the_reply(tmp_path, monkeypatch):
    seen = []
    install(monkeypatch, [
        ("You write evaluation criteria", {"criteria": [{"id": "answer", "scope": "terminal", "name": "答案", "goal": "g",
                                                         "claims": [{"text": "给出距离", "level": "floor"},
                                                                    {"text": "说明是直线还是公路", "level": "challenge"}]}]}),
        ("You read an agent's final reply", {"claims": []}),
        ("You plan checks", {"checks": []}),
        ("You judge each claim", {"claims": [{"index": 0, "status": "met"}, {"index": 1, "status": "met"}]}),
    ], seen)
    root = workspace.make_workspace()
    report = await pipeline.score_workspace("北京到上海多远？一句话回答", root, {"producer": {}}, txt_response="直线约 1070 公里。",
                                   rubric_path=tmp_path / "rubric.json")
    assert report["total"]["score"] == 100
    judged = next(user for system, user, _ in seen if system.startswith("You judge each claim"))
    assert "(no deliverable files)" in judged and "1070" in judged
    assert "交付物 无" in result_mod.render(report)


@pytest.mark.asyncio
async def test_pages_stacked_in_one_long_page_are_reached_by_scrolling(tmp_path):
    pytest.importorskip("playwright")
    page = tmp_path / "book.html"
    page.write_text("<body style='margin:0'>" + "".join(
        f"<section id='p{i}' style='height:100vh;background:hsl({i * 40},70%,50%)'>page {i}</section>" for i in range(1, 4))
        + "</body>")
    facts, shots = await checks.run_experiment(page, {"actions": [
        {"type": "scroll", "pages": 1}, {"type": "probe", "script": "() => Math.round(window.scrollY / window.innerHeight)"},
        {"type": "scroll", "selector": "#p3"}, {"type": "probe", "script": "() => Math.round(window.scrollY / window.innerHeight)"}],
        "snapshots": [0, 2]})
    assert [f.get("value") for f in facts["actions"] if "value" in f] == [1, 2] and len(shots) == 3   # loaded + 2


def test_a_claim_naming_a_judge_is_reworded():
    criteria = [{"id": "visual", "scope": "terminal", "name": "画面", "goal": "g", "claims": [
        {"text": "主角认得出", "level": "floor"}, {"text": "美术总监会认可它的水准", "level": "challenge"}]}]
    texts = [c["text"] for c in pipeline.ensure_visual(criteria, "做一个绘本")[0]["claims"]]
    assert texts == ["主角认得出", pipeline.LOOK_CLAIM["zh"]]


@pytest.mark.asyncio
async def test_a_work_made_of_pictures_is_seen_through_its_picture_files(tmp_path):
    pytest.importorskip("playwright")
    import base64 as b64
    png = b64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    for name in ("page10.png", "page2.png", "page1.png"):
        (tmp_path / name).write_bytes(png)
    shots = await pipeline.default_screenshots(tmp_path, print)
    assert [label for label, _ in shots] == ["delivered picture page1.png", "delivered picture page2.png",
                                             "delivered picture page10.png"]
    assert all(b64.b64decode(data)[:2] == b"\xff\xd8" for _, data in shots)          # jpeg, as the models expect


@pytest.mark.asyncio
async def test_default_screenshots_press_the_start_button_not_the_first_one(tmp_path):
    pytest.importorskip("playwright")
    (tmp_path / "game.html").write_text("""<button id="sound">音效：开</button><button id="startBtn">开始游戏</button>
<p id="state">menu</p><script>
document.getElementById('sound').onclick = () => document.getElementById('state').textContent = 'sound';
document.getElementById('startBtn').onclick = () => document.getElementById('state').textContent = 'playing';
</script>""")
    facts, _ = await checks.run_experiment(tmp_path / "game.html", {"actions": [
        {"type": "click", "selector": pipeline.START_BUTTON},
        {"type": "probe", "script": "() => document.getElementById('state').textContent"}]})
    assert facts["actions"][1]["value"] == "playing"


def test_a_work_folder_is_copied_without_other_versions_beside_it(tmp_path):
    work = tmp_path / "project"
    (work / "pages").mkdir(parents=True)
    (work / "pages" / "page1.png").write_bytes(b"png")
    (work / "index.html").write_text("<img src='pages/page1.png'>")
    (work / "baseline_v2" / "pages").mkdir(parents=True)
    (work / "baseline_v2" / "index.html").write_text("<p>the other branch</p>")
    root = workspace.make_workspace([work])
    assert sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()) == ["index.html", "pages/page1.png"]


@pytest.mark.asyncio
async def test_the_users_feedback_becomes_checks_in_the_criteria_it_concerns(tmp_path, monkeypatch):
    seen = []
    placements = iter([{"claims": [{"criterion": "visual", "text": "每页每个人物正好两只手"}]},
                       {"claims": [{"criterion": "nope", "text": "楼梯场景里孩子的动作自然"},
                                   {"criterion": "visual", "text": "每页每个人物正好两只手"}]}])
    install(monkeypatch, [
        ("You write evaluation criteria", {"criteria": [PAGE_CRITERIA["criteria"][0]]}),
        ("You place a user's feedback", lambda user: next(placements)),
        ("You plan checks", {"checks": []}),
        ("You judge each claim", JUDGED),
        ("You are an art director", {"critique": [], "claims": []}),
    ], seen)
    rubric = tmp_path / "rubric.json"
    first = await pipeline.score_workspace("做绘本", workspace.make_workspace(texts={"a.md": "x"}), {"producer": {}},
                                  rubric_path=rubric, user_note="第 2 页多一只手")
    asked = json.loads(next(u for s, u, _ in seen if s.startswith("You place a user's feedback")))
    assert asked["user_note"] == "第 2 页多一只手" and [c["id"] for c in asked["criteria"]] == ["click-scores", "visual"]
    hands = [i for i in first["sub_category_items"] if i["from_user_note"]]
    assert [(i["category_id"], i["rubric"]) for i in hands] == [("visual", "每页每个人物正好两只手")]
    second = await pipeline.score_workspace("做绘本", workspace.make_workspace(texts={"a.md": "y"}), {"producer": {}},
                                   rubric_path=rubric, user_note="楼梯不自然")
    assert [c["category_id"] for c in second["categories"]] == ["click-scores", "visual"]      # no criterion is added
    stairs, = [i for i in second["sub_category_items"] if i["rubric"] == "楼梯场景里孩子的动作自然"]
    assert stairs["category_id"] == "click-scores" and stairs["from_user_note"]     # unknown id: never the looks-only one
    assert sorted(i["rubric"] for i in second["sub_category_items"] if i["from_user_note"]) == sorted([
        "每页每个人物正好两只手", "楼梯场景里孩子的动作自然"])                      # kept from round 1, no repeat
    kept = json.loads(rubric.read_text())["criteria"]
    assert sum(x.get("source") == "feedback" for c in kept for x in c["claims"]) == 2   # the rubric grows
    third = await pipeline.score_workspace("做绘本", workspace.make_workspace(texts={"a.md": "z"}), {"producer": {}},
                                  rubric_path=rubric)
    assert sum(s.startswith("You place a user's feedback") for s, _, _ in seen) == 2   # no feedback, no call
    assert len([i for i in third["sub_category_items"] if i["from_user_note"]]) == 2


@pytest.mark.asyncio
async def test_reward_takes_product_paths_and_places_note_and_signal(tmp_path, monkeypatch):
    seen = []
    install(monkeypatch, [
        ("You write evaluation criteria", {"criteria": [PAGE_CRITERIA["criteria"][0]]}),
        ("You place a user's feedback", {"claims": [
            {"criterion": "visual", "text": "主角造型可爱", "from": "user_note"},
            {"criterion": "click-scores", "text": "运行时控制台没有报错", "from": "process_signal"}]}),
        ("You plan checks", {"checks": []}),
        ("You judge each claim", JUDGED),
        ("You are an art director", {"critique": [], "claims": []}),
    ], seen)
    made = []
    real = pipeline.make_workspace
    monkeypatch.setattr(pipeline, "make_workspace", lambda products: made.append(real(products)) or made[-1])
    (tmp_path / "a.md").write_text("x")
    report = await pipeline.reward("做绘本", [str(tmp_path / "a.md")], {"producer": {}},
                                   user_note="猫太丑", process_signal="TypeError: x is undefined")
    asked = json.loads(next(u for s, u, _ in seen if s.startswith("You place a user's feedback")))
    assert asked["user_note"] == "猫太丑" and asked["process_signal"] == "TypeError: x is undefined"
    by_text = {i["rubric"]: i for i in report["sub_category_items"]}
    assert by_text["主角造型可爱"]["from_user_note"] and not by_text["主角造型可爱"]["from_process_signal"]
    assert by_text["运行时控制台没有报错"]["from_process_signal"]
    assert report["meta"]["user_note"] == "猫太丑" and report["meta"]["process_signal"].startswith("TypeError")
    assert made and not made[0].exists() and (tmp_path / "a.md").exists()   # scratch copy removed, original kept


class ScriptedLM:
    """The evidence agent's model: one scripted turn per call (tool calls, or a final line)."""

    def __init__(self, turns):
        self.turns, self.seen, self.tools = list(turns), [], []

    async def stream(self, messages, tools=None, **kwargs):
        from openjiuwen.core.foundation.llm import ToolCall
        from openjiuwen.core.foundation.llm.schema.message_chunk import AssistantMessageChunk

        self.seen.append(json.dumps([getattr(m, "content", m) for m in messages], default=str, ensure_ascii=False))
        self.tools.append(sorted(t.name for t in tools or []))
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        if isinstance(turn, str):
            yield AssistantMessageChunk(content=turn, tool_calls=[])
            return
        yield AssistantMessageChunk(content="", tool_calls=[
            ToolCall(id=f"t{len(self.seen)}-{n}", type="function", name=name, arguments=json.dumps(args))
            for n, (name, args) in enumerate(turn)])


ROUTE = {"model": "scripted-model", "base_url": "http://127.0.0.1:9/v1", "api_key": "k"}

TWO_CRITERIA = {"criteria": [
    {"id": "facts", "scope": "terminal", "name": "事实", "goal": "数字有来源",
     "claims": [{"text": "营收增长数字和来源一致", "level": "floor"}, {"text": "标出来源", "level": "challenge"}]},
    {"id": "form", "scope": "terminal", "name": "形式", "goal": "简洁",
     "claims": [{"text": "不超过三句", "level": "floor"}, {"text": "先给结论", "level": "challenge"}]}]}


@pytest.mark.real_agent
@pytest.mark.asyncio
async def test_one_deep_agent_gathers_evidence_for_all_criteria_and_keeps_results_on_disk(monkeypatch):
    from openjiuwen.core.single_agent.agents.react_agent import ReActAgent

    seen = []
    install(monkeypatch, [
        ("You write evaluation criteria", TWO_CRITERIA),
        ("You judge each claim", lambda user: {"claims": [{"index": 0, "status": "met", "reason": "ok"},
                                                          {"index": 1, "status": "partial", "reason": "so-so"}],
                                               "summary": "还行"}),
    ], seen)
    lm = ScriptedLM([
        [("run_checks", {"checks": [
            {"tool": "read", "path": "_inputs/web/01.md", "pattern": "37%", "context": 1, "criteria": ["facts"]},
            {"tool": "read", "path": "summary.md", "criteria": ["form", "facts"]},
            {"tool": "python", "code": "print(open('nope').read())", "criteria": ["facts"]}]})],
        [("read_evidence", {"id": "c1", "pattern": "37%"})],
        "done: every claim has evidence",
    ])
    monkeypatch.setattr(ReActAgent, "_get_llm", lambda self: lm)
    page = "URL: https://acme.com/report\n" + "filler line\n" * 3000 + "Revenue grew 37% in 2025.\n"
    report = await pipeline.text_reward("总结营收", {"summary.md": "营收增长 37%（acme.com）。"},
                                        {"producer": ROUTE, "include_evidence": True},
                                        references={"web/01.md": page})
    assert lm.tools[0] == ["read_evidence", "run_checks"]                         # only the evidence tools
    facts, form = report["evidence"]["facts"], report["evidence"]["form"]
    assert [e["check"] for e in facts] == [1, 2, 3] and [e["check"] for e in form] == [2]   # by criterion
    assert "3002: Revenue grew 37% in 2025." in facts[0]["output"]               # found in a long input
    assert "No such file" in facts[2]["stderr"]
    first, second, third = lm.seen
    assert "facts" in first and "inputs_index" in first and "filler line\\nfiller" not in first
    assert 'summary' in second and len(second) - len(first) < 6000                # summaries, not full results
    assert "Revenue grew 37%" in third                                             # read on demand
    assert [c["score"] for c in report["categories"]] == [75, 75]


@pytest.mark.real_agent
@pytest.mark.asyncio
async def test_the_evidence_agent_fails_loudly(monkeypatch):
    from openjiuwen.core.single_agent.agents.react_agent import ReActAgent

    install(monkeypatch, [("You write evaluation criteria", TWO_CRITERIA)], [])
    monkeypatch.setattr(ReActAgent, "_get_llm", lambda self: ScriptedLM(["I think it is fine."]))
    with pytest.raises(evidence_agent.EvidenceError, match="ran no check"):
        await pipeline.text_reward("t", {"a.md": "x"}, {"producer": ROUTE})
    monkeypatch.setattr(ReActAgent, "_get_llm", lambda self: ScriptedLM([RuntimeError("401 bad key")]))
    with pytest.raises(evidence_agent.EvidenceError, match="401 bad key"):
        await pipeline.text_reward("t", {"a.md": "x"}, {"producer": ROUTE})


def test_the_evidence_model_can_be_a_route_dict_or_an_agent_core_entry(monkeypatch):
    from types import SimpleNamespace

    from openjiuwen.agent_teams.models.pool import ModelPoolEntry

    monkeypatch.setenv("FR_TEST_KEY", "secret")
    entry = evidence_agent.pool_entry({**ROUTE, "api_key": "env:FR_TEST_KEY", "extra_body": {"enable_thinking": False}})
    assert (entry.model_name, entry.api_key, entry.metadata["request"]) == ("scripted-model", "secret",
                                                                            {"enable_thinking": False})
    given = ModelPoolEntry(model_name="m", api_key="k", api_base_url="u", api_provider="OpenAI")
    assert evidence_agent.pool_entry(given) is given
    like = evidence_agent.pool_entry(SimpleNamespace(model_name="chat", api_base_url="u", api_key="k"))
    assert like.model_name == "chat" and like.api_provider == "OpenAI"


@pytest.mark.real_agent
@pytest.mark.asyncio
async def test_the_round_limit_is_a_normal_stop_and_a_missing_tool_is_inferred(monkeypatch):
    from openjiuwen.core.single_agent.agents.react_agent import ReActAgent

    install(monkeypatch, [("You write evaluation criteria", TWO_CRITERIA),
                          ("You judge each claim", JUDGED)], [])
    turn = [("run_checks", {"checks": [{"path": "a.md", "criteria": ["facts"]}]})]   # no "tool": a path means read
    monkeypatch.setattr(ReActAgent, "_get_llm", lambda self: ScriptedLM([turn, turn, turn]))
    report = await pipeline.text_reward("t", {"a.md": "x"}, {"producer": ROUTE, "evidence_rounds": 2,
                                                             "include_evidence": True})
    assert [e["tool"] for e in report["evidence"]["facts"]] == ["read"] and report["total"]["score"] is not None
    assert evidence_agent.with_tool({"page": "i.html"})["tool"] == "browser"


@pytest.mark.real_agent
@pytest.mark.asyncio
async def test_repeated_checks_are_not_rerun_and_an_idle_agent_is_stopped(monkeypatch):
    from openjiuwen.core.single_agent.agents.react_agent import ReActAgent

    install(monkeypatch, [("You write evaluation criteria", TWO_CRITERIA),
                          ("You judge each claim", JUDGED)], [])
    same = [("run_checks", {"checks": [{"tool": "read", "path": "a.md", "criteria": ["facts"]}]})]
    lm = ScriptedLM([same] * 8)
    monkeypatch.setattr(ReActAgent, "_get_llm", lambda self: lm)
    report = await pipeline.text_reward("t", {"a.md": "x"}, {"producer": ROUTE, "include_evidence": True})
    assert len(report["evidence"]["facts"]) == 1                                  # the repeats ran no second check
    assert len(lm.seen) <= 6                                                      # 1 new + 4 repeats, then stopped
    assert "already run as c1" in lm.seen[2] and "Stop now" in lm.seen[-1]


@pytest.mark.real_agent
@pytest.mark.asyncio
async def test_reading_saved_results_many_times_is_never_cut_off(monkeypatch):
    from openjiuwen.core.single_agent.agents.react_agent import ReActAgent

    install(monkeypatch, [("You write evaluation criteria", TWO_CRITERIA),
                          ("You judge each claim", JUDGED)], [])
    check = [("run_checks", {"checks": [{"tool": "read", "path": "a.md", "criteria": ["facts"]}]})]
    read = [("read_evidence", {"id": "c1", "start": 1})]
    lm = ScriptedLM([check] + [read] * 6 + ["enough evidence"])
    monkeypatch.setattr(ReActAgent, "_get_llm", lambda self: lm)
    await pipeline.text_reward("t", {"a.md": "x"}, {"producer": ROUTE, "include_evidence": True})
    assert len(lm.seen) == 8 and not any("Stop now" in turn for turn in lm.seen)   # progressive reading runs on
