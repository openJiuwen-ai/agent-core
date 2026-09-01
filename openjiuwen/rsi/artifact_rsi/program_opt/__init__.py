"""Program artifact optimization: the contract, and the provider that fills it.

`PuctProgramProvider` is imported lazily. It pulls in the search, which reaches
for `agentdescent`, `numpy` and an isolation backend -- none of which a caller
that only wants to *type-check against* the protocol has any use for.
"""

from typing import TYPE_CHECKING, Any

from openjiuwen.rsi.artifact_rsi.program_opt.provider import ProgramArtifactProvider

if TYPE_CHECKING:
    from openjiuwen.rsi.artifact_rsi.program_opt.puct_provider import PuctProgramProvider

__all__ = ["ProgramArtifactProvider", "PuctProgramProvider"]


def __getattr__(name: str) -> Any:
    if name == "PuctProgramProvider":
        from openjiuwen.rsi.artifact_rsi.program_opt.puct_provider import PuctProgramProvider

        return PuctProgramProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
