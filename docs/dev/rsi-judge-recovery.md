# RSI LLM Judge evidence and recovery

The Harness RSI `llm_as_judge` evaluator grades frozen task evidence against
the supplied criteria. The model supplies item scores and citations; trusted
code validates the response, applies configured weights and penalties, and
compares the continuous score with `judge_success_score` (default: 0.8).
The case score remains binary; the continuous score is retained in metadata.

## Evidence routes

- Complete supported UTF-8 text evidence up to 64 KiB, measured after JSON
  serialization, is supplied directly to the model without tools.
- Other inputs use a workspace-restricted, read-only DeepAgent. It can read,
  list and search files, but cannot run shell commands or modify artifacts.
  The default `judge_agent_max_iterations` is now 20 (previously 8).
  Explicit overrides are preserved.
- Long submitted responses remain lossless ordered pages. Files are not
  silently clipped to fit the direct route.
- UTF-8 `.jsonl` logs are supported as text evidence, including during recovery.

## One recovery attempt

An invalid verdict or exhausted reading budget permits one recovery attempt.
The reader no longer loses its read tools on the final iteration. Recovery
does not start another reader: it loads the complete frozen text inventory
up to 256 KiB and requests a verdict without tools. Direct-route recovery
reuses the complete original payload.

If the inventory cannot be included completely (size, file type or read
failure), recovery fails as evaluation infrastructure unavailability. It
does not fabricate a zero task score. Recovery errors distinguish unsupported formats, missing or
unreadable files, invalid UTF-8, snapshot path escapes and byte-limit failures.
File-specific errors identify the evidence filename; serialized size failures
identify the complete payload size. A valid zero verdict is accepted
without recovery. An explicit unavailable verdict is propagated without
requesting a reclassification. Recovery has no transient model retries;
normal calls retain `judge_max_retries` (default: 2). Each attempt has the
configured `judge_timeout_sec` deadline (default: 900 seconds).

Evidence, raw responses, validation errors, tool events, and the assessment
or error are retained in the case's judge directory. The evidence protocol
identity changes so previous grades cannot be reused under the new protocol.

## Limitations

Complete evidence delivery is not proof that the model understood every
criterion. The normal reader path does not mechanically verify that all
relevant files were read. Runtime or visual correctness cannot be inferred
from text alone. Larger and non-text recovery inputs remain unavailable;
the byte limit is not a guarantee of fitting a model's token context window.
