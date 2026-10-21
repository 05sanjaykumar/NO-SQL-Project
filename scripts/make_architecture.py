"""
Writes docs/paper/figures/architecture.drawio (the editable source, open it in
draw.io / diagrams.net) and renders the same cells to architecture.pdf/.png with
matplotlib, so the paper figure can be rebuilt without draw.io installed.

With the draw.io desktop app installed, an exact export is:
    drawio -x -f pdf -o docs/paper/figures/architecture.pdf docs/paper/figures/architecture.drawio

    python scripts/make_architecture.py
"""

from __future__ import annotations

import html
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import itertools

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).resolve().parents[1] / "docs" / "paper" / "figures"
W, H = 1120, 560

# Groups (dashed containers): id, label, x, y, w, h, fill
GROUPS = [
    ("g_sim", "Simulator (seeded)", 10, 40, 250, 470, "#f6f8fa"),
    ("g_eng", "Streaming detection engine", 290, 40, 560, 470, "#eef4ff"),
    ("g_out", "Outputs", 880, 40, 230, 470, "#f6f8fa"),
]
# Boxes: id, label, x, y, w, h, fill
BOXES = [
    ("scen", "Scenario YAML\n(hubs, rings, evasion)", 30, 80, 210, 60, "#ffffff"),
    ("gen", "Event generator\nusers, links, transactions", 30, 175, 210, 70, "#ffffff"),
    ("att", "Adaptive\nattacker", 30, 290, 100, 70, "#fff4e5"),
    ("led", "Ground-truth\nledger\n(hidden)", 140, 290, 100, 70, "#ffe9e9"),
    ("batch", "Micro-batcher\n(50 events)", 310, 80, 150, 55, "#ffffff"),
    ("store", "Graph store\nNeo4j | in-memory", 310, 170, 150, 60, "#ffffff"),
    ("dirty", "Dirty region\nk-hop, fan-out caps", 310, 265, 150, 60, "#ffffff"),
    ("view", "Windowed view\n+ global degrees", 310, 360, 150, 55, "#ffffff"),
    (
        "det",
        "Incremental detectors\nhub-aware shared attr.\ntemporal cycle, smurfing",
        490,
        80,
        170,
        85,
        "#dbe9ff",
    ),
    ("ens", "Ensemble + ring tracker\nmerge, hysteresis", 490, 200, 170, 60, "#dbe9ff"),
    ("full", "Full-graph (timer)\nstatic Cypher, community", 490, 320, 170, 60, "#ffffff"),
    (
        "score",
        "Scoring stage\ncalibrated LR, alert\npolicy, explanations",
        680,
        80,
        150,
        85,
        "#dbe9ff",
    ),
    ("warn", "Early warning\n(pre-activation)", 680, 380, 150, 55, "#ffffff"),
    ("dash", "Live dashboard\nFastAPI WS + React", 895, 80, 200, 60, "#ffffff"),
    ("audit", "Run log, hash-chained\naudit trail, replay", 895, 190, 200, 60, "#ffffff"),
    ("eval", "Evaluation\nflags vs ledger", 895, 300, 200, 60, "#ffffff"),
    ("exp", "Experiments: results.csv\n-> tables, figures", 895, 410, 200, 60, "#ffffff"),
]
# Edges: source, target, label, dashed, waypoints
EDGES = [
    ("scen", "gen", "", False, []),
    ("gen", "att", "", False, []),
    ("gen", "led", "labels", True, []),
    ("gen", "batch", "events", False, []),
    ("batch", "store", "", False, []),
    ("store", "dirty", "", False, []),
    ("dirty", "view", "", False, []),
    ("view", "det", "", False, [(475, 387), (475, 122)]),
    ("store", "full", "", False, []),
    ("det", "ens", "", False, []),
    ("full", "ens", "", False, []),
    ("ens", "score", "", False, []),
    ("ens", "warn", "", False, []),
    ("score", "dash", "alerts", False, []),
    ("score", "audit", "", False, []),
    ("score", "att", "flags (closed loop)", True, [(865, 150), (865, 490), (80, 490)]),
    ("led", "eval", "", True, [(190, 530), (1105, 530), (1105, 330)]),
    ("ens", "eval", "", False, []),
    ("eval", "exp", "", False, []),
]


def _box(bid: str) -> tuple[float, float, float, float]:
    for b in BOXES:
        if b[0] == bid:
            return b[2], b[3], b[4], b[5]
    raise KeyError(bid)


