"""Deterministic figure generation — see docs/paper_writing_design.md §4.

Matplotlib only, no raster-to-vector engine: its PDF backend already emits
genuine vector PDF for the numeric data this pipeline actually produces
(``result.variants[*].metrics``). The original ``spark-to-paper-skills``
repo's PaperBanana+ figure machinery targets schematic/diagram figures with
no underlying numeric data — this pipeline never produces those, so that
whole dependency chain (local vision weights, LibreOffice, vector-audit)
doesn't apply here. See the design doc's "Future upgrades" for when a
diagram engine would actually become relevant.
"""

from __future__ import annotations

from pathlib import Path

from openjiuwen.rsi.artifact_rsi.paper_opt.auto_research.modules.experiment_execution.schemas import ExperimentResult


def numeric_metric_names(result: ExperimentResult) -> list[str]:
    names: list[str] = []
    for variant in result.variants:
        for name, value in variant.metrics.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if name not in names:
                names.append(name)
    return names


def build_results_figure(result: ExperimentResult, output_path: Path) -> Path | None:
    """Grouped bar chart of every numeric metric across every variant.
    Returns ``None`` (writes nothing) if there's no numeric data to plot —
    never fabricates a chart from missing data."""
    metric_names = numeric_metric_names(result)
    if not metric_names or not result.variants:
        return None

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    variants = result.variants
    x = np.arange(len(metric_names))
    width = 0.8 / max(len(variants), 1)

    fig, ax = plt.subplots(figsize=(max(4.0, len(metric_names) * 1.5), 4.0))
    for i, variant in enumerate(variants):
        values = []
        for name in metric_names:
            value = variant.metrics.get(name)
            is_numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            values.append(value if is_numeric else 0)
        ax.bar(x + i * width, values, width, label=variant.name)

    ax.set_xticks(x + width * (len(variants) - 1) / 2)
    ax.set_xticklabels(metric_names, rotation=20, ha="right")
    ax.set_ylabel("value")
    ax.set_title("Results by variant")
    ax.legend()
    fig.tight_layout()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, format="pdf")
    plt.close(fig)
    return output_path
