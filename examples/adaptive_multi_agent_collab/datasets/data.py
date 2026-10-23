"""Local query ingestion and the four disjoint scientific data partitions."""

from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..infrastructure.randomness import derive_seed
from ..schemas import QueryExample


PARTITIONS = ("train", "comp-val", "sys-val", "test")
DUPLICATE_RULE = "original_identity_or_casefolded_whitespace_question_v1"


def question_fingerprint(question: str) -> str:
    """Group exact questions modulo casing and whitespace, before splitting."""

    normalized = " ".join(question.casefold().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _original_id(example: QueryExample) -> str:
    return str(example.metadata.get("original_query_id", example.example_id))


@dataclass(frozen=True, slots=True)
class DatasetSource:
    path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class DatasetProvenance:
    dataset_name: str
    revision: str
    sources: tuple[DatasetSource, ...]
    evaluation_protocol: str = "fixed_holdout"
    evaluation_artifacts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.dataset_name.strip() or not self.revision.strip():
            raise ValueError("dataset name and revision must be recorded")
        if self.evaluation_protocol.startswith("reconcile") and not self.evaluation_artifacts:
            raise ValueError("ReConcile reproduction requires verified evaluation-ID artifacts")


@dataclass(frozen=True, slots=True)
class DatasetPartitions:
    """Every derived record is checked against its original query's assignment."""

    train: tuple[QueryExample, ...]
    component_validation: tuple[QueryExample, ...]
    system_validation: tuple[QueryExample, ...]
    test: tuple[QueryExample, ...]
    provenance: DatasetProvenance
    split_seed: int
    excluded_duplicate_ids: tuple[str, ...] = ()
    duplicate_rule: str = DUPLICATE_RULE
    _by_id: Mapping[str, tuple[str, QueryExample]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.duplicate_rule != DUPLICATE_RULE:
            raise ValueError("unsupported duplicate-handling rule")
        derive_seed(self.split_seed, "split")
        by_id: dict[str, tuple[str, QueryExample]] = {}
        owners: dict[tuple[str, str], str] = {}
        for partition, examples in zip(PARTITIONS, self.groups, strict=True):
            if not examples:
                raise ValueError(f"{partition} must contain at least one original query")
            for example in examples:
                if example.example_id in by_id:
                    raise ValueError(f"query ID appears more than once: {example.example_id}")
                if partition != "test" and example.gold_answer is None:
                    raise ValueError("development queries require canonical gold answers")
                by_id[example.example_id] = (partition, example)
                keys = (("original", _original_id(example)), ("text", question_fingerprint(example.query)))
                for key in keys:
                    previous = owners.setdefault(key, partition)
                    if previous != partition:
                        raise ValueError(f"duplicate original question crosses {previous} and {partition}")
        if set(self.excluded_duplicate_ids) & set(by_id):
            raise ValueError("excluded duplicate IDs cannot be assigned to a partition")
        object.__setattr__(self, "_by_id", by_id)

    @property
    def groups(self) -> tuple[tuple[QueryExample, ...], ...]:
        return self.train, self.component_validation, self.system_validation, self.test

    def examples(self, partition: str) -> tuple[QueryExample, ...]:
        if partition not in PARTITIONS:
            raise ValueError(f"unknown data partition: {partition}")
        return self.groups[PARTITIONS.index(partition)]

    def select(self, partition: str, example_ids: Sequence[str]) -> tuple[QueryExample, ...]:
        if not example_ids or len(set(example_ids)) != len(example_ids):
            raise ValueError("a minibatch requires unique nonempty ordered query IDs")
        result: list[QueryExample] = []
        for example_id in example_ids:
            owner, example = self._by_id[example_id]
            if owner != partition:
                raise ValueError(f"query {example_id} belongs to {owner}, not {partition}")
            result.append(example)
        return tuple(result)

    def require_origin(self, example: QueryExample, partition: str) -> None:
        """Reject relabelled or foreign trajectory origins at execution boundaries."""

        recorded = self.select(partition, (example.example_id,))[0]
        if (
            _original_id(recorded) != _original_id(example)
            or question_fingerprint(recorded.query) != question_fingerprint(example.query)
            or recorded.candidates != example.candidates
            or recorded.option_texts != example.option_texts
            or recorded.open_answer != example.open_answer
            or recorded.gold_answer != example.gold_answer
        ):
            raise ValueError("derived example does not match its recorded original query")

    def manifest(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "provenance": asdict(self.provenance),
            "split_seed": self.split_seed,
            "duplicate_rule": self.duplicate_rule,
            "excluded_duplicate_ids": self.excluded_duplicate_ids,
            "partitions": {
                name: {
                    "count": len(examples),
                    "examples": [
                        {
                            "id": example.example_id,
                            "original_query_id": _original_id(example),
                            "question_sha256": question_fingerprint(example.query),
                            "content_sha256": _digest(
                                {
                                    "query": example.query,
                                    "candidates": example.candidates,
                                    "option_texts": getattr(example, "option_texts", ()),
                                    "open_answer": example.open_answer,
                                    "gold": example.gold_answer,
                                }
                            ),
                        }
                        for example in examples
                    ],
                }
                for name, examples in zip(PARTITIONS, self.groups, strict=True)
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.manifest())

    def write_manifest(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.manifest(), indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True, slots=True)
class ActiveDataPlan:
    """Frozen subsets of the existing partitions, including a training-only pilot."""

    train_ids: tuple[str, ...]
    component_validation_ids: tuple[str, ...]
    system_validation_ids: tuple[str, ...]
    pilot_ids: tuple[str, ...]
    selection_seed: int
    dataset_fingerprint: str
    profile: str = "strategyqa_initial"
    selection_rule: str = "stratified_without_replacement_v1"

    def __post_init__(self) -> None:
        derive_seed(self.selection_seed, "active")
        if self.profile not in {"strategyqa_initial", "offline_fixture", "registered_extension"}:
            raise ValueError("unknown active-data profile")
        for name in ("train_ids", "component_validation_ids", "system_validation_ids", "pilot_ids"):
            values = tuple(getattr(self, name))
            if (
                (not values and not (self.profile == "registered_extension" and name == "pilot_ids"))
                or any(not isinstance(value, str) or not value for value in values)
                or len(set(values)) != len(values)
            ):
                raise ValueError("active subsets require unique nonempty ordered original query IDs")
            object.__setattr__(self, name, values)
        if not set(self.pilot_ids).issubset(self.train_ids):
            raise ValueError("pilot queries must belong to the active training subset")
        if self.profile == "strategyqa_initial" and tuple(
            map(
                len,
                (
                    self.train_ids,
                    self.component_validation_ids,
                    self.system_validation_ids,
                    self.pilot_ids,
                ),
            )
        ) != (60, 20, 20, 8):
            raise ValueError("initial StrategyQA active counts must be 60/20/20 with an eight-query training pilot")
        expected_rule = (
            "explicit_registered_extension_v1" if self.profile == "registered_extension"
            else "stratified_without_replacement_v1"
        )
        if self.selection_rule != expected_rule:
            raise ValueError("unsupported active-data selection rule")
        if self.profile == "registered_extension" and self.pilot_ids:
            raise ValueError("restricted adaptation reuses reviewed parent safeguards; it does not schedule a new pilot")

    def validate(self, dataset: DatasetPartitions) -> None:
        if self.dataset_fingerprint != dataset.fingerprint:
            raise ValueError("active-data plan belongs to a different recorded dataset")
        for partition, identifiers in (
            ("train", self.train_ids),
            ("comp-val", self.component_validation_ids),
            ("sys-val", self.system_validation_ids),
        ):
            examples = dataset.select(partition, identifiers)
            if len({_original_id(example) for example in examples}) != len(examples) or len(
                {question_fingerprint(example.query) for example in examples}
            ) != len(examples):
                raise ValueError("active query counts must not include duplicate question aliases")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ActiveDataPlan:
        return cls(**dict(value))

    @property
    def fingerprint(self) -> str:
        return _digest(self.to_dict())


def _stratified_subset(examples: Sequence[QueryExample], count: int, seed: int, domain: str) -> tuple[str, ...]:
    strata: dict[str, list[QueryExample]] = defaultdict(list)
    seen_ids, seen_texts = set(), set()
    for example in sorted(examples, key=lambda item: item.example_id):
        original, text = _original_id(example), question_fingerprint(example.query)
        if original in seen_ids or text in seen_texts:
            continue
        seen_ids.add(original)
        seen_texts.add(text)
        if example.gold_answer is None:
            raise ValueError("active development selection requires known labels")
        strata[example.gold_answer].append(example)
    total = sum(map(len, strata.values()))
    if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > total:
        raise ValueError(f"active {domain} count exceeds its available unique questions")
    labels = sorted(strata)
    quotas = {label: count * len(strata[label]) / total for label in labels}
    allocated = {label: math.floor(quotas[label]) for label in labels}
    for label in sorted(labels, key=lambda label: (-(quotas[label] - allocated[label]), label))[
        : count - sum(allocated.values())
    ]:
        allocated[label] += 1
    chosen: list[str] = []
    for label in labels:
        values = strata[label]
        random.Random(derive_seed(seed, "active", domain, label)).shuffle(values)
        chosen.extend(example.example_id for example in values[: allocated[label]])
    random.Random(derive_seed(seed, "active", domain, "order")).shuffle(chosen)
    return tuple(chosen)


def select_active_data(
    dataset: DatasetPartitions,
    *,
    seed: int,
    profile: str = "strategyqa_initial",
    counts: tuple[int, int, int, int] = (60, 20, 20, 8),
) -> ActiveDataPlan:
    if profile == "registered_extension":
        raise ValueError("extensions require explicit registered ordered subsets; automatic selection is disabled")
    if len(counts) != 4:
        raise ValueError("active counts must specify train, component, system, and pilot sizes")
    train = _stratified_subset(dataset.train, counts[0], seed, "train")
    plan = ActiveDataPlan(
        train,
        _stratified_subset(dataset.component_validation, counts[1], seed, "comp-val"),
        _stratified_subset(dataset.system_validation, counts[2], seed, "sys-val"),
        _stratified_subset(dataset.select("train", train), counts[3], seed, "pilot"),
        seed,
        dataset.fingerprint,
        profile,
    )
    plan.validate(dataset)
    return plan


def load_strategyqa(
    source_files: Sequence[str | Path], *, revision: str
) -> tuple[tuple[QueryExample, ...], DatasetProvenance]:
    """Read explicitly supplied local StrategyQA JSON/JSONL files; never fetch data."""

    examples: dict[str, QueryExample] = {}
    sources: list[DatasetSource] = []
    for source in source_files:
        path = Path(source).resolve()
        raw = path.read_bytes()
        sources.append(DatasetSource(str(path), hashlib.sha256(raw).hexdigest()))
        if path.suffix == ".jsonl":
            records = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        else:
            decoded = json.loads(raw)
            if isinstance(decoded, Mapping):
                decoded = decoded.get("data", decoded)
            if isinstance(decoded, Mapping):
                records = [{"qid": key, **value} for key, value in decoded.items()]
            else:
                records = decoded
        if not isinstance(records, list):
            raise ValueError("StrategyQA source must contain a list or ID-keyed object of questions")
        for record in records:
            identifier = record.get("qid", record.get("id"))
            question = record.get("question")
            if identifier is None or not isinstance(question, str):
                raise ValueError("StrategyQA records require qid/id and question")
            answer = record.get("answer")
            if isinstance(answer, bool):
                gold = "yes" if answer else "no"
            elif answer is None:
                gold = None
            elif isinstance(answer, str) and answer.strip().casefold() in {"yes", "no"}:
                gold = answer.strip().casefold()
            else:
                raise ValueError("StrategyQA answer must be boolean, yes/no, or unavailable")
            example = QueryExample(
                str(identifier),
                question,
                ("yes", "no"),
                gold,
                metadata={"original_query_id": str(identifier)},
            )
            previous = examples.get(example.example_id)
            if previous is not None and previous != example:
                raise ValueError(f"conflicting source records for query {example.example_id}")
            examples[example.example_id] = example
    if not sources or not examples:
        raise ValueError("at least one nonempty local StrategyQA source is required")
    return tuple(examples.values()), DatasetProvenance("StrategyQA", revision, tuple(sources))


def partition_queries(
    examples: Sequence[QueryExample],
    *,
    evaluation_id_sets: Sequence[Sequence[str]],
    split_seed: int,
    provenance: DatasetProvenance,
    development_fractions: tuple[float, float, float] = (0.8, 0.1, 0.1),
) -> DatasetPartitions:
    """Reserve the union of test IDs first, then stratify grouped development queries."""

    if any(value <= 0 or not math.isfinite(value) for value in development_fractions):
        raise ValueError("development partition fractions must be finite and positive")
    if len(development_fractions) != 3 or not math.isclose(sum(development_fractions), 1.0):
        raise ValueError("three development partition fractions must sum to one")
    by_id = {example.example_id: example for example in examples}
    if len(by_id) != len(examples):
        raise ValueError("original example IDs must be unique before partitioning")
    ordered_reserved = tuple(dict.fromkeys(identifier for group in evaluation_id_sets for identifier in group))
    reserved = set(ordered_reserved)
    if not reserved or not reserved.issubset(by_id):
        raise ValueError("reserved evaluation IDs must be nonempty and verified in the supplied sources")

    # Union original identities and duplicate text, including transitive aliases.
    parent = {identifier: identifier for identifier in by_id}

    def find(identifier: str) -> str:
        while parent[identifier] != identifier:
            parent[identifier] = parent[parent[identifier]]
            identifier = parent[identifier]
        return identifier

    seen: dict[tuple[str, str], str] = {}
    for example in examples:
        for key in (("id", _original_id(example)), ("text", question_fingerprint(example.query))):
            previous = seen.setdefault(key, example.example_id)
            parent[find(example.example_id)] = find(previous)
    groups: dict[str, list[QueryExample]] = defaultdict(list)
    for example in examples:
        groups[find(example.example_id)].append(example)
    strata: dict[str, list[tuple[QueryExample, ...]]] = defaultdict(list)
    excluded: list[str] = []
    for group in groups.values():
        if any(example.example_id in reserved for example in group):
            excluded.extend(example.example_id for example in group if example.example_id not in reserved)
            continue
        labels = {example.gold_answer for example in group}
        if len(labels) != 1 or None in labels:
            raise ValueError("development duplicate groups require one consistent known label")
        strata[str(next(iter(labels)))].append(tuple(sorted(group, key=lambda item: item.example_id)))
    assigned: list[list[QueryExample]] = [[], [], []]
    for label, grouped in sorted(strata.items()):
        grouped.sort(key=lambda group: group[0].example_id)
        random.Random(derive_seed(split_seed, "split", label)).shuffle(grouped)
        raw_counts = [len(grouped) * value for value in development_fractions]
        counts = [math.floor(value) for value in raw_counts]
        for index in sorted(range(3), key=lambda i: (-(raw_counts[i] - counts[i]), i))[: len(grouped) - sum(counts)]:
            counts[index] += 1
        offset = 0
        for index, count in enumerate(counts):
            assigned[index].extend(example for group in grouped[offset : offset + count] for example in group)
            offset += count
    return DatasetPartitions(
        train=tuple(sorted(assigned[0], key=lambda item: item.example_id)),
        component_validation=tuple(sorted(assigned[1], key=lambda item: item.example_id)),
        system_validation=tuple(sorted(assigned[2], key=lambda item: item.example_id)),
        test=tuple(by_id[identifier] for identifier in ordered_reserved),
        provenance=provenance,
        split_seed=split_seed,
        excluded_duplicate_ids=tuple(sorted(excluded)),
    )