def write_drawio(path: Path) -> None:
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    for gid, label, x, y, w, h, fill in GROUPS:
        cells.append(
            f'<mxCell id="{gid}" value="{html.escape(label)}" style="rounded=1;dashed=1;'
            f'fillColor={fill};strokeColor=#8c959f;verticalAlign=top;fontStyle=1;arcSize=4;" '
            f'vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
        )
    for bid, label, x, y, w, h, fill in BOXES:
        value = html.escape(label).replace("\n", "&lt;br&gt;")
        cells.append(
            f'<mxCell id="{bid}" value="{value}" style="rounded=1;whiteSpace=wrap;html=1;'
            f'fillColor={fill};strokeColor=#57606a;fontSize=11;" vertex="1" parent="1">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
        )
    for i, (s, t, label, dashed, pts) in enumerate(EDGES):
        geo = (
            '<mxGeometry relative="1" as="geometry"><Array as="points">'
            + "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in pts)
            + "</Array></mxGeometry>"
            if pts
            else '<mxGeometry relative="1" as="geometry"/>'
        )
        cells.append(
            f'<mxCell id="e{i}" value="{html.escape(label)}" style="endArrow=block;html=1;'
            f'{"dashed=1;" if dashed else ""}{"edgeStyle=orthogonalEdgeStyle;" if pts else ""}strokeColor=#24292f;fontSize=10;" edge="1" parent="1" '
            f'source="{s}" target="{t}">{geo}</mxCell>'
        )
    xml = (
        '<mxfile host="fraudsim"><diagram name="architecture">'
        f'<mxGraphModel dx="{W}" dy="{H}" grid="1" gridSize="10" pageWidth="{W}" pageHeight="{H}">'
        "<root>" + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n"
    )
    path.write_text(xml, encoding="utf-8")


def _anchor(
    b: tuple[float, float, float, float], toward: tuple[float, float]
) -> tuple[float, float]:
    """Point on the box border in the direction of `toward`."""
    x, y, w, h = b
    cx, cy = x + w / 2, y + h / 2
    dx, dy = toward[0] - cx, toward[1] - cy
    if dx == 0 and dy == 0:
        return cx, cy
    sx = (w / 2) / abs(dx) if dx else float("inf")
    sy = (h / 2) / abs(dy) if dy else float("inf")
    s = min(sx, sy)
    return cx + dx * s, cy + dy * s


def render(paths: list[Path]) -> None:
    fig, ax = plt.subplots(figsize=(7.16, 7.16 * H / W))
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.axis("off")
    for _, label, x, y, w, h, fill in GROUPS:
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                w,
                h,
                boxstyle="round,pad=0,rounding_size=8",
                fc=fill,
                ec="#8c959f",
                ls="--",
                lw=0.8,
            )
        )
        ax.text(x + w / 2, y + 14, label, ha="center", va="center", fontsize=6.5, weight="bold")
    for _, label, x, y, w, h, fill in BOXES:
        ax.add_patch(
            FancyBboxPatch(
                (x, y), w, h, boxstyle="round,pad=0,rounding_size=6", fc=fill, ec="#57606a", lw=0.7
            )
        )
        ax.text(
            x + w / 2, y + h / 2, label, ha="center", va="center", fontsize=4.8, linespacing=1.2
        )
    for s, t, label, dashed, pts in EDGES:
        bs, bt = _box(s), _box(t)
        cs = (bs[0] + bs[2] / 2, bs[1] + bs[3] / 2)
        ct = (bt[0] + bt[2] / 2, bt[1] + bt[3] / 2)
        line = [_anchor(bs, pts[0] if pts else ct), *pts, _anchor(bt, pts[-1] if pts else cs)]
        ls = "--" if dashed else "-"
        for a0, a1 in itertools.pairwise(line):
            ax.plot([a0[0], a1[0]], [a0[1], a1[1]], color="#24292f", lw=0.7, ls=ls)
        ax.add_patch(
            FancyArrowPatch(
                line[-2],
                line[-1],
                arrowstyle="-|>",
                mutation_scale=7,
                lw=0.7,
                ls=ls,
                color="#24292f",
                shrinkA=0,
                shrinkB=0,
            )
        )
        if label:
            # Label the longest segment.
            seg = max(
                itertools.pairwise(line),
                key=lambda q: abs(q[1][0] - q[0][0]) + abs(q[1][1] - q[0][1]),
            )
            ax.text(
                (seg[0][0] + seg[1][0]) / 2,
                (seg[0][1] + seg[1][1]) / 2,
                label,
                ha="center",
                va="center",
                fontsize=5,
                style="italic",
                bbox={"fc": "white", "ec": "none", "pad": 0.6},
            )
    for p in paths:
        fig.savefig(p, bbox_inches="tight", dpi=300)
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write_drawio(OUT / "architecture.drawio")
    render([OUT / "architecture.pdf", OUT / "architecture.png"])
    print(f"wrote {OUT / 'architecture.drawio'} (+ .pdf, .png)")


if __name__ == "__main__":
    main()
