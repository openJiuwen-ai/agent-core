# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PA-17 execution log summary: reading, contract checks and the summary given to the Agent."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.store import InMemoryKVStore
from openjiuwen.core.single_agent import create_agent_session
from openjiuwen.harness.persistent_agent.execution import execution_log as LOG
from openjiuwen.harness.persistent_agent.ports import PersistentPorts
from openjiuwen.harness.persistent_agent.schema.build_context import PersistentBuildContext
from openjiuwen.harness.persistent_agent.schema.events import AgentEvent, EventType
from openjiuwen.harness.persistent_agent.schema.execution import ExecutionContext

pytestmark = pytest.mark.level0

WHEN = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
SECRET = "SECRET-CONTENT"
TARGET = "execution-2"


def execution(**changes):
    fields = dict(
        namespace_id="tenant",
        agent_id="agent-a",
        session_id="session-a",
        request_id="request-1",
        execution_id="execution-3",
        config_version="v1",
        config_fingerprint="0" * 64,
        source_ref="source:message-1",
        principal_ref="user",
        startup_scope=frozenset({"read"}),
        source_scope=frozenset({"read"}),
        policy_revision="p1",
        capability_snapshot=frozenset({"construction"}),
        recovery_ref=TARGET,
    )
    fields.update(changes)
    return ExecutionContext(**fields)


CURRENT = execution()
OLD = replace(CURRENT, execution_id=TARGET, recovery_ref=None)


class Commit:
    def __init__(self, log=(), error=None):
        self.log = log
        self.error = error
        self.calls = []

    async def record(self, *args):
        raise AssertionError("summarizing records nothing")

    async def read_execution_log(self, current, execution_id):
        self.calls.append((current, execution_id))
        if self.error is not None:
            raise self.error
        return self.log


class Budget:
    async def admit(self, *args):
        raise AssertionError("no model call")

    async def report(self, *args):
        raise AssertionError("no model call")


def build_context(commit):
    return PersistentBuildContext(
        execution=CURRENT,
        session=create_agent_session(session_id=CURRENT.session_id),
        ports=PersistentPorts(commit=commit, budget=Budget(), window_backend=InMemoryKVStore()),
    )


async def summarize(log=(), error=None, **options):
    commit = Commit(log, error)
    summary = await LOG.summarize_execution(build_context(commit), TARGET, **options)
    assert commit.calls == [(CURRENT, TARGET)], "reads the target Execution under the current invocation"
    return summary


def event(seq, kind, payload, source=OLD):
    return AgentEvent.create(source, event_type=kind, local_seq=seq, occurred_at=WHEN, payload=payload)


def intent(seq, op, name="submit_reminder", args="单号=BX-0930", call=None, target=None):
    payload = {
        "operation_id": op,
        "tool_call_id": call or f"call-{op}",
        "tool_name": name,
        "target_ref": target,
        "params_digest": "a" * 64,
        "args_summary": args,
    }
    return event(seq, EventType.TOOL_INTENT, payload)


def completed(seq, name, status="succeeded", op=None, result="完成", reason=None, args="x", call=None, refs=()):
    payload = {
        "tool_call_id": call or (f"call-{op}" if op else f"call-{seq}"),
        "tool_name": name,
        "args_summary": args,
        "status": status,
        "result_summary": result,
        "result_refs": refs,
        "operation_id": op,
    }
    if status != "succeeded":
        payload["reason"] = reason or "timed out"
    return event(seq, EventType.TOOL_COMPLETED, payload)


def progress(seq, text):
    return event(seq, EventType.PROGRESS_UPDATED, {"text": text})


def answer(seq, output_id, text="回答正文"):
    payload = {"output_id": output_id, "target_ref": "chat-1", "share_mode": "message", "text": text}
    return event(seq, EventType.OUTPUT_PROPOSED, payload)


def shared_file(seq, output_id, path="reports/q3.md", mode="upload", caption=None):
    payload = {
        "output_id": output_id,
        "target_ref": "chat-1",
        "share_mode": mode,
        "file_ref": "sha256:" + "b" * 64,
        "file_path": path,
        "file_size": 13,
    }
    if caption:
        payload["text"] = caption
    return event(seq, EventType.OUTPUT_PROPOSED, payload)


def final(seq, refs=(), status="completed"):
    payload = {"status": status, "output_refs": refs}
    if status != "completed":
        payload["reason"] = "cancelled by request"
    return event(seq, EventType.EXECUTION_FINAL, payload)


