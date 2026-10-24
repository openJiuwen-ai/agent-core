"""Cross-boundary evidence contracts: reuse, cancellation, vision and concurrent language."""

import asyncio
import json
import shutil

import pytest

from openjiuwen.agent_evolving.reward import checks, evidence_agent, models, pipeline, workspace
from openjiuwen.agent_evolving.reward.options import RewardOptions


def test_missing_artifact_is_not_silently_graded_as_a_text_only_answer(tmp_path):
    with pytest.raises(FileNotFoundError, match="artifact does not exist"):
        workspace.make_workspace([tmp_path / "missing.html"])


@pytest.mark.asyncio
async def test_reused_check_serves_every_requesting_criterion_and_keeps_its_images(tmp_path, monkeypatch):
    calls = []
    images = [(f"page {i}", str(i)) for i in range(20)]

    async def execute(check, *_args):
        calls.append(check)
        await asyncio.sleep(0)
        return {"tool": "browser", "page_errors": []}, images

    monkeypatch.setattr(checks, "run_check", execute)
    run = evidence_agent.EvidenceRun([{"id": "a"}, {"id": "b"}],
                                    {"root": tmp_path, "tools": ["browser"]}, tmp_path, lambda _: None)
    check = {"tool": "browser", "page": "index.html", "criteria": ["a"], "note": "original"}
    first, second = await asyncio.gather(
        run.run_checks([check, check]),
        run.run_checks([{**check, "criteria": ["b"], "note": "reuse"}]),
    )
    assert len(calls) == 1
    assert all(row["id"] == "c1" for result in (first, second) for row in result["results"])
    assert run.evidence_for("a") == run.evidence_for("b")
    assert len(run.evidence_for("b")[1]) == evidence_agent.MAX_IMAGES   # the latest ones reach the judge
    receipt = (tmp_path / "c1.txt").read_text()
    assert "criteria: a, b" in receipt and '"note": "original"' in receipt


@pytest.mark.asyncio
async def test_python_timeout_is_not_reported_as_a_successful_check(tmp_path, monkeypatch):
    async def execute(*_args):
        return {"tool": "python", "timed_out": 1, "exit_code": None}, []

    monkeypatch.setattr(checks, "run_check", execute)
    run = evidence_agent.EvidenceRun([{"id": "a"}],
                                    {"root": tmp_path, "tools": ["python"]}, tmp_path, lambda _: None)
    result = await run.run_checks([{"tool": "python", "criteria": ["a"], "code": "pass"}])
    assert result["results"][0]["ok"] is False


class FakeSession:
    async def pre_run(self, **_kwargs):
        pass

    async def post_run(self):
        pass


@pytest.mark.asyncio
async def test_text_reply_reaches_real_gather_and_judge_without_a_fabricated_check(tmp_path, monkeypatch):
    from openjiuwen.core.session import agent as sessions

    reply = "北京到上海的直线距离约一千公里。"
    queries = []

    class Agent:
        card = object()

        def add_rail(self, _rail):
            pass

        async def stream(self, inputs, **_kwargs):
            queries.append(json.loads(inputs["query"]))
            yield {"output": "The supplied reply is sufficient for judging."}

        async def cleanup_task_resources(self):
            pass

    class Judge:
        name, quota = "test", 30

        def __init__(self, _config):
            pass

        async def json(self, _system, user, *_args):
            data = json.loads(user)
            if "criterion" in data:
                assert data["final_reply"] == reply and data["checks"] == []
                return {"claims": [{"index": 0, "status": "met", "reason": "Reply supplies an estimate"}]}
            return {"summary": "An approximate distance is supplied."}

    monkeypatch.setattr(evidence_agent, "build_agent", lambda *_: (Agent(), "test"))
    monkeypatch.setattr(sessions, "create_agent_session", lambda **_: FakeSession())
    monkeypatch.setattr(models, "Model", Judge)
    criterion = {"id": "answer", "claims": [{"text": "Gives an approximate distance", "level": "floor"}]}
    result = await pipeline.score_workspace("北京到上海多远？", tmp_path, {"producer": {}},
                                            RewardOptions(txt_response=reply, criteria=[criterion]))
    assert queries[0]["final_reply"] == reply and queries[0]["workspace"]["files"] == {}
    assert result["total"]["score"] == 100 and result["meta"]["tools"]["used"] == []


