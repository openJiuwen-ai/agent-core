"""Typed native boundary outcomes; never infer replay safety from error strings."""


class InputNotStarted(RuntimeError):
    """Freshness changed at the initial guard, before any action side effect."""