def paused(seq, reason="error", refs=(), detail="model failed"):
    return event(seq, EventType.EXECUTION_PAUSED, {"reason": reason, "detail": detail, "output_refs": refs})


def capture(monkeypatch):
    """Diagnostics as (level, text); the summary never logs anything else."""
    logged = []
    for level in ("warning", "error"):
        monkeypatch.setattr(
            LOG.logger, level, lambda message, *args, level=level: logged.append((level, message % args))
        )
    return logged


def noise(seq):
    usage = {"status": "known", "input_tokens": 1, "output_tokens": 2}
    kinds = [
        (EventType.EXECUTION_STARTED, {"goal": SECRET}),
        (EventType.REPLY_DELTA, {"text": SECRET}),
        (EventType.MODEL_COMPLETED, {"model_call_id": "mc-" + SECRET, "config_version": "v1", "usage": usage}),
    ]
    kind, payload = kinds[seq % 3]
    return event(seq, kind, payload)


@pytest.mark.asyncio
async def test_a_full_log_lists_tools_conclusions_and_candidates_then_the_ending():
    """PA-17-V-01."""
    log = (
        noise(1),
        noise(2),
        noise(3),
        completed(4, "read_file", args="reports/0930.xlsx", result="已读取，共 12 行"),
        progress(5, "审批停在财务复核环节"),
        intent(6, "op_12", target="reminder:BX-0930"),
        completed(7, "submit_reminder", op="op_12", args="单号=BX-0930", result="已提醒", refs=("receipt:77",)),
        answer(8, "out_a"),
        final(9, refs=("out_a",)),
    )
    summary = await summarize(log)
    assert summary.missing is False
    assert summary.text.split("\n") == [
        "上一次执行 execution-2（请求 request-1）的执行日志整理：",
        "1. [成功] read_file(reports/0930.xlsx) → 已读取，共 12 行（调用 call-4）",
        "2. [结论] 审批停在财务复核环节",
        "3. [成功] submit_reminder(单号=BX-0930) → 已提醒（结果引用 receipt:77）"
        "（写操作 op_12，调用 call-op_12，目标 reminder:BX-0930）",
        "4. [交出候选] 最终回答第 1 段（out_a，目标 chat-1，message）："
        "被结束记录“已完成”引用（服务端负责投递，不代表已送达）：回答正文",
        "结局：已完成；引用候选 out_a",
        "归档：日志中没有换窗记录（仅为日志中的归档引用，尚未按 execution_id 查询，可能不全）",
        LOG._FOOTER,
    ]
    assert SECRET not in summary.text


@pytest.mark.asyncio
async def test_an_empty_log_is_a_missing_record(monkeypatch):
    """PA-17-V-02."""
    logged = capture(monkeypatch)
    summary = await summarize(())
    [(level, text)] = logged
    assert summary.missing is True and level == "warning" and "log is empty" in text, "nothing saved can be real"
    assert summary.text == f"上一次执行 {TARGET}：{LOG._MISSING}"


@pytest.mark.asyncio
async def test_a_failed_read_is_a_missing_record_without_host_detail(monkeypatch):
    """PA-17-V-03."""
    logged = capture(monkeypatch)
    summary = await summarize(error=PermissionError(SECRET))
    assert summary.missing is True and SECRET not in summary.text
    [(level, text)] = logged
    assert level == "warning" and SECRET not in text and "PermissionError" in text and TARGET in text


@pytest.mark.asyncio
async def test_cancellation_propagates():
    with pytest.raises(asyncio.CancelledError):
        await summarize(error=asyncio.CancelledError())


@pytest.mark.parametrize("detail", ["full", "brief", "missing"])
@pytest.mark.asyncio
async def test_log_material_leaves_asking_the_initiator_to_the_model(detail):
    if detail == "missing":
        summary = await summarize(error=OSError("not available"))
    else:
        summary = await summarize((progress(1, "核对进度"),), detail=detail)
    assert "查不清时可以询问发起人，是否询问由模型判断" in summary.text
    assert "不能直接重做" in summary.text
    assert "查不清询问发起人" not in summary.text


@pytest.mark.parametrize("bad", ["", "has space", "a\x00b", "a\nb", "x" * 513, None, 123, b"execution-2"])
@pytest.mark.asyncio
async def test_an_invalid_execution_id_is_rejected_before_the_port(bad):
    """PA-17-V-15: the Identifier constraint applies; an argument error is not a missing record."""
    commit = Commit()
    with pytest.raises(BaseError) as exc_info:
        await LOG.summarize_execution(build_context(commit), bad)
    assert exc_info.value.status == StatusCode.DEEPAGENT_INPUT_PARAM_ERROR
    assert commit.calls == [], "the port is not called"


