"""Frozen MiniLM text representations and append-only observable event caches.

The production path loads only an already available, pinned Hugging Face model.
The explicit offline fixture is a test double, never a substitute identified as
MiniLM in experiment metadata. Neither path belongs to alpha or W.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

import torch
from torch.nn import functional as F

from ..config import EncoderConfig

TEXT_DIMENSION = 384
ENCODER_ARCHITECTURE_ID = "frozen_minilm_semantic_registry_event_sequence_v4"
_EVENT_TYPES = (
    None,
    "initial",
    "initiation",
    "role-response",
    "revision-response",
    "judge-return",
    "tie-resolution",
    "judge-adjudication",
    "validation-attempt",
    "conversation-end",
    "safeguard",
    "parse-failure",
    "revision",
    "conversation-complete",
    "unknown",
)
_STATUSES = (None, "continue", "complete", "safeguard-complete", "unknown")
_PARSE_OUTCOMES = (None, "valid", "invalid", "exhausted", "not-applicable", "unknown")
_SCHEMES = (None, "0", "S_self", "S_D", "S_J", "S_T", "S_V", "unknown")
_TEXT_FIELDS = ("content", "answer", "explanation", "feedback")
_NUMERIC_FIELDS = (
    "history_index",
    "session",
    "round_index",
    "delivery_index",
    "processing_index",
    "answer_changed",
    "input_tokens",
    "output_tokens",
    "model_calls",
    "normalized_cost",
)


def _field(value: Any, name: str, default: Any = None) -> Any:
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def _value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def serialize_query(query: Any) -> str:
    """Serialize the complete task input and explicit options, excluding metadata/gold."""

    if isinstance(query, str):
        return query.strip()
    question = _field(query, "query", _field(query, "question", ""))
    options = _field(query, "option_texts", _field(query, "options"))
    observable: dict[str, Any] = {"query": str(question)}
    if options:
        if isinstance(options, Mapping):
            order = _field(query, "candidates", tuple(options))
            observable["options"] = [[str(key), str(options[key])] for key in order]
        else:
            identifiers = _field(query, "candidates")
            observable["options"] = (
                [[str(identifier), str(text)] for identifier, text in zip(identifiers, options, strict=True)]
                if identifiers is not None
                else list(options)
            )
    return _canonical(observable)


def answer_views(example: Any, answers: Sequence[str]) -> tuple[str, ...]:
    """Finite options bind exact identifiers to text; open answers retain the string."""

    options = _field(example, "option_texts", _field(example, "options"))
    if _field(example, "open_answer", False):
        return tuple(answers)
    if not options:
        # Datasets such as StrategyQA use self-describing yes/no labels. When no
        # separate option text is supplied, that label is also its complete text.
        options = {answer: answer for answer in _field(example, "candidates", answers)}
    if not isinstance(options, Mapping):
        options = dict(zip(_field(example, "candidates", ()), options, strict=True))
    return tuple(_canonical({"identifier": answer, "text": str(options[answer])}) for answer in answers)


def text_payload(event: Any) -> str:
    """Use only explicit observable natural-language fields, in a fixed field order."""

    pieces = []
    for name in _TEXT_FIELDS:
        value = _field(event, name, "")
        if value:
            pieces.append(f"{name}: {value}")
    return "\n".join(pieces)


def _identity(event: Any) -> EventIdentity:
    tag = _field(event, "tag")
    initiator = _field(tag, "initiator_id")
    receiver = _field(tag, "receiver_id")
    actor = _field(event, "actor_id")
    recipient = _field(event, "recipient_id")
    involved = tuple(
        sorted(
            {
                item
                for item in (actor, recipient, initiator, receiver, *_field(event, "involved_agent_ids", ()))
                if item is not None
            }
        )
    )
    return EventIdentity(actor, recipient, initiator, receiver, involved)


def observable_event(event: Any, index: int) -> dict[str, Any]:
    """Explicit whitelist: generic reward/gold-bearing dictionaries cannot leak in."""

    identity = _identity(event)
    usage = _field(event, "usage")
    payload: dict[str, Any] = {name: str(_field(event, name, "") or "") for name in _TEXT_FIELDS}
    payload.update(
        {
            "event": str(_value(_field(event, "event", "unknown"))),
            "identity": dataclasses.asdict(identity),
            "scheme": _value(_field(_field(event, "tag"), "scheme")),
            "status": _value(_field(event, "status")),
            "parse_outcome": _value(_field(event, "parse_outcome")),
            "history_index": index,
            "session": _field(event, "session"),
            "round_index": _field(event, "round_index"),
            "delivery_index": _field(event, "delivery_index"),
            "processing_index": _field(event, "processing_index"),
            "answer_changed": _field(event, "answer_changed"),
            "input_tokens": _field(usage, "input_tokens"),
            "output_tokens": _field(usage, "output_tokens"),
            "model_calls": _field(usage, "model_calls"),
            "normalized_cost": _field(event, "normalized_cost"),
        }
    )
    return payload


def serialize_history(history: Sequence[Any]) -> str:
    """Diagnostic serialization only; encoding never flattens history into one text."""

    return _canonical([observable_event(event, index) for index, event in enumerate(history, 1)])


class TextEncoder(Protocol):
    """A frozen text service, with explicit scientific identity."""

    encoder_type: str

    def encode(self, text: str) -> torch.Tensor: ...


class MiniLMTextEncoder:
    """Tokenizer-aligned full-text chunks, masked mean pooling and L2 aggregation.

    all-MiniLM-L6-v2 consists of its Transformer backbone, mean token pooling,
    and normalization. AutoModel uses the existing repository dependency;
    tokenizer special tokens count against the configured 256-token limit.
    """

    encoder_type = "minilm"

    def __init__(self, configuration: EncoderConfig, *, tokenizer: Any = None, model: Any = None) -> None:
        if (tokenizer is None) != (model is None):
            raise ValueError("inject both tokenizer and model, or neither")
        self.configuration = configuration
        self.tokenizer = tokenizer
        self.model = model
        self.dtype = getattr(torch, configuration.numerical_precision)
        if self.model is not None:
            self._freeze()

    def _freeze(self) -> None:
        self.model.eval()
        self.model.to(dtype=self.dtype)
        for parameter in self.model.parameters():
            parameter.requires_grad_(False)

    def _load(self) -> None:
        if self.model is not None:
            return
        if not re.fullmatch(r"[0-9a-f]{40}", self.configuration.model_revision):
            raise ValueError("MiniLM requires a pinned 40-character model_revision and local model files")
        from transformers import AutoModel, AutoTokenizer

        options = {"revision": self.configuration.model_revision, "local_files_only": True}
        if self.configuration.cache_directory is not None:
            options["cache_dir"] = self.configuration.cache_directory
        self.tokenizer = AutoTokenizer.from_pretrained(self.configuration.model_name, **options)
        self.model = AutoModel.from_pretrained(self.configuration.model_name, **options)
        self._freeze()

    def encode(self, text: str) -> torch.Tensor:
        if not text:
            return torch.zeros(TEXT_DIMENSION, dtype=self.dtype)
        self._load()
        token_ids = self.tokenizer.encode(text, add_special_tokens=False, truncation=False)
        budget = self.configuration.max_tokens - self.tokenizer.num_special_tokens_to_add(pair=False)
        if (
            self.tokenizer.num_special_tokens_to_add(pair=False) != 2
            or self.tokenizer.cls_token_id is None
            or self.tokenizer.sep_token_id is None
        ):
            raise ValueError("pinned MiniLM requires its BERT CLS/SEP framing")
        if budget <= 0:
            raise ValueError("encoder token limit leaves no room for text payload")
        chunks = [token_ids[offset : offset + budget] for offset in range(0, len(token_ids), budget)]
        if not chunks:
            return torch.zeros(TEXT_DIMENSION, dtype=self.dtype)
        vectors = []
        with torch.no_grad():
            for chunk in chunks:
                # The pinned tokenizer's single-sequence template is CLS A SEP.
                # Compose already-tokenized chunks directly: re-tokenizing decoded
                # chunks could alter boundaries; Transformers 5 removed its old
                # prepare_for_model helper. BERT defaults token_type_ids to zero.
                ids = torch.tensor([[self.tokenizer.cls_token_id, *chunk, self.tokenizer.sep_token_id]])
                inputs = {"input_ids": ids, "attention_mask": torch.ones_like(ids)}
                if inputs["input_ids"].shape[-1] > self.configuration.max_tokens:
                    raise ValueError("tokenizer produced a chunk exceeding the declared input limit")
                output = self.model(**inputs).last_hidden_state
                mask = inputs["attention_mask"].unsqueeze(-1).to(output.dtype)
                pooled = (output * mask).sum(dim=1) / mask.sum(dim=1).clamp_min(1.0)
                vectors.append(F.normalize(pooled.squeeze(0), dim=0))
            result = F.normalize(torch.stack(vectors).mean(dim=0), dim=0)
        if result.shape != (TEXT_DIMENSION,):
            raise ValueError("all-MiniLM-L6-v2 must produce 384-dimensional representations")
        return result.detach().cpu()


class OfflineTextEncoder:
    """Deterministic numerical fixture; it is not a language representation model."""

    encoder_type = "offline_fixture"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def encode(self, text: str) -> torch.Tensor:
        self.calls.append(text)
        if not text:
            return torch.zeros(TEXT_DIMENSION)
        payload = text.encode("utf-8")
        values = torch.tensor(
            [
                sum((position + 1) * (byte + 1) for position, byte in enumerate(payload[offset::TEXT_DIMENSION]))
                for offset in range(TEXT_DIMENSION)
            ],
            dtype=torch.float32,
        )
        return F.normalize(values, dim=0)


@dataclass(frozen=True, slots=True)
class EventIdentity:
    actor_id: int | None
    recipient_id: int | None
    initiator_id: int | None
    receiver_id: int | None
    involved_agent_ids: tuple[int, ...]

    def routing_relation(self, initiator: int, receiver: int) -> int:
        if (self.initiator_id, self.receiver_id) == (initiator, receiver):
            return 0
        if (self.initiator_id, self.receiver_id) == (receiver, initiator):
            return 1
        if initiator in self.involved_agent_ids:
            return 2
        if receiver in self.involved_agent_ids:
            return 3
        return 4

    def weighting_relation(self, agent: int) -> int:
        if self.actor_id == agent:
            return 0
        if self.recipient_id == agent:
            return 1
        return 2 if agent in self.involved_agent_ids else 3


@dataclass(frozen=True, slots=True)
class HistoryEncoding:
    """Fixed event vectors plus the immutable identities used by relation attention."""

    vectors: torch.Tensor
    identities: tuple[EventIdentity, ...]
    history_indices: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.vectors.ndim != 2 or self.vectors.shape[0] != len(self.identities):
            raise ValueError("one identity is required for each encoded event")
        if len(self.history_indices) != len(self.identities):
            raise ValueError("one position is required for each encoded event")
        if (
            any(index < 1 for index in self.history_indices)
            or tuple(sorted(set(self.history_indices))) != self.history_indices
        ):
            raise ValueError("history must contain ordered real events; padding positions are not accepted")
        if self.vectors.requires_grad:
            raise ValueError("fixed event vectors must be detached")

    def to(self, *, device: torch.device | str | None = None, dtype: torch.dtype | None = None) -> HistoryEncoding:
        return HistoryEncoding(self.vectors.to(device=device, dtype=dtype), self.identities, self.history_indices)


class HistoryCache:
    """One trajectory's append-only cache; finalized prefixes are never re-encoded."""

    def __init__(self, encoders: FixedBaseEncoders) -> None:
        self.encoders = encoders
        self._vectors: list[torch.Tensor] = []
        self._identities: list[EventIdentity] = []

    def __len__(self) -> int:
        return len(self._vectors)

    def append(self, event: Any) -> None:
        index = len(self) + 1
        recorded_index = _field(event, "history_index")
        if recorded_index is not None and recorded_index != index:
            raise ValueError("finalized events must have consecutive monotone history indices")
        observable = observable_event(event, index)
        self._vectors.append(self.encoders.encode_event(observable))
        self._identities.append(_identity(event))

    def extend(self, events: Sequence[Any]) -> None:
        for event in events:
            self.append(event)

    def snapshot(self, *, device: torch.device | str | None = None) -> HistoryEncoding:
        first = 0
        selected = self._vectors[first:]
        vectors = (
            torch.stack(selected)
            if selected
            else torch.empty((0, self.encoders.history_dimension), dtype=self.encoders.dtype)
        )
        return HistoryEncoding(
            vectors.to(device=device), tuple(self._identities[first:]), tuple(range(first + 1, len(self) + 1))
        )


