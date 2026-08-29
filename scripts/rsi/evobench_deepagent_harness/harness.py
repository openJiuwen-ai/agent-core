from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evobench.models.client import ModelConfig

from openjiuwen.core.foundation.llm import UserMessage
from openjiuwen.harness.rails.base import DeepAgentRail


@dataclass(frozen=True)
class PolicyRolloutResult:
    rollout_id: str
    task_id: str
    task_domain: str
    trajectory_path: Path
    metadata_path: Path
    final_answer: str
    exit_reason: str
    steps: int
    duration_seconds: float
    token_usage: dict[str, int]
    runtime_errors: list[str]


@dataclass(frozen=True)
class _DecisionGround:
    ground_id: str
    claim: str
    claim_kind: str
    dependencies: tuple[str, ...]


def _bounded_text(value: Any, limit: int) -> str:
    try:
        text = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 24)] + "...[controller-truncated]"


def _normalized_claim(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Return the first bounded JSON object without accepting prose as evidence."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


class SubmissionCheckpointRail(DeepAgentRail):
    """Fail-closed claim checkpoint for answer-shaped model responses.

    The draft is used only by the claim extractor. Independent auditors receive
    the task, bounded tool evidence, and controller-canonical claims; they never
    receive the draft's prose or reasoning.
    """

    priority = 90

    _ADVERSE_KIND = "adverse_or_prescriptive"
    _AFFIRMATIVE_KIND = "affirmative_or_descriptive"
    _GENERAL_AUDIT_RULES = (
        "Apply these controller rules to the complete inference, not only to its literal observation. "
        "An obligation governing an outcome or action does not by itself entail that every intermediate "
        "artifact or representation must duplicate the obligation or contain a dedicated field. The "
        "absence of a field, sentence, value, or operation proves only absence; it is not authority that "
        "the missing item is mandatory for the inspected target. If the obligation can be satisfied by "
        "a different owner, artifact, representation, incorporated dependency, or downstream operation, "
        "absence in the inspected target is not a defect unless task-visible authority explicitly binds "
        "that exact obligation to that exact target under an active trigger. A task-consistent state in "
        "which the cited evidence remains true but the defect, classification, or prescribed action is "
        "false is a surviving counterexample and defeats the complete ground."
    )

    def __init__(
        self,
        *,
        model: Any,
        task_prompt: str,
        enabled: bool,
        instruction: str = "",
        max_revisions: int = 1,
        max_collected_evidence_items: int = 96,
        max_collected_evidence_chars: int = 256000,
        max_tool_result_chars: int = 8000,
        max_audit_evidence_items: int = 48,
        max_audit_evidence_chars: int = 64000,
        max_task_chars: int = 10000,
        max_draft_chars: int = 12000,
        max_ground_count: int = 12,
        max_audit_prompt_chars: int = 96000,
        audit_timeout_seconds: float = 180.0,
        max_audit_retries: int = 1,
        max_rechecks: int = 1,
    ) -> None:
        super().__init__()
        self.model = model
        self.task_prompt = str(task_prompt)[: max(1000, min(int(max_task_chars), 20000))]
        self.enabled = enabled
        self.instruction = instruction.strip()[:4000]
        self.max_revisions = max(1, min(int(max_revisions), 3))
        self.max_collected_evidence_items = max(8, min(int(max_collected_evidence_items), 128))
        self.max_collected_evidence_chars = max(16000, min(int(max_collected_evidence_chars), 384000))
        self.max_tool_result_chars = max(256, min(int(max_tool_result_chars), 8000))
        self.max_audit_evidence_items = max(4, min(int(max_audit_evidence_items), 48))
        self.max_audit_evidence_chars = max(8000, min(int(max_audit_evidence_chars), 64000))
        self.max_draft_chars = max(1000, min(int(max_draft_chars), 24000))
        self.max_ground_count = max(1, min(int(max_ground_count), 24))
        self.max_audit_prompt_chars = max(16000, min(int(max_audit_prompt_chars), 128000))
        self.audit_timeout_seconds = max(10.0, min(float(audit_timeout_seconds), 300.0))
        self.max_audit_retries = max(0, min(int(max_audit_retries), 2))
        self.max_rechecks = max(0, min(int(max_rechecks), 1))

        self.activation_count = 0
        self.revision_count = 0
        self.recheck_count = 0
        self.repair_evidence_count = 0
        self.audit_calls = 0
        self.audit_parse_failures = 0
        self.evidence_collected_count = 0
        self.evidence_dropped_count = 0
        self.evidence_evicted_count = 0
        self.release_status = "not_activated"
        self._evidence_chars = 0
        self._audit_evidence_chars = 0
        self._evidence_fingerprints: set[str] = set()
        self._evidence: list[dict[str, str]] = []
        self._audit_evidence: list[dict[str, str]] = []
        self._grounds: list[_DecisionGround] = []
        self._decisions: dict[str, dict[str, Any]] = {}
        self._audit_rounds: list[dict[str, Any]] = []
        self._fresh_evidence_since_audit = False
        self._repair_window_open = False
        self._force_safe_release = False

    async def after_tool_call(self, ctx: Any) -> None:
        if not self.enabled:
            return
        inputs = ctx.inputs
        result = getattr(inputs, "tool_result", None)
        if result is None:
            tool_msg = getattr(inputs, "tool_msg", None)
            result = getattr(tool_msg, "content", None)
        if result in (None, ""):
            return

        tool_name = str(getattr(inputs, "tool_name", "tool") or "tool")[:160]
        locator = self._task_visible_locator(getattr(inputs, "tool_args", None))
        result_text = _bounded_text(result, self.max_tool_result_chars)
        fingerprint_source = json.dumps(
            {"tool_name": tool_name, "locator": locator, "result": result_text},
            ensure_ascii=False,
            sort_keys=True,
        )
        fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()
        if fingerprint in self._evidence_fingerprints:
            return
        item_size = len(fingerprint_source)
        if item_size > self.max_collected_evidence_chars:
            self.evidence_dropped_count += 1
            return
        while self._evidence and (
            len(self._evidence) >= self.max_collected_evidence_items
            or self._evidence_chars + item_size > self.max_collected_evidence_chars
        ):
            evicted = self._evidence.pop(0)
            self._evidence_chars -= int(evicted.pop("_serialized_chars", "0"))
            self.evidence_evicted_count += 1
        self._evidence_fingerprints.add(fingerprint)
        self.evidence_collected_count += 1
        self._evidence_chars += item_size
        self._evidence.append(
            {
                "evidence_id": _stable_id("ev", fingerprint_source),
                "tool_name": tool_name,
                "source_locator": locator,
                "tool_result": result_text,
                "_serialized_chars": str(item_size),
            }
        )
        if self.activation_count and self._repair_window_open:
            self.repair_evidence_count += 1
            self._fresh_evidence_since_audit = True
            self.release_status = "repair_evidence_collected"

    async def after_model_call(self, ctx: Any) -> None:
        if not self.enabled:
            return
        response = getattr(ctx.inputs, "response", None)
        content = str(getattr(response, "content", "") or "").strip()
        if not content or getattr(response, "tool_calls", None):
            return
        if self.activation_count == 0:
            await self._checkpoint_draft(ctx, content)
            return
        if self._fresh_evidence_since_audit and self.recheck_count < self.max_rechecks:
            await self._recheck_after_repair(ctx, content)
            return
        await self._enforce_recomputed_release(ctx, response, content)

    async def _checkpoint_draft(self, ctx: Any, draft: str) -> None:
        self.activation_count = 1
        await self._audit_draft(ctx, draft, round_name="initial")

    async def _recheck_after_repair(self, ctx: Any, draft: str) -> None:
        self.recheck_count += 1
        self._fresh_evidence_since_audit = False
        self._repair_window_open = False
        await self._audit_draft(ctx, draft, round_name=f"recheck_{self.recheck_count}")

    async def _audit_draft(self, ctx: Any, draft: str, *, round_name: str) -> None:
        self.release_status = "auditing"
        if len(draft) > self.max_draft_chars:
            self._record_blocked_draft(draft, "draft_length_limit")
            self._finish_audit_round(round_name)
            ctx.push_steering(self._build_steering())
            return

        grounds = await self._extract_grounds(draft)
        if not grounds:
            self._record_blocked_draft(draft, "claim_extraction_failed")
            self._finish_audit_round(round_name)
            ctx.push_steering(self._build_steering())
            return
        self._force_safe_release = False
        self._grounds = grounds
        self._select_audit_evidence(grounds)

        counter_a = await self._run_counterexample_audit(grounds, pass_name="A")
        counter_b = await self._run_counterexample_audit(grounds, pass_name="B")
        binding = await self._run_binding_audit(grounds)
        self._aggregate(grounds, counter_a, counter_b, binding)
        self._finish_audit_round(round_name)
        self.release_status = "steering_recompute"
        ctx.push_steering(self._build_steering())

    def _finish_audit_round(self, round_name: str) -> None:
        self._fresh_evidence_since_audit = False
        self._repair_window_open = self.recheck_count < self.max_rechecks and any(
            item.get("decision") == "REMOVE" for item in self._decisions.values()
        )
        self._audit_rounds.append(
            {
                "round": round_name,
                "selected_evidence_ids": [item["evidence_id"] for item in self._audit_evidence],
                "ground_ids": [ground.ground_id for ground in self._grounds],
                "decisions": {
                    ground_id: str(item.get("decision") or "REMOVE") for ground_id, item in self._decisions.items()
                },
            }
        )

    async def _extract_grounds(self, draft: str) -> list[_DecisionGround]:
        prompt = (
            "You are a claim extractor, not a reviewer. Read the unpublished draft and return JSON only. "
            "Extract the smallest deduplicated set of every complete atomic ground whose presence or "
            "absence could change a recomputed conclusion or action. Include positive and mitigating "
            "grounds already present in the draft even when they do not support the draft's current final "
            "conclusion; they may become decisive after an adverse ground is removed. Merge a missing or "
            "failed condition with the conclusion or "
            "required action it is used to justify. Exclude headings, locators, citations, audit narration, "
            "and intermediate reasoning. Any adverse conclusion (including a failure, defect, rejection, "
            "unsatisfied requirement, or required correction) and every prescription must be typed "
            "adverse_or_prescriptive. An affirmative or non-adverse conclusion (including satisfied, passed, "
            "accepted, or no corrective action needed) and neutral facts must be typed "
            "affirmative_or_descriptive. Classify the complete consequence, not merely whether its wording "
            "contains a negation. Dependencies are "
            "1-based indices into your grounds array. Do not invent claims.\n"
            f"Return at most {self.max_ground_count} rows using exactly this schema:\n"
            '{"grounds":[{"claim":"...","claim_kind":"adverse_or_prescriptive|affirmative_or_descriptive",'
            '"depends_on_ground_indices":[1]}]}\n'
            f"UNPUBLISHED DRAFT:\n{draft}"
        )
        payload = await self._invoke_json(prompt)
        if not isinstance(payload, dict) or set(payload) != {"grounds"}:
            self.audit_parse_failures += 1
            return []
        rows = payload.get("grounds")
        if not isinstance(rows, list) or not rows or len(rows) > self.max_ground_count:
            self.audit_parse_failures += 1
            return []

        parsed: list[tuple[str, str, list[int]]] = []
        index_to_normalized: dict[int, str] = {}
        normalized_to_row: dict[str, tuple[str, str, list[int]]] = {}
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict) or set(row) != {
                "claim",
                "claim_kind",
                "depends_on_ground_indices",
            }:
                self.audit_parse_failures += 1
                return []
            claim = " ".join(str(row.get("claim") or "").split())[:1000]
            normalized = _normalized_claim(claim)
            if not normalized:
                self.audit_parse_failures += 1
                return []
            kind = str(row.get("claim_kind") or "").casefold()
            if kind not in {self._ADVERSE_KIND, self._AFFIRMATIVE_KIND}:
                kind = self._ADVERSE_KIND
            dependencies = row.get("depends_on_ground_indices")
            if not isinstance(dependencies, list) or any(
                not isinstance(item, int) or isinstance(item, bool) for item in dependencies
            ):
                self.audit_parse_failures += 1
                return []
            index_to_normalized[index] = normalized
            existing = normalized_to_row.get(normalized)
            if existing is None:
                normalized_to_row[normalized] = (claim, kind, dependencies)
                parsed.append((claim, kind, dependencies))
            elif kind == self._ADVERSE_KIND and existing[1] != self._ADVERSE_KIND:
                normalized_to_row[normalized] = (existing[0], kind, existing[2])

        id_by_normalized = {normalized: _stable_id("g", normalized) for normalized in normalized_to_row}
        grounds: list[_DecisionGround] = []
        seen_ids: set[str] = set()
        for claim, _, dependency_indices in parsed:
            normalized = _normalized_claim(claim)
            canonical_claim, canonical_kind, _ = normalized_to_row[normalized]
            ground_id = id_by_normalized[normalized]
            if ground_id in seen_ids:
                continue
            dependencies = tuple(
                dict.fromkeys(
                    id_by_normalized[index_to_normalized[item]]
                    for item in dependency_indices
                    if item in index_to_normalized and index_to_normalized[item] != normalized
                )
            )
            grounds.append(_DecisionGround(ground_id, canonical_claim, canonical_kind, dependencies))
            seen_ids.add(ground_id)
        return grounds

    def _canonical_audit_input(self, grounds: list[_DecisionGround]) -> str:
        evidence = json.dumps(self._audit_evidence, ensure_ascii=False, separators=(",", ":"))
        claims = json.dumps(
            [
                {
                    "ground_id": ground.ground_id,
                    "claim": ground.claim,
                    "claim_kind": ground.claim_kind,
                }
                for ground in grounds
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return (
            f"PUBLIC TASK:\n{self.task_prompt}\n"
            f"TASK-VISIBLE SOURCE TOOL EVIDENCE:\n{evidence}\n"
            f"CONTROLLER-CANONICAL CLAIMS:\n{claims}\n"
        )

    def _select_audit_evidence(self, grounds: list[_DecisionGround]) -> None:
        """Build a bounded relevant packet without privileging early tool calls."""
        claim_terms = self._terms(" ".join(ground.claim for ground in grounds))
        task_terms = self._terms(self.task_prompt)
        ranked: list[tuple[float, int, dict[str, str], int]] = []
        total = max(1, len(self._evidence) - 1)
        for index, item in enumerate(self._evidence):
            visible_item = {key: value for key, value in item.items() if not key.startswith("_")}
            serialized = json.dumps(visible_item, ensure_ascii=False, separators=(",", ":"))
            item_terms = self._terms(serialized)
            locator_terms = self._terms(item.get("source_locator", ""))
            score = (
                8.0 * len(claim_terms & item_terms)
                + 2.0 * len(task_terms & item_terms)
                + 3.0 * len((claim_terms | task_terms) & locator_terms)
                + index / total
            )
            ranked.append((score, index, visible_item, len(serialized)))
        ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)

        selected: list[tuple[int, dict[str, str], int]] = []
        selected_chars = 0
        for _, index, item, item_chars in ranked:
            if len(selected) >= self.max_audit_evidence_items:
                break
            if (
                item_chars > self.max_audit_evidence_chars
                or selected_chars + item_chars > self.max_audit_evidence_chars
            ):
                continue
            selected.append((index, item, item_chars))
            selected_chars += item_chars
        selected.sort(key=lambda row: row[0])
        self._audit_evidence = [item for _, item, _ in selected]
        self._audit_evidence_chars = selected_chars

    @staticmethod
    def _terms(text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"\w+", text.casefold(), flags=re.UNICODE)
            if len(token) >= 3 and not token.isdigit()
        }

    def _counterexample_prompt(self, grounds: list[_DecisionGround], *, pass_name: str) -> str:
        return self._canonical_audit_input(grounds) + (
            f"You are independent counterexample auditor {pass_name}. Do not infer from or reconstruct the "
            "draft author's reasoning. For each canonical ground, use only the public task and source tool "
            f"evidence above. {self._GENERAL_AUDIT_RULES} "
            "exact_target_binding is true only when task-visible authority binds the complete claim, including "
            "its defect, classification, or prescribed-action consequence, to that exact inspected target. "
            "counterexample_holds is true when a task-consistent state supported or left possible by the "
            "evidence makes the ground false or makes its prescribed action unnecessary. Return JSON only, "
            "one row for every ground, no explanations and no extra keys:\n"
            '{"audits":[{"ground_id":"g_...","exact_target_binding":true,'
            '"counterexample_holds":false}]}'
        )

    def _binding_prompt(self, grounds: list[_DecisionGround]) -> str:
        return self._canonical_audit_input(grounds) + (
            "You are an independent evidence-binding auditor. Do not infer from or reconstruct the draft "
            f"author's reasoning. {self._GENERAL_AUDIT_RULES} "
            "For each canonical ground, exact_target_binding is true only when the cited task-visible authority "
            "binds the complete inference from observation to defect, classification, or prescribed action to "
            "that exact inspected target. verified is true only when the public task and source evidence entail "
            "the complete claim with the required scope and active trigger. Similar purpose, good practice, a "
            "literal omission, or a requirement on another object is not verification. Return JSON only, one "
            "row for every ground, no explanations and "
            "no extra keys:\n"
            '{"audits":[{"ground_id":"g_...","exact_target_binding":true,"verified":true}]}'
        )

    async def _run_counterexample_audit(
        self, grounds: list[_DecisionGround], *, pass_name: str
    ) -> dict[str, tuple[bool, bool]]:
        prompt = self._counterexample_prompt(grounds, pass_name=pass_name)
        for attempt in range(self.max_audit_retries + 1):
            retry_suffix = (
                "\nYour previous response was missing or invalid. Return the complete JSON object only, "
                "with exactly one schema-valid row for every supplied ground_id."
                if attempt
                else ""
            )
            parsed = self._parse_counterexample_audit(await self._invoke_json(prompt + retry_suffix), grounds)
            if parsed:
                return parsed
        return {}

    async def _run_binding_audit(self, grounds: list[_DecisionGround]) -> dict[str, tuple[bool, bool]]:
        prompt = self._binding_prompt(grounds)
        for attempt in range(self.max_audit_retries + 1):
            retry_suffix = (
                "\nYour previous response was missing or invalid. Return the complete JSON object only, "
                "with exactly one schema-valid row for every supplied ground_id."
                if attempt
                else ""
            )
            parsed = self._parse_binding_audit(await self._invoke_json(prompt + retry_suffix), grounds)
            if parsed:
                return parsed
        return {}

    async def _invoke_json(self, prompt: str) -> dict[str, Any] | None:
        self.audit_calls += 1
        if self.audit_calls > self._max_controller_model_calls() or len(prompt) > self.max_audit_prompt_chars:
            self.audit_parse_failures += 1
            return None
        try:
            response = await asyncio.wait_for(
                self.model.invoke(messages=[UserMessage(content=prompt)]),
                timeout=self._audit_call_timeout(prompt),
            )
        except Exception:
            self.audit_parse_failures += 1
            return None
        content = str(getattr(response, "content", "") or "")
        payload = _extract_json_object(content[: self.max_audit_prompt_chars])
        if payload is None:
            self.audit_parse_failures += 1
        return payload

    def _max_controller_model_calls(self) -> int:
        calls_per_audit_round = 4 + (3 * self.max_audit_retries)
        return ((1 + self.max_rechecks) * calls_per_audit_round) + self.max_revisions

    def _audit_call_timeout(self, prompt: str) -> float:
        # Large evidence packets take longer to prefill and generate a complete
        # all-ground JSON response. Scale within the existing hard 300s cap
        # instead of treating predictable long-prefill latency as a parse failure.
        return min(300.0, self.audit_timeout_seconds + min(120.0, len(prompt) / 1000.0))

    def _parse_counterexample_audit(
        self,
        payload: dict[str, Any] | None,
        grounds: list[_DecisionGround],
    ) -> dict[str, tuple[bool, bool]]:
        return self._parse_audit_rows(
            payload,
            grounds,
            value_key="counterexample_holds",
        )

    def _parse_binding_audit(
        self,
        payload: dict[str, Any] | None,
        grounds: list[_DecisionGround],
    ) -> dict[str, tuple[bool, bool]]:
        return self._parse_audit_rows(payload, grounds, value_key="verified")

    def _parse_audit_rows(
        self,
        payload: dict[str, Any] | None,
        grounds: list[_DecisionGround],
        *,
        value_key: str,
    ) -> dict[str, tuple[bool, bool]]:
        expected_ids = {ground.ground_id for ground in grounds}
        if not isinstance(payload, dict) or set(payload) != {"audits"}:
            self.audit_parse_failures += 1
            return {}
        rows = payload.get("audits")
        if not isinstance(rows, list) or len(rows) != len(expected_ids):
            self.audit_parse_failures += 1
            return {}
        parsed: dict[str, tuple[bool, bool]] = {}
        allowed = {"ground_id", "exact_target_binding", value_key}
        for row in rows:
            if not isinstance(row, dict) or set(row) != allowed:
                self.audit_parse_failures += 1
                return {}
            ground_id = row.get("ground_id")
            exact = row.get("exact_target_binding")
            value = row.get(value_key)
            valid_ground_id = ground_id in expected_ids and ground_id not in parsed
            valid_values = isinstance(exact, bool) and isinstance(value, bool)
            if not valid_ground_id or not valid_values:
                self.audit_parse_failures += 1
                return {}
            parsed[str(ground_id)] = (exact, value)
        if set(parsed) != expected_ids:
            self.audit_parse_failures += 1
            return {}
        return parsed

    def _aggregate(
        self,
        grounds: list[_DecisionGround],
        counter_a: dict[str, tuple[bool, bool]],
        counter_b: dict[str, tuple[bool, bool]],
        binding: dict[str, tuple[bool, bool]],
    ) -> None:
        decisions: dict[str, dict[str, Any]] = {}
        for ground in grounds:
            audit_a = counter_a.get(ground.ground_id, (False, True))
            audit_b = counter_b.get(ground.ground_id, (False, True))
            binding_audit = binding.get(ground.ground_id, (False, False))
            if ground.claim_kind == self._ADVERSE_KIND:
                keep = binding_audit == (True, True) and audit_a == (True, False) and audit_b == (True, False)
            else:
                # Descriptive grounds already occur in the unpublished draft and do
                # not authorize a new defect, classification, or corrective action.
                # Keep them when any independent audit provides exact positive
                # support and no falsifier supplies an exact-bound counterexample.
                # An unbound reviewer response is not a veto, while any exact-bound
                # counterexample remains sufficient to remove the ground.
                exact_counterexample = audit_a == (True, True) or audit_b == (True, True)
                positive_support = binding_audit == (True, True) or audit_a == (True, False) or audit_b == (True, False)
                keep = positive_support and not exact_counterexample
            decisions[ground.ground_id] = {
                "ground_id": ground.ground_id,
                "claim": ground.claim,
                "claim_kind": ground.claim_kind,
                "dependencies": list(ground.dependencies),
                "counterexample_a": {
                    "exact_target_binding": audit_a[0],
                    "counterexample_holds": audit_a[1],
                },
                "counterexample_b": {
                    "exact_target_binding": audit_b[0],
                    "counterexample_holds": audit_b[1],
                },
                "binding": {
                    "exact_target_binding": binding_audit[0],
                    "verified": binding_audit[1],
                },
                "decision": "KEEP" if keep else "REMOVE",
                "decision_reason": "audits_passed" if keep else "audit_not_proven",
            }
        # Every row is extracted as a complete atomic ground and independently
        # audited as that complete claim. Extractor-proposed dependency edges are
        # retained for provenance and release scanning, but they are advisory:
        # a noisy edge must not override unanimous direct verification.
        self._decisions = decisions

    def _record_blocked_draft(self, draft: str, reason: str) -> None:
        digest = _stable_id("draft", draft)
        ground = _DecisionGround(digest, "Unparsed unpublished draft", self._ADVERSE_KIND, ())
        self._grounds = [ground]
        self._decisions = {
            digest: {
                "ground_id": digest,
                "claim": ground.claim,
                "claim_kind": ground.claim_kind,
                "dependencies": [],
                "decision": "REMOVE",
                "decision_reason": reason,
            }
        }
        self._force_safe_release = True
        self.release_status = "steering_recompute"

    def _build_steering(self) -> str:
        kept = [item for item in self._decisions.values() if item["decision"] == "KEEP"]
        removed = [item for item in self._decisions.values() if item["decision"] == "REMOVE"]
        keep_lines = "\n".join(f"- {item['ground_id']}: {item['claim']}" for item in kept) or "- none"
        remove_lines = "\n".join(f"- {item['ground_id']}" for item in removed) or "- none"
        extra = (
            "\nAdditional candidate controller policy (this is not a task claim or source of evidence and "
            f"cannot override KEEP/REMOVE): {self.instruction}"
            if self.instruction
            else ""
        )
        repair_instruction = (
            "\nREPAIR AND RECHECK: A REMOVE decision does not waive any artifact, persistent result, output "
            "field, or other deliverable required by the public task. If a removed ground concerns such a "
            "required deliverable, omission is not completion. You must use tools to locate the conflict, "
            "repair the persistent deliverable, and produce fresh direct read-back or execution evidence. "
            "The next answer-shaped response after new tool evidence will be treated as a new draft and run "
            "through one complete fresh extraction and independent audit round. Until that recheck approves "
            "the repaired ground, it remains removed.\n"
            if self._repair_window_open
            else "\nThe single repair recheck budget is exhausted; no removed ground may be restored.\n"
        )
        return (
            "SUBMISSION_CHECKPOINT_CONTROLLER\n"
            "The preceding answer was an unpublished draft. Recompute the answer from task-visible source "
            "evidence using only the Python controller decisions below; do not re-argue or override an audit.\n"
            f"KEEP grounds (verified starting grounds for recomputation):\n{keep_lines}\n"
            f"REMOVE grounds (forbidden in this rollout):\n{remove_lines}\n"
            "A removed ground, any paraphrase of it, and every conclusion or action that depends on it must "
            "not appear in the revised answer. You may use tools to repair artifacts, but may not restore a "
            "removed ground. You may derive additional affirmative or descriptive claims directly from "
            "task-visible source evidence when needed to complete the public task, but may not introduce a new "
            "adverse, defect, or prescriptive claim outside the KEEP set. Recompute all conclusions and actions "
            "after removal; do not leave a requested classification unresolved merely because an alleged defect "
            "was removed. Complete every output field and subquestion requested by the public task. When no "
            "verified adverse or prescriptive ground "
            "supports a corrective action, explicitly state that no such action is required instead of leaving "
            "the action status implicit. Start the revised answer "
            "with an 'Evidence closure:' section containing ID-only lines 'KEEP: <ids>' and 'REMOVE: <ids>'. "
            "Do not repeat removed claim text in that section." + repair_instruction + extra
        )

    async def _enforce_recomputed_release(self, ctx: Any, response: Any, content: str) -> None:
        self.revision_count += 1
        if self._force_safe_release:
            response.content = self._safe_release("claim extraction did not complete")
            self.release_status = "blocked_fail_closed"
            return
        violation = self._release_violation(content)
        if not violation:
            violation = await self._semantic_release_violation(content)
        if not violation:
            self.release_status = "released"
            return
        if self.revision_count < self.max_revisions:
            self.release_status = "revision_rejected"
            ctx.push_steering(self._build_steering() + f"\nRelease check failed: {violation}. Recompute again.")
            return
        response.content = self._safe_release(violation)
        self.release_status = "blocked_fail_closed"

    async def _semantic_release_violation(self, content: str) -> str:
        removed = [
            {
                "ground_id": ground_id,
                "claim": item["claim"],
                "dependencies": item["dependencies"],
            }
            for ground_id, item in self._decisions.items()
            if item["decision"] == "REMOVE"
        ]
        kept_ids = [ground_id for ground_id, item in self._decisions.items() if item["decision"] == "KEEP"]
        prompt = (
            "You are a release-presence scanner, not a correctness reviewer. Compare the revised response "
            "against frozen Python controller decisions. Detect semantic paraphrases, dependent conclusions "
            "or actions of removed grounds, and any new adverse conclusion or prescribed action that is not "
            "one of the KEEP grounds. A recomputed statement that no corrective action is required is not a "
            "new prescription when it depends only on verified KEEP grounds after all alleged-defect grounds "
            "were removed. Ignore the ID-only Evidence closure declarations themselves. Return "
            "JSON only and no explanations.\n"
            f"KEEP GROUND IDS:\n{json.dumps(kept_ids)}\n"
            f"REMOVED GROUNDS:\n{json.dumps(removed, ensure_ascii=False)}\n"
            f"REVISED RESPONSE:\n{content[: self.max_draft_chars]}\n"
            "Schema:\n"
            '{"removed_ground_ids_present":["g_..."],"dependent_ground_ids_present":["g_..."],'
            '"unlisted_adverse_or_prescriptive_present":false}'
        )
        payload = await self._invoke_json(prompt)
        allowed = {
            "removed_ground_ids_present",
            "dependent_ground_ids_present",
            "unlisted_adverse_or_prescriptive_present",
        }
        if not isinstance(payload, dict) or set(payload) != allowed:
            self.audit_parse_failures += 1
            return "release scan did not return its required schema"
        removed_ids = {item["ground_id"] for item in removed}
        present = payload.get("removed_ground_ids_present")
        dependent = payload.get("dependent_ground_ids_present")
        unlisted = payload.get("unlisted_adverse_or_prescriptive_present")
        if not isinstance(present, list) or not isinstance(dependent, list):
            self.audit_parse_failures += 1
            return "release scan contained invalid or unbound IDs"
        if not isinstance(unlisted, bool):
            self.audit_parse_failures += 1
            return "release scan contained invalid or unbound IDs"
        invalid_ids = []
        for item in present + dependent:
            if not isinstance(item, str) or item not in removed_ids:
                invalid_ids.append(item)
        if invalid_ids:
            self.audit_parse_failures += 1
            return "release scan contained invalid or unbound IDs"
        if present:
            return f"removed grounds reappeared: {', '.join(sorted(set(present)))}"
        if dependent:
            return f"actions dependent on removed grounds reappeared: {', '.join(sorted(set(dependent)))}"
        if unlisted:
            return "an unlisted adverse or prescriptive claim appeared after the audit"
        return ""

    def _release_violation(self, content: str) -> str:
        if "evidence closure:" not in content.casefold():
            return "missing Evidence closure marker"
        compact = " ".join(content.split())
        for ground_id, decision in self._decisions.items():
            expected = decision["decision"]
            if not re.search(rf"\b{expected}\s*:\s*[^\n]*\b{re.escape(ground_id)}\b", content, flags=re.IGNORECASE):
                return f"missing {expected} declaration for {ground_id}"
            if expected == "REMOVE" and re.search(
                rf"\bKEEP\s*:\s*[^\n]*\b{re.escape(ground_id)}\b",
                content,
                flags=re.IGNORECASE,
            ):
                return f"removed ground {ground_id} was marked KEEP"
            if expected == "REMOVE":
                normalized = _normalized_claim(str(decision.get("claim") or ""))
                if normalized and normalized != _normalized_claim("Unparsed unpublished draft"):
                    normalized_content = _normalized_claim(compact)
                    if normalized in normalized_content:
                        return f"removed ground {ground_id} reappeared"
        return ""

    def _safe_release(self, reason: str) -> str:
        keep_ids = [ground_id for ground_id, item in self._decisions.items() if item["decision"] == "KEEP"]
        remove_ids = [ground_id for ground_id, item in self._decisions.items() if item["decision"] == "REMOVE"]
        return (
            "Evidence closure:\n"
            f"KEEP: {', '.join(keep_ids) if keep_ids else 'none'}\n"
            f"REMOVE: {', '.join(remove_ids) if remove_ids else 'none'}\n"
            "The checkpoint withheld the unpublished conclusion and dependent actions because safe "
            f"recomputation did not complete ({reason})."
        )

    @staticmethod
    def _task_visible_locator(args: Any) -> str:
        if not isinstance(args, dict):
            return ""
        locator_keys = ("path", "file_path", "filepath", "url", "uri")
        locator = {key: args[key] for key in locator_keys if key in args and isinstance(args[key], str)}
        return _bounded_text(locator, 800) if locator else ""

    def metadata(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "activation_count": self.activation_count,
            "revision_count": self.revision_count,
            "repair_evidence_count": self.repair_evidence_count,
            "recheck_count": self.recheck_count,
            "max_rechecks": self.max_rechecks,
            "repair_window_open": self._repair_window_open,
            "release_status": self.release_status,
            "evidence_count": len(self._evidence),
            "evidence_collected_count": self.evidence_collected_count,
            "evidence_selected_count": len(self._audit_evidence),
            "evidence_dropped_count": self.evidence_dropped_count,
            "evidence_evicted_count": self.evidence_evicted_count,
            "evidence_chars": self._evidence_chars,
            "audit_evidence_chars": self._audit_evidence_chars,
            "audit_calls": self.audit_calls,
            "max_controller_model_calls": self._max_controller_model_calls(),
            "audit_parse_failures": self.audit_parse_failures,
            "ground_count": len(self._grounds),
            "decisions": list(self._decisions.values()),
            "audit_rounds": list(self._audit_rounds),
        }