@pytest.mark.asyncio
async def test_a_wrong_context_is_rejected_before_the_port():
    with pytest.raises(BaseError) as exc_info:
        await LOG.summarize_execution(object(), TARGET)
    assert exc_info.value.status == StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR


OTHERS = {
    "namespace": {"namespace_id": "other"},
    "agent": {"agent_id": "agent-b"},
    "session": {"session_id": "session-b"},
    "request": {"request_id": "request-9"},
}


@pytest.mark.parametrize(
    "bad",
    [
        *OTHERS,
        "execution",
        "mixed_source",
        "not_an_event",
        "same_event_id",
        "same_local_seq",
        "none",
        "text",
        "mapping",
        "list",
    ],
)
@pytest.mark.asyncio
async def test_a_log_that_breaks_the_contract_is_not_used_at_all(bad, monkeypatch):
    """PA-17-V-04: nothing from such a log reaches the summary or the diagnostics."""
    logged = capture(monkeypatch)
    good = (progress(1, SECRET), completed(2, "read_file", result=SECRET))
    if bad in OTHERS:
        source = replace(OLD, **OTHERS[bad])
        log = good + (event(3, EventType.PROGRESS_UPDATED, {"text": SECRET}, source=source),)
    elif bad == "execution":
        log = good + (event(3, EventType.PROGRESS_UPDATED, {"text": SECRET}, source=replace(OLD, execution_id="e-1")),)
    elif bad == "mixed_source":
        log = good + (event(3, EventType.PROGRESS_UPDATED, {"text": SECRET}, source=replace(OLD, source_ref="s-2")),)
    elif bad == "not_an_event":
        log = good + ({"event_type": "progress.updated", "payload": {"text": SECRET}},)
    elif bad == "same_event_id":
        log = good + (progress(1, SECRET + " changed"),)
    elif bad == "same_local_seq":
        log = good + (event(1, EventType.PROGRESS_UPDATED, {"text": SECRET}, source=replace(OLD, source_ref="s-2")),)
    else:
        log = {"none": None, "text": SECRET, "mapping": {"events": [SECRET]}, "list": list(good)}[bad]
    summary = await summarize(log)
    assert summary.missing is True and summary.text == f"上一次执行 {TARGET}：{LOG._MISSING}"
    [(level, text)] = logged
    assert level == "error" and SECRET not in text, "a log that breaks the port contract is an error"
    category = {
        "none": "not a tuple",
        "text": "not a tuple",
        "mapping": "not a tuple",
        "list": "not a tuple",
        "mixed_source": "different sources",
    }.get(bad)
    assert category is None or category in text


@pytest.mark.asyncio
async def test_the_earlier_execution_may_have_its_own_source():
    """PA-17-V-04: one source per log; it need not be the current Execution's."""
    own = replace(OLD, source_ref="source:message-0")
    log = (
        event(1, EventType.PROGRESS_UPDATED, {"text": "a"}, source=own),
        event(2, EventType.PROGRESS_UPDATED, {"text": "b"}, source=own),
    )
    summary = await summarize(log)
    assert summary.missing is False and "[结论] b" in summary.text


@pytest.mark.asyncio
async def test_gaps_and_a_missing_ending_are_stated_but_the_log_is_used():
    """PA-17-V-05."""
    summary = await summarize((progress(1, "a"), progress(4, "b"), progress(5, "c"), progress(9, "d")))
    assert summary.missing is False
    lines = summary.text.split("\n")
    assert lines[1] == "日志缺少第 2～3、6～8 条记录。"
    assert "结局：未记录结束；无法仅凭日志确定执行是否已结束。" in lines
    single = await summarize((progress(1, "a"), progress(3, "b"), final(4)))
    assert "日志缺少第 2 条记录。" in single.text


