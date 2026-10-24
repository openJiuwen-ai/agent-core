# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""The Reward package as the Reward stage: its result converted to ``attribution.reward.v1``.

The Reward package (:mod:`openjiuwen.agent_evolving.reward`) writes checkable criteria from the user's own
request (what was asked, plus what the best work of the kind does), gathers evidence by running the
deliverable, judges every claim and computes the scores in code.  This module feeds it the stage-one
result and turns its claims into reward dimensions, so the rest of the pipeline (signals, attribution,
the host's display) is unchanged.

The tier of each dimension comes from the claim's status, never from a score: a met claim is perfect
(attribution skips it); a requirement partly met is fair and one not met is weak; a proactive challenge
not (fully) met is fair (room to improve, not a defect).  The dimension score is Reward's own claim
score (met 100, partial 50, unmet 0), shown only.  A claim the evidence could not settle ("unknown") is no
dimension; the overall comment counts it.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from openjiuwen.agent_evolving.attribution.model import TaskParseResult
from openjiuwen.agent_evolving.attribution.nodes import TrajectoryNode

#: ``ATTRIBUTION_REWARD=agent`` switches the Reward stage back to the Reward deep agent.
REWARD_ENGINE_ENV = "ATTRIBUTION_REWARD"

_REQUIREMENT_TIER = {"met": "perfect", "partial": "fair", "unmet": "weak"}
_CHALLENGE_TIER = {"met": "perfect", "partial": "fair", "unmet": "fair"}
# A claim's score is shown beside its tier, and the frontend colours a dimension by score band
# (>= 90 perfect, >= 75 fair, else weak): each score sits in the band of the claim's tier.
_REQUIREMENT_SCORE = {"met": 100, "partial": 75, "unmet": 0}
_CHALLENGE_SCORE = {"met": 100, "partial": 80, "unmet": 75}
_TIER_ORDER = {"weak": 0, "fair": 1, "perfect": 2}
_LABELS = {
    "zh": {
        "floor": "要求",
        "challenge": "挑战",
        "counts": "要求 {floor_met}/{floor} 满足 · 挑战 {challenge_done}/{challenge} 做到",
        "unknown": "另有 {count} 项因证据不足未能判定。",
        "blind_unknown": "所用模型 {model} 不支持读图，需要看图才能判定的子项也在其中。",
        "unscored_blind": "画面维度「{name}」未评：所用模型 {model} 不支持读图，Reward 无法评价需要看图的维度；"
        "这不代表作品有问题，换用能读图的模型即可评价。",
        "unscored_no_screenshots": "画面维度「{name}」未评：没有拿到作品的截图（浏览器没能运行或没有截图）。",
        "sent_files": "（随回复已作为附件发送给用户的文件，用户在对话中能直接看到、打开：{files}）",
        "check_failed": "取证：第 {count} 项检查没能运行（{tool}）：{reason}",
        "batch": "取证第 {batch} 轮：{count} 项检查完成（{tools}），累计 {total} 项",
        "batch_skipped": "；{count} 项按规则不执行（{reason}）",
        "page_source": "网页作品不读源码，改在浏览器里实际使用",
        "warning": "⚠ {text}",
        "criteria": "出题：根据用户的请求写判定项",
        "criteria_done": "出题完成：{count} 个维度",
        "evidence": "取证：实际运行交付物，覆盖 {count} 个维度",
        "evidence_done": "取证结束，共 {count} 项检查",
        "judging": "判定：逐条判定 0/{total} 个维度",
        "judged": "判定：{done}/{total} 个维度已判定",
        "finished": "完成：总分 {score}",
        "elapsed": "已用 {minutes}分{seconds:02d}秒",
        "task_note": "## 用户补充（最高优先级；与下面冲突时以此为准）",
        "task_list": "## 当前有效的需求清单（每一条都要有对应的要求项；标「用户纠正过」的优先）",
        "task_corrected": "（用户纠正过）",
        "task_words": "## 用户原话（按时间顺序，用来核对细节；与清单不一致时以清单为准）",
        "note_mark": "（用户补充）",
    },
    "en": {
        "floor": "Requirement",
        "challenge": "Challenge",
        "counts": "requirements {floor_met}/{floor} met · challenges {challenge_done}/{challenge} done",
        "unknown": "{count} more claims could not be settled.",
        "blind_unknown": "The model {model} cannot read images; claims that need seeing are among them.",
        "unscored_blind": 'Looks category "{name}" not scored: the model {model} cannot read images, so Reward '
        "cannot judge what needs seeing; this is no fault of the work, and a model that reads images can judge it.",
        "unscored_no_screenshots": 'Looks category "{name}" not scored: no screenshot of the work was taken '
        "(the browser did not run or took none).",
        "sent_files": "(Files sent to the user with the reply, shown in the chat as attachments the user can open: "
        "{files})",
        "check_failed": "Evidence: check {count} could not run ({tool}): {reason}",
        "batch": "Evidence round {batch}: {count} checks done ({tools}), {total} in all",
        "batch_skipped": "; {count} not run by rule ({reason})",
        "page_source": "a page's source is not read; it is used in the browser",
        "warning": "⚠ {text}",
        "criteria": "Criteria: writing claims from the user's request",
        "criteria_done": "Criteria written: {count} criteria",
        "evidence": "Evidence: running the deliverable for {count} criteria",
        "evidence_done": "Evidence done: {count} checks",
        "judging": "Judging: 0/{total} criteria",
        "judged": "Judging: {done}/{total} criteria judged",
        "finished": "Done: total {score}",
        "elapsed": "{minutes}m{seconds:02d}s",
        "task_note": "## The reviewer's note (highest priority; wins any conflict with what follows)",
        "task_list": "## Current requirements (each needs a requirement claim; points the user corrected first)",
        "task_corrected": " (corrected by the user)",
        "task_words": "## The user's own words (in order, to check details; the list wins where they differ)",
        "note_mark": " (reviewer's note)",
    },
}


def _labels(language: str) -> dict[str, str]:
    return _LABELS["en" if str(language).lower().startswith("en") else "zh"]


def reward_enabled() -> bool:
    """The Reward package scores the Reward stage unless ``ATTRIBUTION_REWARD=agent``."""
    return os.environ.get(REWARD_ENGINE_ENV, "reward").strip().lower() != "agent"


def route_from_model(model: Any) -> dict[str, Any]:
    """An OpenAI-compatible route (``model``, ``base_url``, ``api_key``) read from a core ``Model``.

    The model's own time limits are not carried over: Reward waits for a slow model.  A ``supports_vision``
    declared on the model (JiuwenSwarm's model config) becomes the route's ``vision``, so Reward does not probe.

    Raises:
        ValueError: The model carries no name or endpoint (e.g. a model group); set ``REWARD_CONFIG``.
    """
    client = getattr(model, "model_client_config", None)
    request = getattr(model, "model_config", None)
    name = str(getattr(request, "model_name", "") or "")
    base_url = str(getattr(client, "api_base", "") or "")
    if not name or not base_url:
        raise ValueError("the diagnosis model has no model name or endpoint; set REWARD_CONFIG to a config file")
    route: dict[str, Any] = {
        "model": name,
        "base_url": base_url,
        "api_key": str(getattr(client, "api_key", "") or ""),
        "provider": str(getattr(client, "client_provider", "") or "OpenAI"),
    }
    extra = getattr(request, "extra_body", None)
    if isinstance(extra, Mapping):
        route["extra_body"] = dict(extra)
    vision = getattr(client, "supports_vision", None)
    if isinstance(vision, bool):
        route["vision"] = vision
    return route


def reward_config(model: Any) -> dict[str, Any]:
    """The Reward config: ``$REWARD_CONFIG`` or ``~/.reward/config.json`` when present,
    else every model role on the diagnosis model. No time limit is set: Reward runs until it is done.
    """
    from openjiuwen.agent_evolving.reward.config import config_path, load_config

    if os.environ.get("REWARD_CONFIG") or config_path().is_file():
        config = dict(load_config())
    else:
        route = route_from_model(model)
        config = {"producer": route, "judge": route, "evidence_model": route, "criteria_budget": 3}
    return config


def existing_paths(paths: Sequence[str]) -> list[str]:
    """The paths that exist (a deliverable deleted since the run cannot be scored)."""
    return [path for path in paths if Path(path).exists()]


def final_reply(task_parse: TaskParseResult, nodes_by_subagent: Mapping[str, Sequence[TrajectoryNode]]) -> str:
    """The text the user got last: the latest message artifact, else the latest reply in the trajectory.

    Messages a team member sent inside the team are skipped while a message to the user exists.  When task
    parsing reported no message artifact, the last assistant message without a tool call from the main agent
    or the leader is the reply (an empty reply would make every claim about the reply unmet) -- unless the
    delivery round was decided (``delivery_round``): then the reply is the one task parsing took from it.
    """
    nodes = {node.node_id: node for rows in nodes_by_subagent.values() for node in rows}
    candidates = [(node_id, text) for node_id, text in task_parse.message_artifacts if text]
    if not candidates and task_parse.delivery_round is not None:
        return ""
    if not candidates:
        candidates = [
            (node.node_id, str(node.content))
            for node in nodes.values()
            if node.role == "assistant" and not node.tool_calls and node.content and node.kind != "team_member"
        ]
    to_user = [item for item in candidates if getattr(nodes.get(item[0]), "kind", "") != "team_member"]
    pool = to_user or candidates
    if not pool:
        return ""
    return max(pool, key=lambda item: getattr(nodes.get(item[0]), "start_ts_ns", 0))[1]


def with_sent_files(reply: str, task_parse: TaskParseResult, language: str) -> str:
    """The final reply, plus the files sent to the user with ``send_file_to_user``: the chat shows them as
    attachments, which the reply text alone does not say (a reply naming only the files would read as showing none).
    """
    sent = [
        Path(path).name
        for path, _, tool in getattr(task_parse, "file_candidates", None) or ()
        if tool == "send_file_to_user"
    ]
    if not sent:
        return reply
    names = ("、" if not str(language).lower().startswith("en") else ", ").join(dict.fromkeys(sent))
    note = _labels(language)["sent_files"].format(files=names)
    return f"{reply}\n\n{note}" if reply else note


def review_task(task_parse: TaskParseResult, user_note: str, language: str) -> str:
    """The task Reward writes its claims from: the reviewer's note, the current requirements, the user's words.

    The note given when starting the diagnosis comes first and wins any conflict; the requirement list (later
    changes applied, overridden demands dropped) is what every floor claim must cover, with the points the
    user corrected first; the user's own words follow to check details against.  Without a note and a list
    the task is the user's words as before.
    """
    words = task_parse.user_request or task_parse.task_description
    requirements = list(task_parse.requirements or ())
    note = (user_note or "").strip()
    if not note and not requirements:
        return words
    labels = _labels(language)
    sections = []
    if note:
        sections.append(f"{labels['task_note']}\n{note}")
    if requirements:
        ordered = sorted(requirements, key=lambda item: not item.get("corrected"))
        lines = [
            f"{index}. {item['text']}{labels['task_corrected'] if item.get('corrected') else ''}"
            for index, item in enumerate(ordered, 1)
        ]
        sections.append(labels["task_list"] + "\n" + "\n".join(lines))
    sections.append(f"{labels['task_words']}\n{words}")
    return "\n\n".join(sections)


class _ProgressLines:
    """Reward's progress as status lines: what it does now, a bar and the time used.

    Installed as Reward's progress object (its ``stage``/``tick``/``counted`` calls) and as its log
    (evidence checks are counted from it); every step the user would care about becomes one line.

    The evidence step has no known number of checks and no time limit, so its share of the bar follows a
    count of checks that slows down but never stops: each check moves the bar a little, and it does not
    sit at one number while checks keep coming.
    """

    def __init__(self, say: Any, language: str) -> None:
        self._say = say
        self._labels = _labels(language)
        self._started = time.monotonic()
        self._percent = 0
        self._criteria = 0
        self._checks = 0
        self._batches = 0
        self._batch: dict[str, int] = {}
        self._skipped: list[str] = []
        self._done = 0
        self._total = 0
        self._stage = ""

    def line(self, percent: int, doing: str) -> None:
        self._percent = max(self._percent, min(100, percent))
        filled = round(self._percent / 10)
        seconds = int(time.monotonic() - self._started)
        elapsed = self._labels["elapsed"].format(minutes=seconds // 60, seconds=seconds % 60)
        self._say(f"{'▓' * filled}{'░' * (10 - filled)} {self._percent}% · {doing} · {elapsed}")

    def stage(self, stage: str, total: int = 0, detail: str = "") -> None:
        del detail
        self._stage = stage
        if stage == "criteria":
            self.line(3, self._labels["criteria"])
        elif stage == "evidence":
            self._criteria = total
            self.line(15, self._labels["evidence"].format(count=total))
        elif stage == "judging":
            self._round_line()  # checks logged without a round end (older callers)
            if self._checks:
                self.line(68, self._labels["evidence_done"].format(count=self._checks))
            self._done, self._total = 0, total
            self.line(70, self._labels["judging"].format(total=total))

    def _round_line(self) -> None:
        """One line for the checks of one run_checks call (or the default walk), then a fresh count."""
        if not self._batch and not self._skipped:
            return
        self._batches += 1
        by_checks = 1 - math.exp(-self._checks / max(6, 2 * self._criteria))
        percent = max(15 + round(52 * min(0.98, by_checks)), min(self._percent + 1, 66))
        tools = "、".join(f"{tool}×{n}" if n > 1 else tool for tool, n in self._batch.items()) or "-"
        text = self._labels["batch"].format(
            batch=self._batches, count=sum(self._batch.values()), tools=tools, total=self._checks
        )
        if self._skipped:
            text += self._labels["batch_skipped"].format(
                count=len(self._skipped), reason="；".join(dict.fromkeys(self._skipped))
            )
        self.line(percent, text)
        self._batch, self._skipped = {}, []

    def tick(self, detail: str = "") -> None:
        del detail
        self._done += 1
        share = self._done / self._total if self._total else 1
        self.line(70 + round(28 * share), self._labels["judged"].format(done=self._done, total=self._total))

    async def counted(self, awaitable: Any, detail: str) -> Any:
        result = await awaitable
        self.tick(detail)
        return result

    @staticmethod
    def finish(total: Any) -> None:  # not called by the pipeline; the adapter ends the run itself
        del total

    @staticmethod
    def fail(error: Any) -> None:
        del error

    def log(self, message: str) -> None:
        from openjiuwen.agent_evolving.reward.evidence_agent import BATCH_DONE, WARNING

        if message.startswith(WARNING):  # what keeps part of the work unseen: shown as it is, the bar stays
            self.line(self._percent, self._labels["warning"].format(text=message.removeprefix(WARNING)))
        elif message.startswith("evidence ") and " for " in message:  # one check: summed up per round
            self._checks += 1
            tool = message.split("(", 1)[-1].split(")", 1)[0] if "(" in message else "-"
            served = message.split(" for ", 1)[1]
            reason = served.split(": ", 1)[1] if ": " in served else ""
            if reason.startswith("skipped: "):  # not run by rule, not a failure
                reason = reason.removeprefix("skipped: ")
                page = reason.startswith("the source of a page is not read")
                self._skipped.append(self._labels["page_source"] if page else reason)
                return
            self._batch[tool] = self._batch.get(tool, 0) + 1
            if reason:  # a check that could not run is said with its reason
                self.line(
                    self._percent, self._labels["check_failed"].format(count=self._checks, tool=tool, reason=reason)
                )
        elif message == BATCH_DONE:
            self._round_line()
        elif " criteria: " in message and "; tools: " in message:
            count = message.split(" criteria: ", 1)[0].strip()
            self.line(12, self._labels["criteria_done"].format(count=count))


def to_reward_payload(result: Mapping[str, Any], language: str = "zh") -> dict[str, Any]:
    """A Reward result as an ``attribution.reward.v1`` payload.

    Each dimension also carries ``tier`` (from the claim's status); ``run_diagnosis`` applies it to the
    signal instead of the score band.  Dimensions come in reading order: requirements not met, challenges
    not done, then what was met; the overall comment opens with those counts.

    Raises:
        ValueError: The result has no total score (no evidence) or no claim it could settle.
    """
    labels = _labels(language)
    total = (result.get("total") or {}).get("score")
    if total is None:
        raise ValueError("Reward found no evidence to score the work")
    dimensions = []
    unknown = 0
    counts = {"floor": 0, "floor_met": 0, "challenge": 0, "challenge_done": 0}
    # a looks category without a single picture is not "claims without evidence": it is said once, with the reason
    unscored = {
        c.get("category_id"): c
        for c in result.get("categories") or ()
        if c.get("unscored") in ("blind", "no_screenshots")
    }
    for item in result.get("sub_category_items") or ():
        status = item.get("status")
        if item.get("category_id") in unscored:
            continue
        challenge = item.get("type") == "proactive_challenge"
        tier = (_CHALLENGE_TIER if challenge else _REQUIREMENT_TIER).get(status)
        if tier is None:
            unknown += 1
            continue
        if challenge:
            counts["challenge"] += 1
            counts["challenge_done"] += status == "met"
        else:
            counts["floor"] += 1
            counts["floor_met"] += tier == "perfect"
        mark = labels["note_mark"] if item.get("from_user_note") else ""
        dimensions.append(
            {
                "category": str(item.get("category_name") or item.get("category_id") or "-"),
                "requirement": f"[{labels['challenge' if challenge else 'floor']}] {item.get('rubric', '')}{mark}",
                "comment": str(item.get("comment") or item.get("rubric") or "-"),
                "score": (_CHALLENGE_SCORE if challenge else _REQUIREMENT_SCORE)[status],
                "tier": tier,
                "related_signal_ids": [],
            }
        )
    if not dimensions:
        raise ValueError("Reward settled no claim")
    dimensions.sort(
        key=lambda d: (
            _TIER_ORDER[d["tier"]],
            d["requirement"].startswith(f"[{labels['challenge']}]"),
            not d["requirement"].endswith(labels["note_mark"]),  # the reviewer's note first within a tier
        )
    )
    comment = (
        f"{labels['counts'].format(**counts)}。"
        if labels is _LABELS["zh"]
        else f"{labels['counts'].format(**counts)}. "
    )
    comment += str((result.get("total") or {}).get("summary") or "-")
    meta = result.get("meta") or {}
    models = meta.get("models") or {}
    vision = meta.get("vision") or {}
    if unknown:
        comment = f"{comment} {labels['unknown'].format(count=unknown)}"
        if vision.get("judge") is False:  # say that seeing, not the work, is what is missing
            joint = "" if labels is _LABELS["zh"] else " "
            comment += joint + labels["blind_unknown"].format(model=models.get("judge") or "-")
    for category in unscored.values():
        name = category.get("category_name") or category.get("category_id") or "-"
        model = models.get("art_judge") or models.get("judge") or "-"
        comment = f"{comment} {labels['unscored_' + category['unscored']].format(name=name, model=model)}"
    categories = [
        {
            "name": str(c.get("category_name") or c.get("category_id") or "-"),
            "comment": str(c.get("comment") or ""),
            **({"score": c["score"]} if isinstance(c.get("score"), (int, float)) else {}),
        }
        for c in result.get("categories") or ()
    ]
    return {
        "schema_version": "attribution.reward.v1",
        "overall": {"comment": comment, "total_score": int(round(float(total)))},
        "categories": categories,
        "dimensions": dimensions,
    }


async def run_reward(
    *,
    task_parse: TaskParseResult,
    nodes_by_subagent: Mapping[str, Sequence[TrajectoryNode]],
    user_attribution: str,
    model: Any,
    language: str,
    status_sink: Any = None,
    model_name: str = "",
    status_counter: list[int] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Score the stage-one deliverables with Reward.

    Returns:
        The ``attribution.reward.v1`` payload and run meta for ``run_meta["reward"]``.

    Raises:
        Exception: Any failure (no route, no evidence, model errors); the caller falls back as for a
            failed Reward run.
    """
    from openjiuwen.agent_evolving.attribution.events import emit_single_llm_event
    from openjiuwen.agent_evolving.reward import pipeline, progress
    from openjiuwen.agent_evolving.reward.options import RewardOptions

    counter = status_counter if status_counter is not None else [0]
    task = review_task(task_parse, user_attribution, language)
    artifacts = existing_paths(task_parse.file_artifacts)
    reply = with_sent_files(final_reply(task_parse, nodes_by_subagent), task_parse, language)
    config = reward_config(model)
    started = time.time_ns()

    def say(line: str) -> None:
        emit_single_llm_event(
            status_sink,
            stage="reward",
            model=model_name,
            counter=counter,
            started_at_ns=started,
            ended_at_ns=time.time_ns(),
            preview=line,
        )

    lines = _ProgressLines(say, language)
    try:
        with progress.using(lines):  # Reward's own per-run progress slot, restored on exit
            options = RewardOptions(txt_response=reply, user_note=user_attribution or "", log=lines.log)
            result = await pipeline.reward(task, artifacts, config, options)
        payload = to_reward_payload(result, language)
    except Exception as exc:
        emit_single_llm_event(
            status_sink,
            stage="reward",
            model=model_name,
            counter=counter,
            started_at_ns=started,
            ended_at_ns=time.time_ns(),
            error=str(exc)[:200] or type(exc).__name__,
        )
        raise
    lines.line(100, _labels(language)["finished"].format(score=payload["overall"]["total_score"]))
    meta = {
        "engine": "reward",
        "artifacts": artifacts,
        "reply_chars": len(reply),
        "task_from_user": bool(task_parse.user_request),
        "requirements": len(task_parse.requirements or ()),
        "delivery_round": task_parse.delivery_round,
        "claims": len(result.get("sub_category_items") or ()),
        "warnings": list((result.get("meta") or {}).get("warnings") or ()),
    }
    return payload, meta


__all__ = [
    "REWARD_ENGINE_ENV",
    "reward_config",
    "reward_enabled",
    "existing_paths",
    "review_task",
    "final_reply",
    "route_from_model",
    "run_reward",
    "to_reward_payload",
]
