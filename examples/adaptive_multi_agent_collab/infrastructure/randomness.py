"""Identifier-derived, domain-separated randomness without global RNG state."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, cast


SEED_DERIVATION_ID = "domain_separated_blake2b_v1"
VALIDATION_DOMAINS = frozenset({"promptval", "alphaval", "sysval", "wval"})


def derive_seed(run_seed: int, domain: str, *identifiers: str | int) -> int:
    """Derive one portable 63-bit seed; the literal purpose tag is mandatory."""

    if isinstance(run_seed, bool) or not isinstance(run_seed, int) or run_seed < 0:
        raise ValueError("run_seed must be a nonnegative integer")
    if not isinstance(domain, str) or not domain.strip():
        raise ValueError("seed domain must be a nonempty literal identifier")
    if any(isinstance(value, bool) or not isinstance(value, (str, int)) for value in identifiers):
        raise TypeError("seed identifiers must be strings or integers")
    payload = json.dumps(
        [SEED_DERIVATION_ID, run_seed, domain, *identifiers],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big") & ((1 << 63) - 1)


@dataclass(frozen=True, slots=True)
class LogicalCallIdentity:
    """The identity of an actual call, including initial answers and repairs."""

    agent_id: int
    session_id: int
    conversation_id: str
    call_type: str
    round_index: int = 0
    repair_attempt: int = 0

    def __post_init__(self) -> None:
        for name in ("agent_id", "session_id", "round_index", "repair_attempt"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"logical call {name} must be a nonnegative integer")
        if not isinstance(self.conversation_id, str) or not self.call_type:
            raise ValueError("logical calls require a conversation identity and call type")


@dataclass(frozen=True, slots=True)
class GenerationContext:
    """Fixed evaluation condition; no checkpoint, prompt, or update index enters its seed."""

    run_seed: int
    domain: str
    example_id: str
    component: str | None = None
    attempt_id: int | None = None

    def __post_init__(self) -> None:
        derive_seed(self.run_seed, self.domain, self.example_id)
        if not self.example_id.strip():
            raise ValueError("generation context requires a query identity")
        if self.domain == "train_generation":
            if self.component not in {"weighting", "routing", "prompt"} or (
                isinstance(self.attempt_id, bool) or not isinstance(self.attempt_id, int) or self.attempt_id < 0
            ):
                raise ValueError("training generation requires a component and globally allocated attempt ID")
        elif self.component is not None or self.attempt_id is not None:
            raise ValueError("validation generation cannot include a component or workflow attempt")

    def seed_for(self, logical_call: LogicalCallIdentity) -> int:
        identifiers = (
            (cast(str, self.component), self.example_id, cast(int, self.attempt_id))
            if self.domain == "train_generation"
            else (self.example_id,)
        )
        return derive_seed(
            self.run_seed,
            self.domain,
            *identifiers,
            logical_call.agent_id,
            logical_call.session_id,
            logical_call.conversation_id,
            logical_call.call_type,
            logical_call.round_index,
            logical_call.repair_attempt,
        )

    def descriptor(self, logical_call: LogicalCallIdentity) -> dict[str, Any]:
        return {
            "derivation_id": SEED_DERIVATION_ID,
            **asdict(self),
            "logical_call": asdict(logical_call),
            "seed": self.seed_for(logical_call),
        }