@pytest.mark.asyncio
async def test_each_write_keeps_its_status_and_none_is_called_done():
    """PA-17-V-06."""
    log = (
        intent(1, "op_a", "send_mail"),
        intent(2, "op_b", "upload_report"),
        completed(3, "upload_report", status="unknown", op="op_b", result="上传超时", reason="timed out"),
        intent(4, "op_c", "delete_draft"),
        completed(5, "delete_draft", status="failed", op="op_c", result="权限不足", reason="forbidden"),
        completed(6, "write_file", op="op_d", result="已写入"),
        completed(7, "read_file", status="unknown", result="读取超时", reason="timed out"),
        completed(8, "custom_tool", result="已处理"),
    )
    lines = (await summarize(log)).text.split("\n")
    assert lines[1] == (
        "1. [已登记，无结果] send_mail(单号=BX-0930)（写操作 op_a，调用 call-op_a）：结果不明，先查证，不能直接重做"
    )
    assert lines[2] == (
        "2. [结果不明] upload_report(单号=BX-0930) → 上传超时（原因：timed out）（写操作 op_b，调用 call-op_b）："
        "先查证，不能直接重做"
    )
    assert lines[3] == (
        "3. [失败] delete_draft(单号=BX-0930) → 权限不足（原因：forbidden）（写操作 op_c，调用 call-op_c）："
        "失败不代表没有副作用，必要时查证"
    )
    assert lines[4] == "4. [成功] write_file(x) → 已写入（写操作 op_d，调用 call-op_d）（未见对应的意图记录）"
    assert lines[5] == "5. [结果不明] read_file(x) → 读取超时（原因：timed out）（调用 call-7）"
    assert lines[6] == "6. [成功] custom_tool(x) → 已处理（写操作，调用 call-8）（未见对应的意图记录）", (
        "not on the read-only list"
    )


@pytest.mark.asyncio
async def test_a_result_is_merged_only_when_it_matches_one_intent():
    """PA-17-V-11: a reused operation_id never lets a result stand for a later pending intent."""
    reused = (
        intent(1, "op_r", "send_mail", args="第一次"),
        completed(2, "send_mail", op="op_r", result="已发送"),
        intent(3, "op_r", "send_mail", args="第二次"),
    )
    lines = (await summarize(reused)).text.split("\n")
    assert lines[1] == "1. [成功] send_mail(第一次) → 已发送（写操作 op_r，调用 call-op_r）", "merged before the reuse"
    assert lines[2].startswith("2. [已登记，无结果] send_mail(第二次)"), "the later intent stays unresolved"
    assert "operation_id 与其他调用重复，结果无法唯一关联" in lines[2]

    late = reused + (completed(4, "send_mail", op="op_r", result="已发送"),)
    summary = await summarize(late)
    lines = summary.text.split("\n")
    assert summary.missing is False
    assert lines[2].startswith("2. [已登记，无结果] send_mail(第二次)"), "a late result of A does not resolve B"
    assert lines[3].startswith("3. [成功] send_mail(x) → 已发送") and "无法确定对应哪次意图，未合并" in lines[3]

    ambiguous = (
        intent(1, "op_r", "send_mail", args="甲"),
        intent(2, "op_r", "send_mail", args="乙"),
        completed(3, "send_mail", op="op_r", result="已发送"),
    )
    lines = (await summarize(ambiguous)).text.split("\n")
    assert lines[1].startswith("1. [已登记，无结果] send_mail(甲)") and "结果无法唯一关联" in lines[1]
    assert lines[2].startswith("2. [已登记，无结果] send_mail(乙)") and "结果无法唯一关联" in lines[2]
    assert lines[3].startswith("3. [成功] send_mail(x) → 已发送") and "无法确定对应哪次意图，未合并" in lines[3]

    orphan = (
        completed(1, "send_mail", op="op_r", result="已发送"),
        intent(2, "op_r", "send_mail", args="第二次"),
        completed(3, "send_mail", op="op_r", result="已发送"),
    )
    lines = (await summarize(orphan)).text.split("\n")
    assert lines[1].startswith("1. [成功] send_mail(x) → 已发送") and "未见对应的意图记录" in lines[1]
    assert lines[2].startswith("2. [已登记，无结果] send_mail(第二次)"), "an intent after a result means a reused ID"
    assert "结果无法唯一关联" in lines[2]
    assert lines[3].startswith("3. [成功] send_mail(x)") and "无法确定对应哪次意图，未合并" in lines[3]

    repeated = (
        intent(1, "op_r"),
        completed(2, "submit_reminder", op="op_r"),
        completed(3, "submit_reminder", op="op_r"),
    )
    lines = (await summarize(repeated)).text.split("\n")
    assert "该 operation_id 已有一条结果，本条未合并" in lines[2]