def _task_prompt(task: dict[str, Any], workspace: Path) -> str:
    parts = [
        f"Task id: {task['id']}",
        f"Task workspace: {workspace}",
        "",
        str(task["prompt"]),
    ]
    public_files = task.get("public_files")
    if isinstance(public_files, list) and public_files:
        parts.extend(["", "Public files:", *[f"- {item}" for item in public_files]])
    return "\n".join(parts)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _chunk_dict(chunk: Any) -> dict[str, Any]:
    data = _jsonable(chunk)
    return data if isinstance(data, dict) else {"type": type(chunk).__name__, "payload": data}


def _controller_task_failed_error(event: dict[str, Any]) -> str:
    """Extract a task-loop failure that streaming APIs may return as a normal chunk."""
    if str(event.get("type") or "") != "controller_output":
        return ""
    payload = event.get("payload")
    if not isinstance(payload, dict) or str(payload.get("type") or "").casefold() != "task_failed":
        return ""
    parts: list[str] = []
    data = payload.get("data")
    for item in data if isinstance(data, list) else [data]:
        if isinstance(item, dict):
            value = item.get("text") or item.get("content") or item.get("message")
        else:
            value = item
        if value:
            parts.append(str(value))
    return "\n".join(parts).strip() or "DeepAgent controller reported task_failed"


