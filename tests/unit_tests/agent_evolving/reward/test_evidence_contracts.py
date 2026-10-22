"""Cross-boundary evidence contracts: reuse, cancellation, vision and concurrent language."""

import asyncio
import json
import shutil

import pytest

from openjiuwen.agent_evolving.reward import checks, evidence_agent, models, pipeline, workspace


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
                                            txt_response=reply, criteria=[criterion])
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

    async def capture(criteria, ctx, *args):
        seeds.append(ctx["plan_input"]["workspace"])
        return {c["id"]: ([], []) for c in criteria}

    monkeypatch.setattr(evidence_agent, "gather", capture)
    monkeypatch.setattr(models.Model, "__init__", lambda self, config: self.__dict__.update(name="m", vision=False,
                                                                                         quota=60))

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