@pytest.mark.parametrize("change", [{"call": "call-other"}, {"name": "other_tool"}])
@pytest.mark.asyncio
async def test_a_result_whose_call_identity_differs_is_kept_apart(change):
    """PA-17-V-14: tool_call_id and tool name must agree before a result is paired with an intent."""
    result = completed(
        2, change.get("name", "send_mail"), op="op_x", result="已发送", call=change.get("call", "call-op_x")
    )
    summary = await summarize((intent(1, "op_x", "send_mail"), result))
    lines = summary.text.split("\n")
    assert summary.missing is False, "an ambiguous pairing is an incomplete log, not a missing one"
    assert (
        lines[1].startswith("1. [已登记，无结果] send_mail(单号=BX-0930)")
        and "调用身份不一致的结果，未合并" in lines[1]
    )
    assert lines[2].startswith("2. [成功]") and "调用身份与意图不一致（tool_call_id 或工具名），未合并" in lines[2]


@pytest.mark.parametrize("ending", ["final", "paused", "none"])
@pytest.mark.asyncio
async def test_candidates_say_whether_an_ending_references_them_never_whether_delivered(ending):
    """PA-17-V-07: the full text is kept; paused and final references count alike."""
    log = [
        answer(1, "out_a", "第一段\n第二行"),
        answer(2, "out_b", "第二段"),
        shared_file(3, "out_f", caption="季度报告"),
        shared_file(4, "out_x", "notes.txt", "attachment"),
    ]
    if ending == "final":
        log.append(final(5, refs=("out_a", "out_b", "out_f")))
    elif ending == "paused":
        log.append(paused(5, refs=("out_f",)))
    text = (await summarize(tuple(log))).text
    lines = text.split("\n")
    assert lines[1].startswith("1. [交出候选] 最终回答第 1 段（out_a，目标 chat-1，message）")
    assert lines[2:4] == ["    第一段", "    第二行"], "the answer is kept line for line"
    assert "最终回答第 2 段（out_b，目标 chat-1，message）" in lines[4] and lines[4].endswith("：第二段")
    assert f"文件 reports/q3.md（out_f，目标 chat-1，upload，sha256:{'b' * 64}，13 字节）" in lines[5]
    assert lines[5].endswith("：季度报告")
    assert "文件 notes.txt（out_x，目标 chat-1，attachment" in lines[6]
    referenced = {"final": {1, 4, 5}, "paused": {5}, "none": set()}[ending]
    for number, line in ((1, lines[1]), (4, lines[4]), (5, lines[5]), (6, lines[6])):
        if ending == "none":
            assert "未见结束记录，无法判断是否被引用" in line
        elif number in referenced:
            assert "引用（服务端负责投递，不代表已送达）" in line
        else:
            assert "未被结束记录引用，服务端不会投递" in line
    assert "已投递" not in text and "已送达）" not in text.replace("不代表已送达）", "")


@pytest.mark.asyncio
async def test_endings_that_disagree_or_cite_unseen_candidates_are_stated():
    """PA-17-V-12."""
    text = (await summarize((answer(1, "out_a"), paused(2, refs=()), final(3, refs=("out_a", "out_z"))))).text
    assert "结局记录冲突，无法确定以哪一条为准：“暂停（error）：model failed”；“已完成；引用候选 out_a、out_z”" in text
    assert "结束记录引用了 out_z，但日志中未见对应候选。" in text


@pytest.mark.asyncio
async def test_a_long_log_is_not_cut_and_every_write_is_kept():
    """PA-17-V-08: no length limit yet (D-05); nothing is left out or merged."""
    log, seq = [], 0
    for index in range(60):
        seq += 1
        log.append(completed(seq, "read_file", args=f"file-{index}.md " + "r" * 400, result="x" * 400))
        seq += 1
        log.append(progress(seq, f"结论 {index} " + "c" * 480))
        seq += 1
        log.append(intent(seq, f"op_{index}", "send_mail", "m" * 480))
    text = (await summarize(tuple(log))).text
    assert len(text) > 50000
    assert text.count("[成功] read_file(") == 60 and text.count("[结论] 结论 ") == 60
    assert text.count("（写操作 op_") == 60
    assert "省略" not in text and "[只读]" not in text


@pytest.mark.asyncio
async def test_a_log_of_only_display_and_usage_events_is_used_and_says_so():
    """PA-17-V-13."""
    summary = await summarize((noise(1), noise(2), noise(3)))
    assert summary.missing is False
    assert LOG._NOTHING in summary.text and SECRET not in summary.text


