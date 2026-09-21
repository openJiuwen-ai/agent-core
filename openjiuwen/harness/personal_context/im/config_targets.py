"""Translate the ``im_learning`` config section into scheduler inputs.

Kept as a thin helper so ``config.py`` (pydantic contracts) never imports
the im runtime package, and so the scheduler constructor stays free of
config parsing.
"""

from __future__ import annotations

from openjiuwen.harness.personal_context.config import ImLearningConfig
from openjiuwen.harness.personal_context.im.models import ImLearningTarget


def build_im_learning_targets(config: ImLearningConfig) -> tuple[ImLearningTarget, ...]:
    """Map ``ImLearningConfig.targets`` to frozen ``ImLearningTarget`` DTOs."""
    return tuple(
        ImLearningTarget(
            channel_id=item.channel_id,
            kind=item.kind,
            external_id=item.external_id,
            title=item.title,
        )
        for item in config.targets
    )


__all__ = ["build_im_learning_targets"]