@pytest.mark.parametrize("folder_input", [False, True])
def test_html_assets_include_recursive_css_dependencies_without_other_versions(tmp_path, folder_input):
    source = tmp_path / "product"
    (source / "styles").mkdir(parents=True)
    (source / "images").mkdir()
    (source / "old-version").mkdir()
    (source / "old-version" / "index.html").write_text("unrelated old work")
    page = source / "index.html"
    page.write_text('<link rel="stylesheet" href="styles/main.css"><a href="index.html">Self</a>')
    (source / "styles" / "main.css").write_text('@import "theme.css"; .hero{background:url(../images/hero.svg)}')
    (source / "styles" / "theme.css").write_text('@import "main.css"; .icon{background:url(../images/icon.svg)}')
    (source / "images" / "hero.svg").write_text("<svg>hero</svg>")
    (source / "images" / "icon.svg").write_text("<svg>icon</svg>")
    copied = workspace.make_workspace([source if folder_input else page])
    try:
        assert (copied / "images" / "hero.svg").read_text() == "<svg>hero</svg>"
        assert (copied / "images" / "icon.svg").read_text() == "<svg>icon</svg>"
        assert not (copied / "old-version").exists()
        assert len(workspace.linked_files(page, max_files=4)) == 4  # cycles do not consume the limit
        with pytest.raises(ValueError, match="copy limit"):
            workspace.linked_files(page, max_files=3)
    finally:
        shutil.rmtree(copied)


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [True, False])
async def test_gather_stops_its_background_stream_and_rejects_only_failed_checks(tmp_path, monkeypatch, cancel):
    from openjiuwen.core.session import agent as sessions

    started = asyncio.Event()

    class Agent:
        card = object()
        closed = False

        def add_rail(self, run):
            self.run = run

        async def stream(self, *_args, **_kwargs):
            try:
                await self.run.run_checks([{"tool": "missing", "criteria": ["a"]}])
                started.set()
                if cancel:
                    await asyncio.Event().wait()
                yield {}
            finally:
                self.closed = True

        async def cleanup_task_resources(self):
            assert self.closed

    agent = Agent()
    monkeypatch.setattr(evidence_agent, "build_agent", lambda *_: (agent, "test"))
    monkeypatch.setattr(evidence_agent, "evidence_tools", lambda run: run)
    monkeypatch.setattr(sessions, "create_agent_session", lambda **_: FakeSession())
    task = asyncio.create_task(evidence_agent.gather(
        [{"id": "a", "claims": []}], {"root": tmp_path, "tools": [], "plan_input": {}}, {}, lambda _: None))
    if cancel:
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        with pytest.raises(evidence_agent.EvidenceError, match="every check failed"):
            await task
    assert agent.closed


@pytest.mark.asyncio
async def test_cancelling_python_check_kills_and_waits_for_process(monkeypatch, tmp_path):
    started = asyncio.Event()
    events = []

    class Process:
        returncode = None

        async def communicate(self):
            started.set()
            await asyncio.Event().wait()

        def kill(self):
            events.append("kill")

        async def wait(self):
            events.append("wait")
            self.returncode = -9

    async def launch(*_args, **_kwargs):
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    task = asyncio.create_task(checks.run_process(["unused"], tmp_path, 60))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["kill", "wait"]