def _is_retryable_model_transport_error(error: BaseException | str) -> bool:
    """Identify transient model transport failures without retrying semantic errors."""
    text = str(error).casefold()
    transport_markers = (
        "remoteprotocolerror",
        "incomplete chunked read",
        "peer closed connection",
        "apiconnectionerror",
        "connection reset",
        "connection aborted",
        "server disconnected",
        "readtimeout",
        "read timed out",
        "error code: 502",
        "error code: 503",
        "error code: 504",
    )
    return any(marker in text for marker in transport_markers)


def _trajectory_messages(prompt: str, events: list[dict[str, Any]], final_answer: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    pending_calls: list[dict[str, Any]] = []
    call_index = 0

    for event in events:
        event_type = str(event.get("type") or "")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if event_type == "tool_call":
            call_index += 1
            call_id = f"deepagent-call-{call_index}"
            tool_name = str(payload.get("tool_name") or "tool")
            tool_args = payload.get("tool_args")
            pending_calls.append({"id": call_id, "name": tool_name})
            messages.append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(tool_args, ensure_ascii=False)
                                if not isinstance(tool_args, str)
                                else tool_args,
                            },
                        }
                    ],
                }
            )
        elif event_type == "tool_result":
            call = pending_calls.pop(0) if pending_calls else {"id": "unknown", "name": "tool"}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": call["name"],
                    "content": str(payload.get("tool_result") or ""),
                }
            )

    if final_answer:
        messages.append({"role": "assistant", "content": final_answer})
    return messages


