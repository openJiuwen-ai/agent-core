"""Calculator service for mathematical operations."""

from typing import Optional


class Calculator:
    """A calculator class providing basic mathematical operations."""

    def __init__(self, precision: int = 10):
        self.precision = precision

    def _round_result(self, value: float) -> float:
        return round(value, self.precision)

    def add(self, a: float, b: float) -> float:
        """Add two numbers."""
        return self._round_result(float(a) + float(b))


_default_calculator: Optional[Calculator] = None


def get_default_calculator() -> Calculator:
    """Get the default calculator instance."""
    global _default_calculator
    if _default_calculator is None:
        _default_calculator = Calculator()
    return _default_calculator
