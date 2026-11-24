# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""Business failures for package loading.

Codes and message templates live in ``StatusCode`` under
``openjiuwen.core.common.exception``. Event triggering still uses the EventBus
exception channel.

Error style for this package:

- Raise ``ExtensionFailure`` when a single synchronous step must stop.
- Return ``OperationError`` (via ``operation_error`` / ``as_error``) when a
  Report, rejected-package list, or decision API needs a structured failure
  without unwinding the stack.
- Do not rebuild ``ExtensionFailure`` from a rendered message alone; keep
  template ``params`` so ``to_dict()`` stays accurate.

``ExtensionFailure`` subclasses ``ValidationError`` so existing
``except ValidationError`` handlers and ``STATUS_TO_EXCEPTION`` range mapping
still see extension failures. Load/copy/inflight codes additionally map to
``ExecutionError`` when constructed through ``build_error``.
"""

from dataclasses import dataclass, field
from typing import Any

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import ValidationError


@dataclass(frozen=True, slots=True)
class OperationError:
    """A failure a caller can put into an ``ActivationReport``."""

    status: StatusCode
    message: str
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def code(self) -> str:
        """Stable report code: the ``StatusCode`` member name."""
        return self.status.name


class ExtensionFailure(ValidationError):
    """A single package operation stopped before it changed shared state."""

    recoverable = False
    fatal = False

    def as_error(self) -> OperationError:
        return OperationError(self.status, self.message, dict(self.params))


def operation_error(status: StatusCode, *, msg: str | None = None, **kwargs: Any) -> OperationError:
    """Build a report error from a ``StatusCode`` template and kwargs."""
    return ExtensionFailure(status, msg=msg, **kwargs).as_error()
