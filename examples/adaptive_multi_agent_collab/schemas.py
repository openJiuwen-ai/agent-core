import hashlib, json, os, re
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import LABELS

@dataclass(frozen=True)
class MCQExample:
    example_id: str; source_split: str; question: str
    options: dict[str, str]; gold_label: str
    def __post_init__(self) -> None:
        options, gold = {str(k).upper(): str(v) for k, v in self.options.items()}, self.gold_label.upper()
        if not options or any(label not in LABELS for label in options) or gold not in options:
            raise ValueError("Options and gold label must use available labels A through E")
        object.__setattr__(self, "options", options); object.__setattr__(self, "gold_label", gold)
    id = property(lambda self: self.example_id)

@dataclass(frozen=True)
class ParsedAnswer:
    answer: str | None; justification: str = ""; parse_method: str = "unparsed"
    parse_error: str | None = None

@dataclass(frozen=True)
class ParsedReviewer:
    status: str | None; feedback: str = ""; recommended_answer: str | None = None
    parse_method: str = "unparsed"; parse_error: str | None = None
    protocol_inconsistent: bool = False; protocol_repair: bool = False

def validate_reviewer_protocol(result: ParsedReviewer, answer: str, *, repair: bool = False) -> ParsedReviewer:
    inconsistent = result.status == "complete" and result.recommended_answer != answer.upper()
    return replace(result, status="continue" if repair else result.status,
                   protocol_inconsistent=True, protocol_repair=repair) if inconsistent else result

class AnswerParser:
    _answer = re.compile(r"(?i)\b(?:final\s+answer|answer)\s*(?:(?:is|:|=)\s*)?[\[(]?([A-E])[\])]?(?=\W|$)")
    _isolated = re.compile(r"(?i)^\s*[\[(]?([A-E])[\])]?[.!]?\s*$")
    _status = re.compile(r"(?i)\bstatus\s*(?:is|:|=)\s*(continue|complete)\b")
    _recommended = re.compile(r"(?i)\b(?:recommended[_ ]answer|answer)\s*(?:(?:is|:|=)\s*)?[\[(]?([A-E])[\])]?(?=\W|$)")
    _feedback = re.compile(r"(?is)\bfeedback\s*(?:(?:is|:|=)\s*)?(.+?)(?=\s*;\s*(?:status|recommended)|\n|$)")
    @staticmethod
    def _parts(value: Any, structured: Any) -> tuple[str, Any]:
        if isinstance(value, str):
            return value, structured
        return str(getattr(value, "content", "") or ""), structured if structured is not None else getattr(value, "parser_content", None)
    @staticmethod
    def _object(value: Any) -> dict[str, Any] | None:
        if isinstance(value, dict): return value
        if not isinstance(value, str): return None
        decoder = json.JSONDecoder()
        for brace in re.finditer(r"\{", value):
            try:
                parsed, _ = decoder.raw_decode(value[brace.start():])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        return None
    @staticmethod
    def _label(value: Any) -> str | None:
        label = str(value).strip().upper() if value is not None else ""
        return label if label in LABELS else None
    def parse_answer(self, value: Any, parser_content: Any = None) -> ParsedAnswer:
        content, structured = self._parts(value, parser_content)
        errors: list[str] = []
        for method, candidate in (("structured", structured), ("content_json", content)):
            obj = self._object(candidate)
            if obj and (label := self._label(obj.get("answer"))):
                return ParsedAnswer(label, str(obj.get("justification", "")).strip(), method)
            if candidate not in (None, ""):
                errors.append(f"{method} lacked a valid A-E answer")
        explicit = self._answer.search(content)
        match, method = explicit or self._isolated.fullmatch(content), "explicit_marker" if explicit else "isolated_label"
        if match:
            return ParsedAnswer(match.group(1).upper(), parse_method=method, parse_error="; ".join(errors) or None)
        return ParsedAnswer(None, parse_error="; ".join([*errors, "no unambiguous answer marker found"]))
    def parse_reviewer(self, value: Any, parser_content: Any = None) -> ParsedReviewer:
        content, structured = self._parts(value, parser_content)
        errors: list[str] = []
        for method, candidate in (("structured", structured), ("content_json", content)):
            obj = self._object(candidate)
            status = str(obj.get("status", "")).strip().lower() if obj else ""
            answer = self._label(obj.get("recommended_answer")) if obj else None
            if status in {"continue", "complete"} and answer:
                return ParsedReviewer(status, str(obj.get("feedback", "")).strip(), answer, method)
            if candidate not in (None, ""):
                errors.append(f"{method} lacked valid reviewer fields")
        status, answer, feedback = self._status.search(content), self._recommended.search(content), self._feedback.search(content)
        if status and answer:
            return ParsedReviewer(
                status.group(1).lower(), feedback.group(1).strip() if feedback else "", answer.group(1).upper(),
                "explicit_marker", "; ".join(errors) or None,
            )
        return ParsedReviewer(None, parse_error="; ".join([*errors, "no valid reviewer markers found"]))

