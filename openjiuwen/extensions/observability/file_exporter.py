# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""File-based SpanExporter that appends OTLP JSON lines, no collector.

All spans are appended to a per-day file ``traces-<YYYY-MM-DD>.jsonl``
directly under ``root_dir``. Each line is a standalone OTLP JSON
``ExportTraceServiceRequest`` (``resourceSpans`` → ``scopeSpans`` →
``spans`` with a single span) carrying ``traceId``/``spanId``/
``parentSpanId`` as hex strings — the format Collector/Langfuse ingest
directly via ``POST /v1/traces``. Spans from different traces are
interleaved in the same file; the collector rebuilds each trace from
the ``traceId``/``parentSpanId`` carried on every span, so physical
ordering or per-trace file separation is irrelevant for ingestion.
Replaying is just POSTing each line in turn.

Pair this exporter with ``BatchSpanProcessor`` (see ``runtime.py``) so
span-end does not block the business thread: the processor batches
ended spans and calls :meth:`export` asynchronously (default every 5s
or 512 spans). ``export()`` appends straight to disk — no in-memory
buffer, so there is nothing to flush; ``force_flush`` / ``shutdown``
are no-ops.

Trace files (``*.jsonl``) whose mtime predates ``retention_days`` are
lazily pruned at most every ``_CLEANUP_INTERVAL`` exports; cleanup never
raises.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Sequence

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from openjiuwen.core.common.logging import team_logger
from openjiuwen.extensions.observability.otlp_codec import encode_span_to_otlp_json

# Cleanup runs at most every N export cycles to keep latency low.
_CLEANUP_INTERVAL = 64
_SECONDS_PER_DAY = 86400


def _encode_span_line(span: ReadableSpan) -> str:
    """Encode a single ended span as one OTLP JSON line (hex ids, no indent)."""
    return encode_span_to_otlp_json(span).decode("utf-8")


class TraceFileExporter(SpanExporter):
    """Append OTLP JSON lines to ``<root_dir>/traces-<YYYY-MM-DD>.jsonl``.

    One append-only file per calendar day; spans from every trace share
    it. The collector splits traces by ``traceId`` on ingest, so no
    per-trace file separation is needed.
    """

    def __init__(self, root_dir: str = "./traces", retention_days: int = 7) -> None:
        self.root_dir = root_dir
        self.retention_days = max(0, int(retention_days))
        # Serialize appends: BatchSpanProcessor may call export() from its
        # worker thread while shutdown runs on the main thread.
        self._lock = threading.Lock()
        self._write_count = 0
        try:
            os.makedirs(self.root_dir, exist_ok=True)
        except Exception as exc:
            team_logger.warning("file_exporter: cannot create traces_dir={} - {}", self.root_dir, exc)

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        """Append every ended span (one OTLP JSON line each) to today's file.

        Writes straight to disk — no buffering, so nothing is held back
        for a later flush. Called asynchronously by BatchSpanProcessor.
        """
        lines: list[str] = []
        for span in spans or ():
            # on_end only fires after end, but drop any not-yet-ended defensively.
            if getattr(span, "end_time", None) is None:
                continue
            lines.append(_encode_span_line(span))

        if lines:
            file_path = os.path.join(self.root_dir, f"traces-{time.strftime('%Y-%m-%d')}.jsonl")
            try:
                with self._lock:
                    with open(file_path, "a", encoding="utf-8") as f:
                        for line in lines:
                            f.write(line)
                            f.write("\n")
            except OSError as exc:
                team_logger.warning("file_exporter: append failed to {} - {}", file_path, exc)

        self._maybe_cleanup()
        return SpanExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """No-op: export() writes straight to disk, nothing buffered to flush."""
        return True

    def shutdown(self) -> None:
        """No-op: nothing buffered; the last export() already hit disk."""
        return

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _maybe_cleanup(self) -> None:
        self._write_count += 1
        if self._write_count % _CLEANUP_INTERVAL != 0:
            return
        try:
            self._cleanup_old_files()
        except Exception as exc:
            team_logger.warning("file_exporter: cleanup failed - {}", exc)

    def _cleanup_old_files(self) -> None:
        """Delete trace files whose mtime predates the retention cutoff.

        ``FileNotFoundError`` is silently ignored in both the listdir and
        per-file steps (the dir may not exist yet; a file may have been
        removed by another process between listdir and stat/remove). Any
        other OSError is logged once and skipped so a single bad file
        can't abort the whole sweep.
        """
        if self.retention_days <= 0:
            return
        try:
            entries = os.listdir(self.root_dir)
        except FileNotFoundError:
            return
        except OSError as exc:
            team_logger.warning("file_exporter: cannot list {} - {}", self.root_dir, exc)
            return

        cutoff = time.time() - self.retention_days * _SECONDS_PER_DAY
        for entry in entries:
            if not entry.endswith(".jsonl"):
                continue
            file_path = os.path.join(self.root_dir, entry)
            if not os.path.isfile(file_path):
                continue
            try:
                if os.path.getmtime(file_path) < cutoff:
                    os.remove(file_path)
            except FileNotFoundError:
                # removed by another process between listdir and now
                continue
            except OSError as exc:
                team_logger.warning("file_exporter: cannot prune {} - {}", file_path, exc)
