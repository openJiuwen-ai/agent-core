"""Orchestrate one distill job (PersonalContext Home, account-level)."""

from __future__ import annotations

from dataclasses import dataclass

from openjiuwen.core.common.logging import logger
from openjiuwen.harness.personal_context.distill.analyzer import AnalyzerPort, LlmAnalyzer
from openjiuwen.harness.personal_context.distill.corpus import CorpusPort
from openjiuwen.harness.personal_context.distill.llm import LlmPort
from openjiuwen.harness.personal_context.distill.profile import (
    activate_profile_version,
    publish_distilled,
)
from openjiuwen.harness.personal_context.distill.store import begin_job, finish_job, get_cursor_ms


@dataclass(frozen=True, slots=True)
class DistillRunResult:
    job_id: str
    status: str
    window_start_ms: int
    window_end_ms: int
    message_count: int
    sampled: bool
    distilled_dir: str | None = None
    error: str | None = None


async def run_distill_job(
    home: str,
    *,
    window_end_ms: int,
    learning_since_ms: int | None = None,
    max_messages: int = 800,
    corpus: CorpusPort,
    analyzer: AnalyzerPort | None = None,
    llm: LlmPort | None = None,
    force_full_window: bool = False,
    subject_name: str | None = None,
) -> DistillRunResult:
    """
    Run one distill cycle under PersonalContext ``home`` (snapshot portrait).

    One job = one bounded-cost representative distillation of the window.
    On non-empty success: writes ``im/profiles/versions/<job_id>/``, atomically
    switches ``current.json``, then advances the Distill cursor to the last
    eligible message of the PRE-downsample window
    (``covered_through_ms = max(window_start_ms, window_max_sent_at_ms + 1)``)
    — even when the window was downsampled the whole window is accounted as
    covered, and ``eligible_count`` / ``message_count`` /
    ``prompt_message_count`` / ``sampled`` record the approximation honestly
    in job json and meta.json. Empty windows succeed and failures fail without
    advancing the cursor, so late-ingested messages stay eligible.
    """
    cursor_ms = 0 if force_full_window else get_cursor_ms(home)
    floor_ms = 0 if learning_since_ms is None else int(learning_since_ms)
    window_start_ms = max(cursor_ms, floor_ms)

    job_id = begin_job(
        home,
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
    )

    subject = str(subject_name or "本人").strip() or "本人"

    try:
        active_analyzer: AnalyzerPort
        if analyzer is not None:
            active_analyzer = analyzer
        else:
            if llm is None:
                raise ValueError("distill requires analyzer= or llm=")
            active_analyzer = LlmAnalyzer(llm, subject_name=subject)

        batch = corpus.list_messages(
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
            max_messages=max_messages,
        )
        messages = batch.messages
        if not messages:
            finish_job(
                home,
                job_id,
                status="success",
                message_count=0,
                sampled=False,
                covered_through_ms=None,
                eligible_count=batch.eligible_count,
                prompt_message_count=0,
            )
            return DistillRunResult(
                job_id=job_id,
                status="success",
                window_start_ms=window_start_ms,
                window_end_ms=window_end_ms,
                message_count=0,
                sampled=False,
                distilled_dir=None,
            )

        candidates = await active_analyzer.analyze(messages)
        prompt_message_count = candidates.prompt_message_count
        materials_chars = candidates.materials_chars
        if batch.sampled:
            logger.info(
                "distill window downsampled: eligible_count=%s message_count=%s",
                batch.eligible_count,
                len(messages),
            )
        if prompt_message_count is not None and prompt_message_count != len(messages):
            logger.error(
                "distill prompt truncation regression: "
                "prompt_message_count=%s message_count=%s",
                prompt_message_count,
                len(messages),
            )
        if materials_chars is not None:
            logger.info("distill materials_chars=%s", materials_chars)

        window_max_sent_at_ms = batch.window_max_sent_at_ms
        covered_through_ms = (
            max(window_start_ms, window_max_sent_at_ms + 1)
            if window_max_sent_at_ms is not None
            else window_start_ms
        )
        root = publish_distilled(
            home,
            job_id,
            persona_md=candidates.persona_md,
            work_md=candidates.work_md,
            meta={
                "window_start_ms": window_start_ms,
                "window_end_ms": window_end_ms,
                "message_count": len(messages),
                "sampled": batch.sampled,
                "eligible_count": batch.eligible_count,
                "prompt_message_count": prompt_message_count,
                "covered_through_ms": covered_through_ms,
                "channels": sorted({message.channel_id for message in messages}),
                "conversation_count": len({message.conversation_id for message in messages}),
                "job_id": job_id,
                "analyzer": type(active_analyzer).__name__,
            },
            merge_with_existing=True,
        )
        activate_profile_version(home, job_id, source="distill")
        finish_job(
            home,
            job_id,
            status="success",
            message_count=len(messages),
            sampled=batch.sampled,
            covered_through_ms=covered_through_ms,
            eligible_count=batch.eligible_count,
            prompt_message_count=prompt_message_count,
        )
        return DistillRunResult(
            job_id=job_id,
            status="success",
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
            message_count=len(messages),
            sampled=batch.sampled,
            distilled_dir=str(root),
        )
    except Exception as exc:  # noqa: BLE001 — job must record failure
        finish_job(
            home,
            job_id,
            status="failed",
            error=str(exc),
        )
        return DistillRunResult(
            job_id=job_id,
            status="failed",
            window_start_ms=window_start_ms,
            window_end_ms=window_end_ms,
            message_count=0,
            sampled=False,
            error=str(exc),
        )