def _skill_dirs(harness_dir: Path) -> list[str]:
    root = harness_dir / "skills"
    if not root.is_dir():
        return []
    return [
        str(path.resolve())
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if (path / "SKILL.md").is_file()
    ]


def _materialize_runtime_skills(
    skill_dirs: list[str], workspace: Path, rollout_id: str
) -> tuple[Path | None, list[str]]:
    """Copy candidate skills under the task workspace so sandboxed SkillTool can read them."""
    if not skill_dirs:
        return None, []

    runtime_root = workspace / f".rsi_skills_{rollout_id}"
    runtime_root.mkdir(parents=True, exist_ok=False)
    materialized: list[str] = []
    for source_text in skill_dirs:
        source = Path(source_text)
        target = runtime_root / source.name
        shutil.copytree(source, target)
        materialized.append(str(target.resolve()))
    return runtime_root, materialized


class PolicyHarness:
    """Evo-Bench protocol adapter backed by the latest openJiuwen DeepAgent."""

    def __init__(self, harness_dir: str | Path, model_config: ModelConfig) -> None:
        self.harness_dir = Path(harness_dir).resolve()
        self.config = json.loads((self.harness_dir / "harness.json").read_text(encoding="utf-8"))
        self.system_prompt = (self.harness_dir / self.config["system_prompt"]).read_text(encoding="utf-8")
        self.model_config = model_config

    def run_task(
        self,
        *,
        task: dict[str, Any],
        task_workspace: str | Path,
        output_dir: str | Path,
        harness_revision: str,
        model_config_id: str,
        command_timeout_seconds: int = 120,
    ) -> PolicyRolloutResult:
        return asyncio.run(
            self._run_task(
                task=task,
                task_workspace=Path(task_workspace).resolve(),
                output_dir=Path(output_dir),
                harness_revision=harness_revision,
                model_config_id=model_config_id,
                command_timeout_seconds=command_timeout_seconds,
            )
        )

    async def _run_task(
        self,
        *,
        task: dict[str, Any],
        task_workspace: Path,
        output_dir: Path,
        harness_revision: str,
        model_config_id: str,
        command_timeout_seconds: int,
    ) -> PolicyRolloutResult:
        from openjiuwen.core.foundation.llm import init_model
        from openjiuwen.core.runner import Runner
        from openjiuwen.harness import create_deep_agent
        from openjiuwen.harness.cli.rails import TokenTrackingRail, ToolTrackingRail
        from openjiuwen.harness.rails import ModelAnomalyDetectionRail, SkillUseRail
        from openjiuwen.harness.rails.context_engineer import ContextAssembleRail, ContextProcessorRail
        from openjiuwen.harness.rails.model_anomaly_detection_rail import ToolLoopCompactConfig
        from openjiuwen.harness.rails.sys_operation_rail import SysOperationRail

        checkpoint_config = self.config.get("submission_checkpoint", {})
        checkpoint_config = checkpoint_config if isinstance(checkpoint_config, dict) else {}

        output_dir.mkdir(parents=True, exist_ok=True)
        rollout_id = uuid.uuid4().hex
        source_skill_dirs = _skill_dirs(self.harness_dir)
        runtime_skill_root, skill_dirs = _materialize_runtime_skills(
            source_skill_dirs,
            task_workspace,
            rollout_id,
        )
        started_at = time.monotonic()
        prompt = _task_prompt(task, task_workspace)
        events: list[dict[str, Any]] = []
        runtime_errors: list[str] = []
        final_answer = ""
        exit_reason = "unknown"

        api_key = os.environ.get(self.model_config.api_key_env, "")
        if self.model_config.require_api_key and not api_key:
            raise RuntimeError(f"missing model API key environment: {self.model_config.api_key_env}")

        model = init_model(
            provider="OpenAI",
            model_name=self.model_config.model,
            api_key=api_key or "EMPTY_API_KEY",
            api_base=self.model_config.api_base,
            temperature=self.model_config.temperature if self.model_config.temperature is not None else 0.95,
            top_p=1.0,
            max_tokens=self.model_config.max_output_tokens,
            timeout=float(self.model_config.timeout_seconds),
            max_retries=3,
            verify_ssl=False,
        )
        token_tracker = TokenTrackingRail()
        anomaly_rail = ModelAnomalyDetectionRail(
            tool_loop_compact=ToolLoopCompactConfig(**self.config.get("tool_loop_compaction", {}))
        )
        checkpoint_rail = SubmissionCheckpointRail(
            model=model,
            task_prompt=prompt,
            enabled=bool(checkpoint_config.get("enabled", False)),
            instruction=str(checkpoint_config.get("instruction", "") or ""),
            max_revisions=int(checkpoint_config.get("max_revisions", 1)),
            max_collected_evidence_items=int(checkpoint_config.get("max_collected_evidence_items", 96)),
            max_collected_evidence_chars=int(checkpoint_config.get("max_collected_evidence_chars", 256000)),
            max_tool_result_chars=int(checkpoint_config.get("max_tool_result_chars", 8000)),
            max_audit_evidence_items=int(checkpoint_config.get("max_audit_evidence_items", 48)),
            max_audit_evidence_chars=int(checkpoint_config.get("max_audit_evidence_chars", 64000)),
            max_task_chars=int(checkpoint_config.get("max_task_chars", 10000)),
            max_draft_chars=int(checkpoint_config.get("max_draft_chars", 12000)),
            max_ground_count=int(checkpoint_config.get("max_ground_count", 12)),
            max_audit_prompt_chars=int(checkpoint_config.get("max_audit_prompt_chars", 96000)),
            audit_timeout_seconds=float(checkpoint_config.get("audit_timeout_seconds", 180.0)),
            max_audit_retries=int(checkpoint_config.get("max_audit_retries", 1)),
            max_rechecks=int(checkpoint_config.get("max_rechecks", 1)),
        )
        rails = [
            SysOperationRail(),
            ContextProcessorRail(preset=True),
            ContextAssembleRail(),
            anomaly_rail,
            token_tracker,
            ToolTrackingRail(),
        ]
        if checkpoint_rail.enabled:
            rails.append(checkpoint_rail)
        if skill_dirs:
            skill_roots = sorted({str(Path(path).parent) for path in skill_dirs})
            rails.append(
                SkillUseRail(
                    skills_dir=skill_roots,
                    skill_mode="all",
                    include_tools=False,
                )
            )
        max_steps = int(self.config.get("max_steps", 120))
        wall_clock = float(self.config.get("rollout_wall_clock_seconds", 3600))
        model_transport_retry_limit = max(0, min(int(self.config.get("model_transport_retries", 2)), 3))
        model_transport_retries = 0
        effective_shell_timeout = int(self.config.get("command_timeout_seconds", command_timeout_seconds))
        system_prompt = (
            self.system_prompt
            + f"\n\nRuntime limits: at most {max_steps} agent iterations, "
            + f"{wall_clock:.0f} seconds total, and {effective_shell_timeout} seconds per shell command."
        )
        agent = create_deep_agent(
            model,
            system_prompt=system_prompt,
            rails=rails,
            enable_task_loop=True,
            enable_task_planning=True,
            max_iterations=max_steps,
            workspace=str(task_workspace),
            restrict_to_work_dir=True,
            language="en",
            parallel_tool_calls=False,
            # Agent completion is bounded by the rollout wall clock.  The shell
            # timeout applies to one command only and must not cap the whole task.
            completion_timeout=wall_clock,
            enable_model_anomaly_detection_rail=True,
        )

        await Runner.start()
        try:

            async def consume(query: str) -> None:
                nonlocal final_answer, exit_reason
                async for chunk in Runner.run_agent_streaming(
                    agent,
                    {"query": query, "conversation_id": rollout_id},
                    session=rollout_id,
                ):
                    event = _chunk_dict(chunk)
                    events.append(event)
                    controller_error = _controller_task_failed_error(event)
                    if controller_error:
                        raise RuntimeError(controller_error)
                    event_type = str(event.get("type") or "")
                    payload = event.get("payload")
                    if event_type in {"answer", "agent_final", "workflow_final"}:
                        if isinstance(payload, dict):
                            candidate = payload.get("output") or payload.get("answer") or payload.get("content")
                        else:
                            candidate = payload
                        if candidate:
                            final_answer = str(candidate)
                exit_reason = "completed"

            query = prompt
            while True:
                remaining = wall_clock - (time.monotonic() - started_at)
                if remaining <= 0:
                    raise asyncio.TimeoutError
                try:
                    await asyncio.wait_for(consume(query), timeout=remaining)
                    break
                except Exception as exc:
                    if (
                        not _is_retryable_model_transport_error(exc)
                        or model_transport_retries >= model_transport_retry_limit
                    ):
                        raise
                    model_transport_retries += 1
                    events.append(
                        {
                            "type": "rsi_model_transport_retry",
                            "payload": {
                                "attempt": model_transport_retries,
                                "error_type": type(exc).__name__,
                            },
                        }
                    )
                    query = (
                        "A transient model transport interruption occurred. Resume the same public task from "
                        "the task-visible evidence and tool state already present in this session. Do not restart "
                        "completed work; finish the remaining verification and return the requested final answer."
                    )
        except asyncio.TimeoutError:
            exit_reason = "rollout_wall_clock_timeout"
            runtime_errors.append(f"rollout exceeded {wall_clock:.0f} seconds")
            try:
                await agent.abort()
            except Exception as exc:
                runtime_errors.append(f"agent_abort_error: {type(exc).__name__}: {exc}")
        except Exception as exc:
            exit_reason = "deepagent_error"
            runtime_errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            try:
                await Runner.stop()
            except Exception as exc:
                runtime_errors.append(f"runner_stop_error: {type(exc).__name__}: {exc}")
            if runtime_skill_root is not None:
                try:
                    shutil.rmtree(runtime_skill_root)
                except OSError as exc:
                    runtime_errors.append(f"skill_cleanup_error: {type(exc).__name__}: {exc}")

        duration = time.monotonic() - started_at
        token_usage = token_tracker.get_summary()
        messages = _trajectory_messages(prompt, events, final_answer)
        trajectory_path = output_dir / "trajectory.json"
        metadata_path = output_dir / "metadata.json"
        trajectory_path.write_text(
            json.dumps(
                {
                    "rollout_id": rollout_id,
                    "messages": messages,
                    "trajectory": events,
                    "engine": "openjiuwen-deepagent",
                    "engine_revision": self.config.get("engine_revision"),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        metadata_path.write_text(
            json.dumps(
                {
                    "rollout_id": rollout_id,
                    "task_id": str(task["id"]),
                    "task_domain": str(task.get("domain", "unknown")),
                    "policy_harness_revision": harness_revision,
                    "model_config_id": model_config_id,
                    "duration_seconds": duration,
                    "exit_reason": exit_reason,
                    "final_answer": final_answer,
                    "artifact_path": str(task_workspace),
                    "runtime_errors": runtime_errors,
                    "token_usage": token_usage,
                    "steps": int(token_usage.get("model_calls", 0)),
                    "engine": "openjiuwen-deepagent",
                    "engine_revision": self.config.get("engine_revision"),
                    "context_processor": "forked-preset",
                    "registered_skills": [Path(path).name for path in source_skill_dirs],
                    "tool_loop_compaction": self.config.get("tool_loop_compaction", {}),
                    "model_transport_retry_limit": model_transport_retry_limit,
                    "model_transport_retries": model_transport_retries,
                    "submission_checkpoint": {
                        **checkpoint_config,
                        **checkpoint_rail.metadata(),
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return PolicyRolloutResult(
            rollout_id=rollout_id,
            task_id=str(task["id"]),
            task_domain=str(task.get("domain", "unknown")),
            trajectory_path=trajectory_path,
            metadata_path=metadata_path,
            final_answer=final_answer,
            exit_reason=exit_reason,
            steps=int(token_usage.get("model_calls", 0)),
            duration_seconds=duration,
            token_usage={key: int(value) for key, value in token_usage.items() if isinstance(value, int)},
            runtime_errors=runtime_errors,
        )
