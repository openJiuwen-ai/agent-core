"""Reference provenance for ReConcile; this module never fetches protected questions."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .assets import RECONCILE_REVISION, git_blob_sha1

RECONCILE_BLOB_SHA1 = "32932a11f4d7033bb700cacf5826d85607ba868c"


RECONCILE_REFERENCE: dict[str, Any] = {
    "repository": "https://github.com/dinobby/ReConcile",
    "paper": "https://arxiv.org/abs/2309.13007",
    "paper_locations": ["Section 5", "Appendix A.1", "Appendix B.1"],
    "main100_selection": "first_100_in_original_dataset_SQA_dev_json_order",
    "selection_sources": [
        "https://github.com/dinobby/ReConcile/blob/main/data_utils.py",
        "https://github.com/dinobby/ReConcile/blob/main/run.py",
    ],
    "historical_agents": [
        {"name": "ChatGPT", "model": "gpt-3.5-turbo-0613", "backend": "Azure OpenAI", "temperature": 0.7},
        {"name": "Bard", "model": "chat-bison-001", "backend": "PaLM2 API", "temperature": 0.7},
        {"name": "Claude2", "model": "claude-2", "backend": "Claude web interface", "temperature": "service_default"},
    ],
    "immutable_service_snapshots_verified": False,
    "code_alias_caveat": "Public code uses a gpt-35-turbo deployment alias and palm.chat defaults; these do not pin the paper's service snapshots.",
    "main_result_runs": "at_least_three_on_the_same_100_questions",
    "demonstrations": "four convincing samples per agent; an agent sees the other agents' samples",
    "current_availability": "historical pool is not assumed runnable; an explicitly approved replacement is a protocol comparison",
    "availability_sources": [
        "https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/retired-models",
        "https://platform.claude.com/docs/en/about-claude/model-deprecations",
    ],
}


def verify_reconcile_main100(
    evidence: Mapping[str, Any],
    ordered_ids: Sequence[str],
    *,
    directory: Path,
    supplied_examples: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Check a user-supplied pinned original release against the public selection code.

    The declared source checksum is a provenance input, not a checksum discovered
    from the internet here. A filename or arbitrary 100-ID list is insufficient.
    """

    revision = str(evidence.get("repository_revision", ""))
    expected = str(evidence.get("source_sha256", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("main-100 provenance requires an immutable repository revision and original source SHA256")
    if revision != RECONCILE_REVISION:
        raise ValueError("main-100 preparation requires the exact predeclared ReConcile repository commit")
    if (
        evidence.get("repository") != RECONCILE_REFERENCE["repository"]
        or evidence.get("repository_path") != "dataset/SQA/dev.json"
    ):
        raise ValueError("main-100 provenance must identify the original ReConcile dataset/SQA/dev.json artifact")
    source = Path(evidence["local_source"])
    source = source if source.is_absolute() else directory / source
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("original evaluation source checksum differs from its declared provenance")
    if git_blob_sha1(raw) != RECONCILE_BLOB_SHA1:
        raise ValueError("original evaluation bytes do not match the verified pinned repository blob")
    records = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(records, list) or len(records) < 100:
        raise ValueError("original evaluation source must retain at least its first 100 ordered records")
    ids = tuple(str(item["qid"]) for item in records[:100])
    if len(set(ids)) != 100 or tuple(ordered_ids) != ids:
        raise ValueError("evaluation IDs do not equal the original release's first 100 question IDs in order")
    if supplied_examples is not None:
        by_id = {example.example_id: example for example in supplied_examples}
        for record, identifier in zip(records[:100], ids, strict=True):
            example = by_id.get(identifier)
            answer = record.get("answer")
            gold = ("yes" if answer else "no") if isinstance(answer, bool) else answer
            if example is None or example.query != record.get("question") or example.gold_answer != gold:
                raise ValueError("protected query content or label differs from the declared original source")
    return {
        "repository": evidence["repository"],
        "repository_revision": revision,
        "repository_path": evidence["repository_path"],
        "source_sha256": expected,
        "source_git_blob_sha1": RECONCILE_BLOB_SHA1,
        "local_source": str(source.resolve()),
        "ordered_ids": ids,
        "selection_rule": RECONCILE_REFERENCE["main100_selection"],
        "verification": "pinned_repository_blob_and_ordered_question_label_identity",
        "remote_source_fetched": False,
    }
