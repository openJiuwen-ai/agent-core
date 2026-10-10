"""CommonsenseQA loading and one cyclic reviewer-revision session."""
from __future__ import annotations

import asyncio, hashlib, json, random
from dataclasses import asdict
from typing import Any, Iterable, Sequence

from .config import AGENT_IDS, ExperimentConfig, LABELS
from .openjiuwen_client import OpenJiuwenClient
from .prompts import PROMPT_HASH, initial_prompt, reviewer_prompt, revision_prompt
from .schemas import (
    AnswerParser, CacheKey, CallRecord, JsonlCallCache, MCQExample, ParsedAnswer,
    ParsedReviewer, Trajectory, validate_reviewer_protocol,
)
REVIEWER_EDGES = (("g_01", 0, 1), ("g_12", 1, 2), ("g_20", 2, 0))
ROLE_NAMES = {0: "analytical_solver", 1: "option_eliminator", 2: "skeptical_verifier"}

async def _settled_gather(*awaitables: Any) -> list[Any]:
    results = list(await asyncio.gather(*awaitables, return_exceptions=True))
    error = next((item for item in results if isinstance(item, BaseException)), None)
    if error is not None:
        raise error
    return results

def _example(row: dict[str, Any], source: str) -> MCQExample:
    choices = row["choices"]; options = dict(zip(choices["label"], choices["text"], strict=True))
    return MCQExample(str(row["id"]), source, str(row["question"]), options, row["answerKey"])

def select_dataset_splits(
    train_rows: Sequence[dict[str, Any]], validation_rows: Sequence[dict[str, Any]],
    config: ExperimentConfig,
) -> dict[str, list[MCQExample]]:
    if len(train_rows) < config.train_size + config.val_size:
        raise ValueError("CommonsenseQA train split is smaller than requested train+validation sizes")
    if len(validation_rows) < config.test_size: raise ValueError("validation split is too small")
    train_ids, test_ids = list(range(len(train_rows))), list(range(len(validation_rows)))
    random.Random(config.seed).shuffle(train_ids); random.Random(config.seed + 1).shuffle(test_ids)
    boundary = config.train_size
    selected = {
        "train": [_example(train_rows[i], "train") for i in train_ids[:boundary]],
        "validation": [_example(train_rows[i], "train")
                       for i in train_ids[boundary : boundary + config.val_size]],
        "test": [_example(validation_rows[i], "validation") for i in test_ids[: config.test_size]],
    }
    sets = [{item.example_id for item in values} for values in selected.values()]
    if sets[0] & sets[1] or sets[0] & sets[2] or sets[1] & sets[2]: raise AssertionError("dataset splits overlap")
    return selected

def synthetic_splits(config: ExperimentConfig) -> dict[str, list[MCQExample]]:
    result: dict[str, list[MCQExample]] = {}
    sizes = (("train", config.train_size), ("validation", config.val_size), ("test", config.test_size))
    for split_index, (name, size) in enumerate(sizes):
        result[name] = [
            MCQExample(
                f"synthetic-{name}-{index:03d}", f"synthetic_{name}",
                f"Synthetic control question {split_index}-{index}: choose the indexed option.",
                {label: f"Synthetic option {label}" for label in LABELS},
                LABELS[(index + split_index) % len(LABELS)])
            for index in range(size)
        ]
    return result

def load_dataset_splits(config: ExperimentConfig) -> dict[str, list[MCQExample]]:
    if config.offline_mock: return synthetic_splits(config)
    try:
        from datasets import load_dataset
        dataset = load_dataset("tau/commonsense_qa")
        return select_dataset_splits(dataset["train"], dataset["validation"], config)
    except Exception as exc:
        command = ("/Users/IDLE_And_R/.virtualenvs/openjiuwen-agent-core/bin/python "
                   "-m examples.adaptive_multi_agent_collab.run_experiment generate")
        raise RuntimeError(f"CommonsenseQA could not be loaded: {exc}. Retry with: {command}") from exc