@pytest.mark.asyncio
async def test_the_agent_is_steered_to_run_pages_not_to_read_them(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    page = "\n".join(f"line {i}" for i in range(1, 501))
    (tmp_path / "game.html").write_text(page)
    (tmp_path / "notes.md").write_text("# Notes\nscore rules")
    for asked in ({"start": 1, "count": 400}, {"pattern": "line 42$"}):
        read, _ = await checks.run_check({"tool": "read", "path": "game.html", **asked}, tmp_path,
                                         ["read", "python", "browser"])
        assert "not read" in read["skipped"] and "output" not in read           # a page is used, not read
    code, _ = await checks.run_check({"tool": "read", "path": "game.html", "pattern": "line 42$"}, tmp_path, ["read"])
    assert code["output"] == "42: line 42"                                        # no page to use: code is read
    notes, _ = await checks.run_check({"tool": "read", "path": "notes.md"}, tmp_path, ["read"])
    assert "score rules" in notes["output"]                                       # documents are read as before
    saved, _ = await checks.run_check({"tool": "read", "id": "c3"}, tmp_path, ["read"])
    assert "read_evidence" in saved["skipped"]
    shown = evidence_agent.shown({"tool": "browser", "actions": [
        {"index": 0, "action": {"type": "probe", "script": "() => " + "x" * 2000}, "ok": True, "value": {"score": 3}}]})
    assert shown["actions"] == [{"index": 0, "ok": True, "value": {"score": 3}}]  # the outcome, not the echo
    seeds = []

    async def capture(criteria, ctx, *args, **kwargs):
        seeds.append(ctx["plan_input"]["workspace"])
        return {c["id"]: ([], []) for c in criteria}

    async def no_walk(root, log, errors=None):
        return None

    monkeypatch.setattr(evidence_agent, "gather", capture)
    monkeypatch.setattr(pipeline, "default_walk", no_walk)
    monkeypatch.setattr(models.Model, "__init__", lambda self, config: self.__dict__.update(name="m", vision=False))

    async def answer(self, system, user, images=()):
        if system.startswith("You write evaluation criteria"):
            return {"criteria": [{"id": "play", "claims": [{"text": "Scores", "level": "floor"}]}]}
        judged.append(user)
        return {"claims": [{"index": 0, "status": "unknown"}], "summary": "s"}

    judged = []
    monkeypatch.setattr(models.Model, "json", answer)
    await pipeline.score_workspace("Write notes", tmp_path, {"producer": {}})
    assert "line 1" not in judged[0] and "score rules" in judged[0]               # nor does the judge read page code
    assert seeds[0]["files"] == {"game.html": "500 lines", "notes.md": "2 lines"}
    assert "score rules" in seeds[0]["excerpts"] and "line 1" not in seeds[0]["excerpts"]   # documents, not page code


@pytest.mark.asyncio
async def test_a_check_run_before_the_agent_serves_every_criterion_and_is_not_run_again(tmp_path, monkeypatch):
    calls, lines = [], []

    async def execute(check, root, tools, number=1):
        calls.append(check)
        return {"tool": "browser", "page_errors": []}, []

    monkeypatch.setattr(checks, "run_check", execute)
    run = evidence_agent.EvidenceRun([{"id": "a"}, {"id": "b"}],
                                    {"root": tmp_path, "tools": ["browser"]}, tmp_path, lines.append)
    walk = {"tool": "browser", "page": "index.html", "note": "default walk", "actions": [], "snapshots": [0]}
    row = run.add_done(walk, {"tool": "browser", "page": "index.html", "actions": []}, [("after action 0", "IMG")])
    assert row["id"] == "c1" and row["criteria"] == ["a", "b"] and row["screenshots"] == ["check 1, after action 0"]
    assert lines == ["evidence c1 (browser) for a, b", evidence_agent.BATCH_DONE]  # counted like any check
    for cid in ("a", "b"):
        evidence, images = run.evidence_for(cid)
        assert evidence[0]["check"] == 1 and images == [("check 1, after action 0", "IMG")]
    again = await run.run_checks([{**walk, "criteria": ["a"], "note": "same again"}])
    assert again["results"][0]["repeat"] and not calls                   # the agent's repeat is not run
    fresh = await run.run_checks([{"tool": "browser", "page": "index.html", "actions": [{"type": "key"}],
                                   "criteria": ["a"]}])
    assert fresh["results"][0]["id"] == "c2" and len(calls) == 1


@pytest.mark.asyncio
async def test_the_default_walk_runs_while_the_criteria_are_written_and_reaches_the_agent(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<button>开始</button>")
    order, given = [], []

    async def walk(root, log, errors=None):
        order.append("walk started")
        await asyncio.sleep(0.05)
        order.append("walk done")
        return ({"tool": "browser", "page": "index.html"}, {"tool": "browser", "page": "index.html"}, [])

    async def capture(criteria, ctx, route, log, limits=None, prefetched=()):
        given.extend(prefetched)
        return {c["id"]: ([], []) for c in criteria}

    async def answer(self, system, user, images=()):
        if system.startswith("You write evaluation criteria"):
            await asyncio.sleep(0.2)   # a slow model writing the criteria
            order.append("criteria written")
            return {"criteria": [{"id": "play", "claims": [{"text": "Scores", "level": "floor"}]}]}
        return {"claims": [{"index": 0, "status": "unknown"}], "summary": "s"}

    monkeypatch.setattr(pipeline, "default_walk", walk)
    monkeypatch.setattr(checks, "available_tools", lambda root, config: ["read", "python", "browser"])
    monkeypatch.setattr(evidence_agent, "gather", capture)
    monkeypatch.setattr(models.Model, "__init__", lambda self, config: self.__dict__.update(name="m", vision=True))
    monkeypatch.setattr(models.Model, "json", answer)
    await pipeline.score_workspace("Make a click game", tmp_path, {"producer": {}})
    assert order[:3] == ["walk started", "walk done", "criteria written"]   # it did not wait for the criteria
    assert given == [({"tool": "browser", "page": "index.html"}, {"tool": "browser", "page": "index.html"}, [])]


@pytest.mark.asyncio
async def test_a_failed_rubric_cancels_the_default_walk(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<p>x</p>")
    cancelled = []

    async def walk(root, log, errors=None):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    async def answer(self, system, user, images=()):
        await asyncio.sleep(0.05)
        raise RuntimeError("model down")

    monkeypatch.setattr(pipeline, "default_walk", walk)
    monkeypatch.setattr(checks, "available_tools", lambda root, config: ["read", "python", "browser"])
    monkeypatch.setattr(models.Model, "__init__", lambda self, config: self.__dict__.update(name="m", vision=True))
    monkeypatch.setattr(models.Model, "json", answer)
    with pytest.raises(evidence_agent.EvidenceError, match="model down"):
        await asyncio.wait_for(pipeline.score_workspace("Make a game", tmp_path, {"producer": {}}), 5)
    assert cancelled == [True]


def test_a_slow_first_cdn_download_has_a_minute_and_is_cached(tmp_path, monkeypatch):
    import inspect
    import urllib.request

    monkeypatch.setenv("REWARD_HOME", str(tmp_path))
    asked = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"lib"

    def urlopen(url, timeout=None):
        asked.append(timeout)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    url = "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js"
    assert checks.cdn_file(url) == b"lib" and checks.cdn_file(url) == b"lib"
    assert asked == [checks.CDN_SECONDS] == [60]                       # downloaded once, with a minute for it
    assert checks.PAGE_LOAD_MS == 60000                                  # the page may wait that long for it
    assert "timeout=PAGE_LOAD_MS" in inspect.getsource(checks.run_experiment)


@pytest.mark.asyncio
async def test_no_browser_on_the_machine_falls_back_to_chrome_then_edge_without_a_model_call():
    playwright_error = pytest.importorskip("playwright.async_api").Error
    tried = []

    class Chromium:
        async def launch(self, **kwargs):
            tried.append(kwargs.get("channel"))
            if kwargs.get("channel") != "msedge":
                raise playwright_error("Executable doesn't exist at .../chrome-headless-shell")
            return "edge"

    pw = type("PW", (), {"chromium": Chromium()})()
    assert await checks.launch_browser(pw) == "edge" and tried == [None, "chrome", "msedge"]
    tried.clear()
    async def never(self, **kwargs):
        tried.append(kwargs.get("channel"))
        raise playwright_error(f"cannot start {kwargs.get('channel')}")

    Chromium.launch = never
    with pytest.raises(RuntimeError, match="no browser could start.*cannot start None"):
        await checks.launch_browser(pw)   # the first failure is the one reported
    assert tried == [None, "chrome", "msedge"]


@pytest.mark.asyncio
async def test_what_keeps_the_work_unseen_is_said_as_it_happens(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<p>x</p>")
    lines = []

    async def walk(root, log, errors=None):
        errors.append("RuntimeError: no browser could start")
        return None

    async def blind(model, log=None, notes=None):
        return False

    async def gather(criteria, ctx, route, log, limits=None, prefetched=()):
        failed = {"tool": "browser", "could_not_run": "RuntimeError: no browser could start"}
        return {c["id"]: ([failed], []) for c in criteria}

    async def answer(self, system, user, images=()):
        if system.startswith("You write evaluation criteria"):
            return {"criteria": [{"id": "play", "claims": [{"text": "Scores", "level": "floor"}]}]}
        return {"claims": [{"index": 0, "status": "unknown"}], "summary": "s"}

    monkeypatch.setattr(pipeline, "default_walk", walk)
    monkeypatch.setattr(models, "can_see", blind)
    monkeypatch.setattr(checks, "available_tools", lambda root, config: ["read", "python", "browser"])
    monkeypatch.setattr(evidence_agent, "gather", gather)
    monkeypatch.setattr(models.Model, "__init__", lambda self, config: self.__dict__.update(name="glm", vision=None))
    monkeypatch.setattr(models.Model, "json", answer)
    result = await pipeline.score_workspace("Make a click game", tmp_path, {"producer": {}},
                                            RewardOptions(log=lines.append))
    said = [line[len(pipeline.WARNING):] for line in lines if line.startswith(pipeline.WARNING)]
    assert any("判定模型 glm 看不到图片" in s for s in said)
    assert any("画面维度不评分" in s for s in said)
    assert any(s.startswith("默认浏览没能运行（RuntimeError: no browser could start）") for s in said)
    assert any(s.startswith("浏览器检查全部没能运行") for s in said)
    assert result["meta"]["warnings"] == said                                 # the same notes, once each
    looks, = [c for c in result["categories"] if c["category_id"] == "visual"]
    assert looks["unscored"] == "blind" and looks["score"] is None


@pytest.mark.asyncio
async def test_a_probe_that_never_returns_cannot_hold_the_reward(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    (tmp_path / "game.html").write_text("<script>var over = false;</script><p>play</p>")
    monkeypatch.setattr(checks, "PROBE_SECONDS", 1)
    check = {"tool": "browser", "page": "game.html", "snapshots": [2], "actions": [
        {"type": "probe", "script": "() => 1"},
        {"type": "probe", "script": "() => { while (!over) {} return 'game over'; }"},   # plays until game over
        {"type": "probe", "script": "() => 3"}]}
    facts, shots = await asyncio.wait_for(checks.run_check(check, tmp_path, ["browser"]), 60)
    first, looped = facts["actions"]
    assert first["value"] == 1 and not looped["ok"] and "did not return within 1 s" in looped["error"]
    assert "page_stuck" in facts and [label for label, _ in shots] == ["loaded"]   # nothing after it was waited on


@pytest.mark.asyncio
async def test_a_browser_check_has_an_overall_limit(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    (tmp_path / "game.html").write_text("<p>slow</p>")
    monkeypatch.setattr(checks, "BROWSER_CHECK_SECONDS", 2)
    check = {"tool": "browser", "page": "game.html", "actions": [{"type": "wait", "ms": 20000}]}
    facts, shots = await asyncio.wait_for(checks.run_check(check, tmp_path, ["browser"]), 60)
    assert facts["timed_out"] == 2 and shots == []


def _picture(path, size, color="red", kind="PNG"):
    from PIL import Image

    Image.new("RGB", size, color).save(path, kind)
    return path


def test_a_large_picture_is_shrunk_and_a_small_one_sent_as_it_is(tmp_path):
    import base64

    data, size = checks.picture_data(_picture(tmp_path / "poster.png", (3200, 1800)))
    assert size == "3200x1800" and base64.b64decode(data)[:2] == b"\xff\xd8"   # shrunk to a JPEG
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(base64.b64decode(data))) as shrunk:
        assert max(shrunk.size) == checks.PICTURE_SIDE
    small, _ = checks.picture_data(_picture(tmp_path / "icon.png", (64, 64)))
    assert models.data_url(small).startswith("data:image/png;")
    bmp, _ = checks.picture_data(_picture(tmp_path / "scan.bmp", (64, 64), kind="BMP"))
    assert models.data_url(bmp).startswith("data:image/jpeg;")                 # a format models do not take


@pytest.mark.asyncio
async def test_reading_a_picture_shows_it_to_the_judge(tmp_path):
    (tmp_path / "book").mkdir()
    _picture(tmp_path / "book" / "page-3.png", (120, 80))
    facts, shots = await checks.run_check({"tool": "read", "path": "book/page-3.png"}, tmp_path, ["read"])
    assert facts["picture"] == "120x80" and "Judge sees it as a picture" in facts["output"]
    assert [label for label, _ in shots] == ["file book/page-3.png"]
    facts, shots = await checks.run_check({"tool": "read", "path": "book/page-3.png"}, tmp_path,
                                          ["read", "browser"])
    assert shots                                                                 # pictures are not page source


@pytest.mark.asyncio
async def test_a_page_the_browser_cannot_open_falls_back_to_its_pictures(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<img src='p1.png'><img src='p2.png'>")
    _picture(tmp_path / "p1.png", (40, 40))
    _picture(tmp_path / "p2.png", (40, 40), "blue")
    lines, errors = [], []

    async def no_browser(page, experiment):
        raise RuntimeError("no browser could start")

    monkeypatch.setattr(checks, "run_experiment", no_browser)
    check, facts, shots = await pipeline.default_walk(tmp_path, lines.append, errors)
    assert check["tool"] == "read" and check["pictures"] == ["p1.png", "p2.png"]
    assert [label for label, _ in shots] == ["delivered picture p1.png", "delivered picture p2.png"]
    assert errors and "no browser could start" in errors[0]                     # the reason is kept
    (tmp_path / "p1.png").unlink()
    (tmp_path / "p2.png").unlink()
    assert await pipeline.default_walk(tmp_path, lines.append, []) is None      # a page alone: nothing to fall back to


@pytest.mark.asyncio
async def test_pictures_reach_the_model_with_their_own_type():
    seen = []

    class Completions:
        async def create(self, **kwargs):
            seen.append(kwargs["messages"][1]["content"])

            class Stream:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    if getattr(self, "done", False):
                        raise StopAsyncIteration
                    self.done = True
                    delta = type("D", (), {"content": "{}"})()
                    return type("C", (), {"choices": [type("Ch", (), {"delta": delta})()]})()

            return Stream()

    model = models.Model.__new__(models.Model)
    model.name, model.extra = "m", {}
    model.client = type("Client", (), {"chat": type("Chat", (), {"completions": Completions()})()})()
    png = "iVBORw0KGgoAAAANSUhEUg"
    await model.json("sys", "user", [("page 1", png), ("shot", "/9j/4AAQ")])
    urls = [part["image_url"]["url"] for part in seen[0] if part["type"] == "image_url"]
    assert urls[0].startswith("data:image/png;base64,") and urls[1].startswith("data:image/jpeg;base64,")
