"""Program artifact optimization: the contract, and the provider that fills it.

`ScienceDiscoveryProgramProvider` is imported lazily. It pulls in the vendored
search, which reaches for `agentdescent`, `numpy` and an isolation backend --
none of which a caller that only wants to *type-check against* the protocol has
any use for.
"""

from typing import TYPE_CHECKING, Any

from openjiuwen.rsi.artifact_rsi.program_opt.provider import ProgramArtifactProvider

if TYPE_CHECKING:
    from openjiuwen.rsi.artifact_rsi.program_opt.sciencediscovery_provider import (
        ScienceDiscoveryProgramProvider,
    )

__all__ = ["ProgramArtifactProvider", "ScienceDiscoveryProgramProvider"]


def __getattr__(name: str) -> Any:
    if name == "ScienceDiscoveryProgramProvider":
        from openjiuwen.rsi.artifact_rsi.program_opt.sciencediscovery_provider import (
            ScienceDiscoveryProgramProvider,
        )

        return ScienceDiscoveryProgramProvider
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
