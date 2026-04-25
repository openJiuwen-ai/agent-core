"""Extension sandbox tests.

Import extension providers here so tests in this package run with the same
registration state as users who import the extension sandbox package.
"""

from openjiuwen.extensions.sys_operation.sandbox.providers import aio as _aio  # noqa: F401
from openjiuwen.extensions.sys_operation.sandbox.providers import jiuwenbox as _jiuwenbox  # noqa: F401