class AdaptiveCollaborationExperiment:
    def __init__(self, config: ExperimentConfig, client: OpenJiuwenClient, cache: JsonlCallCache) -> None:
        self.config, self.client, self.cache = config, client, cache
        self.parser = AnswerParser()
        self.trajectory_path = config.mode_root / "cache" / "trajectories.jsonl"

    @property
    def run_fingerprint(self) -> str:
        payload = {
            "cache_schema_version": 2, "client": self.client.non_secret_fingerprint(),
            "prompt_hash": PROMPT_HASH, "routing": REVIEWER_EDGES,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _key(
        self, example: MCQExample, agent: int, stage: str, prompt: str,
        initiator: int | None = None, reviewer: int | None = None,
    ) -> CacheKey:
        settings = {**self.client.effective_generation_settings(), "run_fingerprint": self.run_fingerprint}
        prompt_hash = hashlib.sha256(f"{PROMPT_HASH}\0{prompt}".encode()).hexdigest()
        return CacheKey(
            example.example_id, example.source_split, self.client.provider,
            self.client.model_name, agent, ROLE_NAMES[agent], stage, prompt_hash,
            settings, initiator, reviewer,
        )

    async def _call(
        self, example: MCQExample, agent: int, stage: str, prompt: str, *,
        initiator: int | None = None, reviewer: int | None = None,
        context: dict[str, Any] | None = None,
    ) -> tuple[ParsedAnswer | ParsedReviewer, dict[str, Any]]:
        key = self._key(example, agent, stage, prompt, initiator, reviewer)
        parse = self.parser.parse_reviewer if stage == "review" else self.parser.parse_answer
        cached = self.cache.get(key)
        if cached:
            parsed = parse(cached["raw_response"], cached.get("parsed"))
            return parsed, {**cached, "cached": True, "stage": stage, "agent_id": agent}
        remaining, used = self.config.max_retries + 1, 0
        while remaining:
            result = await self.client.invoke(agent, prompt, stage=stage, context=context,
                                              max_attempts=remaining)
            used += result.attempts
            remaining = max(0, self.config.max_retries + 1 - used)
            parsed = parse(result.content, result.parser_content)
            valid = (parsed.status is not None and parsed.recommended_answer is not None
                     if isinstance(parsed, ParsedReviewer) else parsed.answer is not None)
            # Provider capability repair can change effective settings during invoke.
            key = self._key(example, agent, stage, prompt, initiator, reviewer)
            record = CallRecord(
                key=key, mode=self.config.mode, valid=valid, attempt=used,
                raw_prompt=prompt, raw_response=result.content, parsed=asdict(parsed),
                wall_latency=result.wall_latency, usage_metadata=result.usage_metadata,
                error=(result.error or parsed.parse_error) if not valid else None,
                attempt_errors=result.attempt_errors,
                generation_adjustments=result.generation_adjustments,
            )
            self.cache.append(record)
            call = {**record.to_dict(), "cached": False, "stage": stage, "agent_id": agent}
            if valid or result.error or not remaining: return parsed, call
        raise RuntimeError("unreachable call retry state")

    async def _initial(self, example: MCQExample, agent: int) -> tuple[int, dict[str, Any]]:
        parsed, call = await self._call(example, agent, "initial", initial_prompt(example, agent),
                                        context={"example_id": example.example_id})
        assert isinstance(parsed, ParsedAnswer)
        return agent, {
            "agent_id": agent, "answer": parsed.answer, "justification": parsed.justification,
            "parse_method": parsed.parse_method, "parse_error": parsed.parse_error, "call": call,
        }

    async def _conversation(
        self, example: MCQExample, name: str, initiator: int, reviewer: int,
        turn: dict[str, Any],
    ) -> dict[str, Any]:
        answer, justification = turn["answer"], turn["justification"]
        review, call = await self._call(
            example, reviewer, "review", reviewer_prompt(example, reviewer, initiator, answer, justification),
            initiator=initiator, reviewer=reviewer,
            context={"example_id": example.example_id, "current_answer": answer})
        assert isinstance(review, ParsedReviewer)
        checked = validate_reviewer_protocol(review, answer)
        initial_inconsistency, calls = checked.protocol_inconsistent, [call]
        repair = False
        if initial_inconsistency:
            corrected, corrected_call = await self._call(
                example, reviewer, "review", reviewer_prompt(
                    example, reviewer, initiator, answer, justification, corrective=True),
                initiator=initiator, reviewer=reviewer,
                context={"example_id": example.example_id, "current_answer": answer, "corrective": True})
            assert isinstance(corrected, ParsedReviewer)
            calls.append(corrected_call)
            checked = validate_reviewer_protocol(
                corrected if corrected.status is not None else review, answer, repair=True)
            repair = True
        terminal, revision = answer, None
        if checked.status == "continue":
            revision, revision_call = await self._call(
                example, initiator, "revision", revision_prompt(
                    example, initiator, answer, checked.feedback, checked.recommended_answer or answer),
                initiator=initiator, reviewer=reviewer,
                context={"example_id": example.example_id, "current_answer": answer})
            assert isinstance(revision, ParsedAnswer)
            terminal = revision.answer or answer
            calls.append(revision_call)
        return {
            "conversation_id": name, "initiator_id": initiator, "reviewer_id": reviewer,
            "scheme": "reviewer_revision", "initial_answer": answer,
            "reviewer_status": checked.status, "reviewer_feedback": checked.feedback,
            "reviewer_recommended_answer": checked.recommended_answer,
            "reviewer_parse_method": checked.parse_method, "reviewer_parse_error": checked.parse_error,
            "protocol_inconsistency": initial_inconsistency,
            "protocol_repair": repair or checked.protocol_repair,
            "revision_answer": getattr(revision, "answer", None),
            "revision_justification": getattr(revision, "justification", ""),
            "revision_parse_method": getattr(revision, "parse_method", None),
            "revision_parse_error": getattr(revision, "parse_error", None),
            "terminal_answer": terminal, "calls": calls,
        }

    def _validate(self, trajectory: Trajectory) -> str | None:
        issues = [f"agent {agent} initial answer missing" for agent in AGENT_IDS
                  if trajectory.initial_turns.get(agent, {}).get("answer") not in LABELS]
        by_initiator = {item["initiator_id"]: item for item in trajectory.conversations}
        for agent in AGENT_IDS:
            conversation = by_initiator.get(agent)
            if not conversation:
                issues.append(f"agent {agent} conversation missing"); continue
            if conversation.get("reviewer_status") not in {"complete", "continue"}:
                issues.append(f"agent {agent} reviewer output invalid")
            if conversation.get("terminal_answer") not in LABELS:
                issues.append(f"agent {agent} terminal answer missing")
        return "; ".join(issues) or None

    async def run_example(self, example: MCQExample) -> Trajectory:
        trajectory = Trajectory(example, run_fingerprint=self.run_fingerprint)
        try:
            trajectory.initial_turns = dict(await _settled_gather(
                *(self._initial(example, agent) for agent in AGENT_IDS)))
            trajectory.conversations = list(await _settled_gather(*(
                self._conversation(example, name, initiator, reviewer, trajectory.initial_turns[initiator])
                for name, initiator, reviewer in REVIEWER_EDGES
                if trajectory.initial_turns[initiator]["answer"] in LABELS
            )))
            trajectory.terminal_answers = {item["initiator_id"]: [item["terminal_answer"]]
                                           for item in trajectory.conversations
                                           if item["terminal_answer"] in LABELS}
            trajectory.failure = self._validate(trajectory)
        except Exception as exc:
            trajectory.failure = f"{type(exc).__name__}: {exc}"
        trajectory.run_fingerprint = self.run_fingerprint
        return trajectory

    async def generate(self, splits: dict[str, Iterable[MCQExample]]) -> list[Trajectory]:
        queue: asyncio.Queue[MCQExample] = asyncio.Queue()
        for split in ("train", "validation", "test"):
            for example in splits[split]:
                queue.put_nowait(example)
        completed: list[Trajectory] = []

        async def worker() -> None:
            while not queue.empty() and not self.client.budget.exhausted:
                trajectory = await self.run_example(queue.get_nowait())
                completed.append(trajectory)
                self.trajectory_path.parent.mkdir(parents=True, exist_ok=True)
                with self.trajectory_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(trajectory.to_dict(), ensure_ascii=False) + "\n")
                    stream.flush()
                queue.task_done()

        await asyncio.gather(*(worker() for _ in range(self.config.concurrency)))
        return completed
