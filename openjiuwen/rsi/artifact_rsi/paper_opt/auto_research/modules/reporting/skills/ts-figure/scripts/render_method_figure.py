#!/usr/bin/env python
"""CLI for the ts-figure skill's matplotlib fallback: render a validated
MethodFigureSpec (see reporting/schemas.py) as a box-and-arrow pipeline
diagram, run by the reporting agent via its own shell tool when the
Draw.io path isn't available (no ``drawio`` binary on PATH).

Usage: python render_method_figure.py <spec.json> <output_basepath>
  <output_basepath> gets .svg/.pdf/.png appended. Prints JSON:
  {"paths": {...}, "width_mm": ..., "height_mm": ..., "exceeds_double_column": bool}

Physical box/font sizes are fixed in mm, not data units stretched to fit an
assumed canvas -- an earlier prototype of this renderer sized boxes in
"data units" against a fixed 85-178mm figure width, which silently shrank
text below the 7pt academic-style.yaml floor as node count grew (verified:
5 nodes read fine, but the same wrap width at 7 nodes would cram into a
much smaller physical box). Here the canvas grows with node count instead
of the text shrinking; ``exceeds_double_column`` tells the caller when the
result no longer fits a normal double-column figure width, so that's a
signal to shorten labels or trim nodes, not something to silently absorb.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

MM_TO_IN = 1 / 25.4

PALETTE = {
    "neutral": "#64748B",
    "accent": "#2563EB",
    "secondary": "#7C3AED",
    "warning": "#B45309",
    "success": "#15803D",
    "danger": "#B91C1C",
    "ink": "#172033",
}

# Tuned empirically against a real 5-node case (see the design doc for this
# feature) at fontsize 6.5/5/4.8pt bold/regular/regular -- box_w_mm here is
# what a 1.5 "data unit" box came out to at that case's 178mm/~7.8-unit
# scale, so reusing these constants at a fixed physical size preserves the
# same legibility instead of accidentally shrinking it at other node counts.
BOX_W_MM = 34.0
BOX_H_MM = 24.0
GAP_MM = 14.0
MARGIN_MM = 8.0
LABEL_WRAP_CHARS = 15
SUBTITLE_WRAP_CHARS = 20
EDGE_LABEL_WRAP_CHARS = 16
SELF_LOOP_CLEARANCE_MM = 16.0
EDGE_LABEL_CLEARANCE_MM = 14.0
DOUBLE_COLUMN_MAX_MM = 178.0


def wrap(text: str, width: int) -> str:
    return "\n".join(textwrap.wrap(text, width=width)) if text else ""


def tone_color(tone: str) -> str:
    return PALETTE.get(tone, PALETTE["neutral"])


def render(spec: dict, output_base: Path) -> dict:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    nodes = spec["nodes"]
    edges = spec["edges"]
    n = len(nodes)

    box_w = BOX_W_MM * MM_TO_IN
    box_h = BOX_H_MM * MM_TO_IN
    gap = GAP_MM * MM_TO_IN
    margin = MARGIN_MM * MM_TO_IN

    x_positions = [margin + i * (box_w + gap) for i in range(n)]
    id_to_x = {node["id"]: x_positions[i] + box_w / 2 for i, node in enumerate(nodes)}
    y_center = 0.0

    node_index = {node["id"]: i for i, node in enumerate(nodes)}

    # An edge is only safe to draw as a single straight line at y_center if
    # it connects immediately-adjacent, left-to-right boxes -- anything
    # else (backward, or skipping over an intervening node) would cut
    # straight through every box in between. Confirmed the hard way: a
    # 7-node spec with one back-edge drew a dashed line directly across
    # five node titles before this split existed.
    self_loops: dict[str, dict] = {}
    adjacent_edges = []
    long_range_edges = []
    for edge in edges:
        if edge["from"] == edge["to"]:
            self_loops.setdefault(edge["from"], edge)
            continue
        if edge["from"] not in node_index or edge["to"] not in node_index:
            continue
        if node_index[edge["to"]] - node_index[edge["from"]] == 1:
            adjacent_edges.append(edge)
        else:
            long_range_edges.append(edge)

    fig_w_in = x_positions[-1] + box_w + margin
    top_extra = (SELF_LOOP_CLEARANCE_MM * MM_TO_IN) if self_loops else 0.06
    has_bottom_labels = any(e.get("label") for e in adjacent_edges)
    lane_gap_in = 0.16
    lane_y = -(box_h / 2 + (EDGE_LABEL_CLEARANCE_MM * MM_TO_IN if has_bottom_labels else 0.10))
    if long_range_edges:
        # Stack one lane per long-range edge below whatever adjacent-edge
        # labels already need, furthest-reaching edge closest to the boxes
        # so lanes nest without crossing.
        long_range_edges.sort(key=lambda e: abs(node_index[e["to"]] - node_index[e["from"]]))
        lane_ys = [lane_y - lane_gap_in * (i + 1) for i in range(len(long_range_edges))]
        bottom_extra = (lane_y - lane_ys[-1]) + lane_gap_in + 0.10
    else:
        lane_ys = []
        bottom_extra = (EDGE_LABEL_CLEARANCE_MM * MM_TO_IN) if has_bottom_labels else 0.06
    fig_h_in = box_h + top_extra + bottom_extra + 2 * (margin * 0.5)

    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "DejaVu Sans"],
            "svg.fonttype": "none",
        }
    )
    fig, ax = plt.subplots(figsize=(fig_w_in, fig_h_in))

    for i, node in enumerate(nodes):
        x = x_positions[i]
        color = tone_color(node.get("tone", "neutral"))
        is_accent = node.get("tone") == "accent"
        fill_alpha = 0.32 if is_accent else 0.12
        border_lw = 2.4 if is_accent else 1.2
        ax.add_patch(
            FancyBboxPatch(
                (x, y_center - box_h / 2), box_w, box_h,
                boxstyle="round,pad=0.02,rounding_size=0.06",
                linewidth=0, edgecolor="none", facecolor=color, alpha=fill_alpha,
            )
        )
        ax.add_patch(
            FancyBboxPatch(
                (x, y_center - box_h / 2), box_w, box_h,
                boxstyle="round,pad=0.02,rounding_size=0.06",
                linewidth=border_lw, edgecolor=color, facecolor="none",
            )
        )
        ax.text(
            x + box_w / 2, y_center + box_h * 0.20, wrap(node["label"], LABEL_WRAP_CHARS),
            ha="center", va="center", fontsize=6.5, fontweight="bold",
            color=PALETTE["ink"], linespacing=1.3,
        )
        subtitle = node.get("subtitle") or ""
        if subtitle:
            ax.text(
                x + box_w / 2, y_center - box_h * 0.28, wrap(subtitle, SUBTITLE_WRAP_CHARS),
                ha="center", va="center", fontsize=5, color=PALETTE["ink"], alpha=0.75,
                linespacing=1.3,
            )
        badge = node.get("badge") or ""
        if badge:
            ax.text(
                x + box_w - box_w * 0.05, y_center + box_h / 2 - box_h * 0.06, badge,
                ha="right", va="top", fontsize=5, fontweight="bold", color="white",
                bbox={"boxstyle": "round,pad=0.2", "facecolor": color, "edgecolor": "none"},
            )
        loop_edge = self_loops.get(node["id"])
        if loop_edge:
            top_y = y_center + box_h / 2
            ax.add_patch(
                FancyArrowPatch(
                    (x + box_w * 0.32, top_y), (x + box_w * 0.68, top_y),
                    connectionstyle="arc3,rad=-1.1",
                    arrowstyle="-|>", mutation_scale=7, linewidth=0.9,
                    linestyle="dashed", color=PALETTE["danger"],
                )
            )
            ax.text(
                x + box_w / 2, top_y + SELF_LOOP_CLEARANCE_MM * MM_TO_IN * 0.85,
                wrap(loop_edge.get("label") or "retry", 14),
                ha="center", va="bottom", fontsize=4.8, color=PALETTE["danger"], linespacing=1.2,
            )

    for edge in adjacent_edges:
        x0 = id_to_x[edge["from"]] + box_w / 2
        x1 = id_to_x[edge["to"]] - box_w / 2
        style = "dashed" if edge.get("style") == "dashed" else "solid"
        color = PALETTE["danger"] if edge.get("style") == "dashed" else PALETTE["ink"]
        ax.add_patch(
            FancyArrowPatch(
                (x0, y_center), (x1, y_center),
                arrowstyle="-|>", mutation_scale=9, linewidth=1.0,
                linestyle=style, color=color,
            )
        )
        label = edge.get("label") or ""
        if label:
            ax.text(
                (x0 + x1) / 2, y_center - box_h / 2 - EDGE_LABEL_CLEARANCE_MM * MM_TO_IN * 0.3,
                wrap(label, EDGE_LABEL_WRAP_CHARS),
                ha="center", va="top", fontsize=4.8, color=color, linespacing=1.2,
            )

    # Long-range edges (backward, or forward but skipping a node) route
    # through a dedicated lane below the boxes instead of a straight line
    # at y_center, which would cut across every node in between.
    for edge, lane_y in zip(long_range_edges, lane_ys):
        x_from = id_to_x[edge["from"]]
        x_to = id_to_x[edge["to"]]
        style = "dashed" if edge.get("style") == "dashed" else "solid"
        color = PALETTE["danger"] if edge.get("style") == "dashed" else PALETTE["ink"]
        box_bottom = y_center - box_h / 2
        ax.plot([x_from, x_from], [box_bottom, lane_y], linestyle=style, linewidth=1.0, color=color)
        ax.plot([x_from, x_to], [lane_y, lane_y], linestyle=style, linewidth=1.0, color=color)
        ax.add_patch(
            FancyArrowPatch(
                (x_to, lane_y), (x_to, box_bottom),
                arrowstyle="-|>", mutation_scale=9, linewidth=1.0,
                linestyle=style, color=color,
            )
        )
        label = edge.get("label") or ""
        if label:
            ax.text(
                (x_from + x_to) / 2, lane_y - 0.03,
                wrap(label, EDGE_LABEL_WRAP_CHARS),
                ha="center", va="top", fontsize=4.8, color=color, linespacing=1.2,
            )

    ax.set_xlim(0, fig_w_in)
    ax.set_ylim(-(box_h / 2 + bottom_extra), box_h / 2 + top_extra)
    ax.axis("off")

    output_base.parent.mkdir(parents=True, exist_ok=True)
    paths = {}
    for ext in ("svg", "pdf", "png"):
        path = output_base.with_suffix(f".{ext}")
        fig.savefig(path, dpi=300 if ext == "png" else None)
        paths[ext] = str(path)
    plt.close(fig)

    width_mm = fig_w_in / MM_TO_IN
    height_mm = fig_h_in / MM_TO_IN
    return {
        "paths": paths,
        "width_mm": round(width_mm, 1),
        "height_mm": round(height_mm, 1),
        "exceeds_double_column": width_mm > DOUBLE_COLUMN_MAX_MM,
    }


def main() -> None:
    if len(sys.argv) != 3:
        print(json.dumps({"error": "usage: render_method_figure.py <spec.json> <output_basepath>"}))
        raise SystemExit(1)
    spec_path = Path(sys.argv[1])
    output_base = Path(sys.argv[2])
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"error": f"cannot read spec: {exc}"}))
        raise SystemExit(1)
    if not spec.get("nodes") or not spec.get("edges"):
        print(json.dumps({"error": "spec has no nodes/edges -- refusing to render an empty figure"}))
        raise SystemExit(1)
    result = render(spec, output_base)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
