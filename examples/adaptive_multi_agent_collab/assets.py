"""Compatibility access to the single maintained datasets.assets module."""
import sys
from importlib import import_module

sys.modules[__name__] = import_module(".datasets.assets", __package__)