@pytest.mark.asyncio
async def test_archives_are_those_the_log_names_and_say_no_query_was_made():
    """PA-17-V-09."""
    switched = event(1, EventType.WINDOW_SWITCHED, {"archive_ref": "archive#7", "window_seq": 2})
    again = event(3, EventType.WINDOW_SWITCHED, {"archive_ref": "archive#8", "window_seq": 3})
    text = (await summarize((switched, progress(2, "a"), again, final(4)))).text
    assert "归档：archive#7、archive#8（仅为日志中的归档引用，尚未按 execution_id 查询，可能不全）" in text


@pytest.mark.asyncio
async def test_identical_events_recorded_twice_count_once():
    """PA-17-V-10: a retried ending and its outputs may be stored again with the same identity."""
    out, end = answer(1, "out_a"), final(2, refs=("out_a",))
    summary = await summarize((end, out, out, end))
    assert summary.missing is False and summary.text.count("[交出候选]") == 1 and "日志缺少" not in summary.text


# F_20 D-08: the brief summary a continuation starts from, and the full one its record tool pages.


def brief_log():
    return (
        noise(1),
        completed(2, "read_file", result="已读取"),
        completed(3, "read_file", result="已读取"),
        completed(4, "list_files", result="3 个文件"),
        completed(5, "share_file", result="已登记 reports/q3.md"),
        intent(6, "op_a", "send_mail"),
        intent(7, "op_b", "submit_reminder"),
        completed(8, "submit_reminder", op="op_b", result="已提醒"),
        completed(9, "upload_report", status="unknown", op="op_c", result="超时", reason="timed out"),
        progress(10, "审批停在财务复核环节"),
        answer(12, "out_a", text="回答" * 300),
        final(13, refs=("out_a",)),
    )


@pytest.mark.asyncio
async def test_a_brief_summary_keeps_every_write_outcome_and_gap_and_counts_reads():
    brief = (await summarize(brief_log(), detail="brief")).text
    lines = brief.split("\n")
    assert lines[0] == "执行 execution-2（请求 request-1）的执行日志摘要："
    assert lines[1] == "日志缺少第 11 条记录。" and lines[2] == LOG._OMITTED, "gaps and omissions are told apart"
    assert "[成功] share_file(x) → 已登记 reports/q3.md（调用 call-5）" in brief, "listings are kept one by one"
    assert "[已登记，无结果] send_mail(单号=BX-0930)（写操作 op_a" in brief
    assert "[成功] submit_reminder(单号=BX-0930) → 已提醒（写操作 op_b" in brief
    assert "[结果不明] upload_report(x) → 超时（原因：timed out）（写操作 op_c" in brief
    assert "[结论] 审批停在财务复核环节" in brief
    assert "[只读操作] read_file 2 次、list_files 1 次（明细未列出）" in brief
    assert "out_a" in brief and "（正文 600 字，未列出）" in brief and "回答回答" not in brief
    assert "结局：已完成；引用候选 out_a" in brief and brief.endswith(LOG._FOOTER)


@pytest.mark.asyncio
async def test_a_brief_summary_with_nothing_left_out_does_not_say_so():
    brief = (await summarize((intent(1, "op_a", "send_mail"), final(2)), detail="brief")).text
    assert LOG._OMITTED not in brief and "[只读操作]" not in brief


@pytest.mark.asyncio
async def test_an_unknown_detail_is_rejected_before_the_port():
    commit = Commit((progress(1, "a"),))
    raw_detail: Any = "short"  # Deliberately invalid input at the runtime boundary.
    with pytest.raises(BaseError) as exc_info:
        await LOG.summarize_execution(build_context(commit), TARGET, detail=raw_detail)
    assert exc_info.value.status == StatusCode.DEEPAGENT_INPUT_PARAM_ERROR
    assert commit.calls == []


@pytest.mark.asyncio
async def test_record_blocks_join_into_the_full_summary_after_the_same_checks():
    commit = Commit(brief_log())
    blocks, missing = await LOG.record_blocks(build_context(commit), TARGET)
    assert missing is False and "\n".join(blocks) == (await summarize(brief_log())).text
    assert any("回答回答" in block for block in blocks), "the full record keeps the bodies"
    missing_blocks, missing = await LOG.record_blocks(build_context(Commit(())), TARGET)
    assert missing is True and missing_blocks == (f"上一次执行 {TARGET}：{LOG._MISSING}",)