@dataclass(frozen=True)
class CacheKey:
    example_id: str; source_split: str; provider: str; model_name: str
    agent_id: int; role_name: str; stage: str; prompt_hash: str
    generation_settings: dict[str, Any]
    initiator_id: int | None = None; reviewer_id: int | None = None
    def __post_init__(self) -> None:
        if {"api_key", "authorization", "secret"} & {key.lower() for key in self.generation_settings}:
            raise ValueError("Cache generation settings contain a forbidden secret field")
    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

@dataclass
class CallRecord:
    key: CacheKey; mode: str; valid: bool; attempt: int
    raw_prompt: str; raw_response: str; parsed: dict[str, Any]; wall_latency: float
    usage_metadata: dict[str, Any] | None = None; error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    attempt_errors: list[str] = field(default_factory=list)
    generation_adjustments: list[str] = field(default_factory=list)
    def to_dict(self) -> dict[str, Any]: return {**asdict(self), "cache_key": self.key.digest}

@dataclass
class Trajectory:
    example: MCQExample; session_index: int = 0
    initial_turns: dict[int, dict[str, Any]] = field(default_factory=dict)
    conversations: list[dict[str, Any]] = field(default_factory=list)
    terminal_answers: dict[int, list[str]] = field(default_factory=dict)
    failure: str | None = None; run_fingerprint: str = ""
    def to_dict(self) -> dict[str, Any]: return asdict(self)
    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Trajectory":
        data = dict(value)
        data["example"] = MCQExample(**data["example"])
        for name in ("initial_turns", "terminal_answers"):
            data[name] = {int(key): item for key, item in data.get(name, {}).items()}
        return cls(**data)

class JsonlCallCache:
    _forbidden = {"api_key", "authorization", "secret"}
    def __init__(self, path: Path, mode: str, force_regenerate: bool = False) -> None:
        if mode not in {"real", "mock"}: raise ValueError("Cache mode must be real or mock")
        self.path, self.mode, self.force_regenerate = Path(path), mode, force_regenerate
        self.records: dict[str, dict[str, Any]] = {}; self.malformed_lines = 0
        self.reload()
    def reload(self) -> None:
        self.records.clear(); self.malformed_lines = 0
        if not self.path.exists(): return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                self.malformed_lines += 1; continue
            if record.get("valid") and record.get("mode") == self.mode and record.get("cache_key"):
                self.records[record["cache_key"]] = record
    def get(self, key: CacheKey | str) -> dict[str, Any] | None:
        return None if self.force_regenerate else self.records.get(key.digest if isinstance(key, CacheKey) else key)
    def append(self, record: CallRecord | dict[str, Any]) -> None:
        value = record.to_dict() if isinstance(record, CallRecord) else record
        if value.get("mode") != self.mode or self._contains_secret(value):
            raise ValueError("Refusing to serialize a wrong-mode or secret-bearing record")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, default=str) + "\n"); stream.flush()
        if value.get("valid") and value.get("cache_key"):
            self.records[value["cache_key"]] = value
    @classmethod
    def _contains_secret(cls, value: Any) -> bool:
        if isinstance(value, dict):
            return any(str(k).lower() in cls._forbidden or cls._contains_secret(v) for k, v in value.items())
        if isinstance(value, (list, tuple)):
            return any(map(cls._contains_secret, value))
        configured_key = os.environ.get("API_KEY")
        return isinstance(value, str) and bool(configured_key and configured_key in value)