def offline_encoder_configuration(**overrides: Any) -> EncoderConfig:
    return EncoderConfig(encoder_type="offline_fixture", model_revision="deterministic_fixture", **overrides)


class FixedBaseEncoders:
    """Shared fixed Enc_x and event-sequence Enc_H, with independent trajectory caches."""

    def __init__(
        self, n_agents: int, *, configuration: EncoderConfig | None = None, text_encoder: TextEncoder | None = None,
        scheme_order: Sequence[Any] | None = None,
        categories: Mapping[str, Sequence[str | None]] | None = None,
        agent_ids: Sequence[int] | None = None,
    ) -> None:
        if n_agents <= 0:
            raise ValueError("n_agents must be positive")
        self.n_agents = n_agents
        self.agent_ids = tuple(range(n_agents) if agent_ids is None else agent_ids)
        if len(self.agent_ids) != n_agents or len(set(self.agent_ids)) != n_agents:
            raise ValueError("agent identities must be unique and match n_agents")
        defaults = {"event": _EVENT_TYPES, "status": _STATUSES, "parse_outcome": _PARSE_OUTCOMES, "scheme": _SCHEMES}
        self.categories = dict(defaults)
        for name, values in (categories or {}).items():
            if name not in defaults:
                raise ValueError("unsupported structural category field")
            values = tuple(values)
            if values[:len(defaults[name])] != defaults[name] or len(set(values)) != len(values):
                raise ValueError("structural categories must append to the existing ordered registry")
            self.categories[name] = values
        if scheme_order is not None:
            labels = tuple(str(_value(label)) for label in scheme_order)
            additional = tuple(label for label in labels if label not in self.categories["scheme"])
            self.categories["scheme"] = (*self.categories["scheme"], *additional)
        self.configuration = configuration or EncoderConfig()
        self.dtype = getattr(torch, self.configuration.numerical_precision)
        self.text_encoder = text_encoder or (
            OfflineTextEncoder()
            if self.configuration.encoder_type == "offline_fixture"
            else MiniLMTextEncoder(self.configuration)
        )
        if self.text_encoder.encoder_type != self.configuration.encoder_type:
            raise ValueError("injected text encoder identity does not match EncoderConfig")
        if self.configuration.encoder_type == "minilm" and (
            not isinstance(self.text_encoder, MiniLMTextEncoder)
            or self.text_encoder.configuration != self.configuration
        ):
            raise ValueError("injected MiniLM configuration does not match EncoderConfig")
        self._query_cache: dict[str, torch.Tensor] = {}

    @property
    def query_dimension(self) -> int:
        return TEXT_DIMENSION

    @property
    def structural_dimension(self) -> int:
        return (
            sum(map(len, self.categories.values()))
            + 4 * (self.n_agents + 1)
            + self.n_agents
            + 2 * len(_NUMERIC_FIELDS)
        )

    @property
    def history_dimension(self) -> int:
        return TEXT_DIMENSION + self.structural_dimension

    @property
    def semantic_coordinates(self) -> tuple[str, ...]:
        """Column identities follow Appendix A.2, independent of positional shifts."""
        result = [f"text:{index}" for index in range(TEXT_DIMENSION)]
        for name, values in self.categories.items():
            result.extend(f"{name}:{_canonical(value)}" for value in values)
        for name in ("actor_id", "recipient_id", "initiator_id", "receiver_id"):
            result.extend(f"{name}:{_canonical(agent)}" for agent in (*self.agent_ids, None))
        result.extend(f"involved:{agent}" for agent in self.agent_ids)
        for name in _NUMERIC_FIELDS:
            result.extend((f"{name}:present", f"{name}:log1p"))
        return tuple(result)

    def extended(
        self, *, agent_ids: Sequence[int], scheme_order: Sequence[Any],
        categories: Mapping[str, Sequence[str | None]] | None = None,
    ) -> FixedBaseEncoders:
        ids = tuple(agent_ids)
        if ids[:len(self.agent_ids)] != self.agent_ids:
            raise ValueError("agent identities must be append-only")
        requested = dict(self.categories)
        requested.update(categories or {})
        for name, values in requested.items():
            if tuple(values)[:len(self.categories[name])] != self.categories[name]:
                raise ValueError("category identities must be append-only")
        result = FixedBaseEncoders(
            len(ids), configuration=self.configuration, text_encoder=self.text_encoder,
            agent_ids=ids, scheme_order=scheme_order, categories=requested,
        )
        # Structural extensions do not change frozen query text or its encoder.
        result._query_cache = self._query_cache
        return result

    def metadata(self) -> dict[str, Any]:
        return {
            "architecture_id": ENCODER_ARCHITECTURE_ID,
            **dataclasses.asdict(self.configuration),
            "n_agents": self.n_agents,
            "query_dimension": self.query_dimension,
            "history_event_dimension": self.history_dimension,
            "event_types": self.categories["event"],
            "statuses": self.categories["status"],
            "parse_outcomes": self.categories["parse_outcome"],
            "schemes": self.categories["scheme"],
            "agent_ids": self.agent_ids,
            "semantic_coordinates": self.semantic_coordinates,
            "text_field_order": _TEXT_FIELDS,
            "numeric_field_order": _NUMERIC_FIELDS,
            "numeric_encoding": "presence_then_log1p_nonnegative",
            "identity_encoding": "fixed_one_hot_with_missing_slot",
            "loader": (
                "transformers_auto_model_masked_mean"
                if self.configuration.encoder_type == "minilm"
                else "explicit_offline_fixture"
            ),
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical(self.metadata()).encode("utf-8")).hexdigest()

    def _encode_text(self, text: str) -> torch.Tensor:
        if not text:
            return torch.zeros(TEXT_DIMENSION, dtype=self.dtype)
        result = self.text_encoder.encode(text).detach().cpu().to(dtype=self.dtype)
        if result.shape != (TEXT_DIMENSION,) or not bool(torch.isfinite(result).all()):
            raise ValueError("frozen text encoder must return a finite 384-vector")
        return result.clone()

    def encode_query(self, query: Any, *, device: torch.device | str | None = None) -> torch.Tensor:
        text = serialize_query(query)
        query_encoder_key = hashlib.sha256(_canonical(dataclasses.asdict(self.configuration)).encode("utf-8")).hexdigest()
        key = query_encoder_key + ":" + text
        if key not in self._query_cache:
            self._query_cache[key] = self._encode_text(text)
        return self._query_cache[key].to(device=device).clone()

    def encode_event(self, observable: Mapping[str, Any]) -> torch.Tensor:
        features: list[float] = []
        for name, vocabulary in self.categories.items():
            selected = observable.get(name)
            if selected not in vocabulary:
                selected = "unknown"
            features.extend(float(value == selected) for value in vocabulary)
        identity = observable["identity"]
        if any(
            not isinstance(agent, int) or agent not in self.agent_ids for agent in identity["involved_agent_ids"]
        ):
            raise ValueError("event involved-agent identity outside configured pool")
        for name in ("actor_id", "recipient_id", "initiator_id", "receiver_id"):
            selected = identity[name]
            if selected is not None and selected not in self.agent_ids:
                raise ValueError("event agent identity outside configured pool")
            features.extend(float(value == selected) for value in (*self.agent_ids, None))
        features.extend(float(agent in identity["involved_agent_ids"]) for agent in self.agent_ids)
        for name in _NUMERIC_FIELDS:
            value = observable.get(name)
            if value is not None and (not math.isfinite(float(value)) or float(value) < 0):
                raise ValueError("observable numerical features must be finite and nonnegative")
            features.extend((float(value is not None), math.log1p(float(value)) if value is not None else 0.0))
        return torch.cat((self._encode_text(text_payload(observable)), torch.tensor(features, dtype=self.dtype)))

    def new_history_cache(self) -> HistoryCache:
        return HistoryCache(self)

    def encode_history(self, history: Sequence[Any], *, device: torch.device | str | None = None) -> HistoryEncoding:
        cache = self.new_history_cache()
        cache.extend(history)
        return cache.snapshot(device=device)
