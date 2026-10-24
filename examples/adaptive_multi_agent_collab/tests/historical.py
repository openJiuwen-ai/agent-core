"""Resolve read-only historical fixtures without changing embedded evidence paths."""

from pathlib import Path


def historical_campaign_path(path: str | Path, *, root: Path | None = None) -> Path:
    root = root or Path(__file__).resolve().parents[1]
    source = Path(path)
    source = source if source.is_absolute() else root / source
    relative = source.relative_to(root)
    if source.is_file():
        return source
    archived = root / "archive/Pilot_1" / relative
    return archived if archived.is_file() else source
