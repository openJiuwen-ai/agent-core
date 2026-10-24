"""Compatibility access to the single maintained infrastructure.storage module."""
import sys
from importlib import import_module

sys.modules[__name__] = import_module(".infrastructure.storage", __package__)
