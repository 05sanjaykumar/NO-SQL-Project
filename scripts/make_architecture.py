"""
Architecture figure for the paper, in the style of IEEE graph-fraud papers:
white background, Times labels, small coloured node glyphs, mini graph sketches,
stacked feature vectors, dashed stage separators and a legend row.

One list of primitives is written twice, so the two outputs cannot drift apart:
  docs/paper/figures/architecture.drawio   editable source (draw.io / diagrams.net)
  docs/paper/figures/architecture.pdf/.png rendered with matplotlib

With the draw.io desktop app, an exact export of the source is:
    drawio -x -f pdf -o docs/paper/figures/architecture.pdf docs/paper/figures/architecture.drawio

    python scripts/make_architecture.py
"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Rectangle

OUT = Path(__file__).resolve().parents[1] / "docs" / "paper" / "figures"
W, H = 1200, 540
FONT = "Times New Roman"

BENIGN = "#2e9e4f"  # green, as in the reference papers
FRAUD = "#ef7d22"  # orange
NEUTRAL = "#9aa0a6"  # grey: attributes / unknown
HUB = "#6b7280"
BLUE = "#8db4e2"
LINE = "#1f2328"
PANEL = "#5b8bd0"


@dataclass
class Shape:
    kind: str  # rect | ellipse | text | edge
    x: float = 0
    y: float = 0
    w: float = 0
    h: float = 0
    label: str = ""
    fill: str = "none"
    stroke: str = LINE
    dashed: bool = False
    rounded: bool = True
    size: float = 10
    bold: bool = False
    italic: bool = False
    align: str = "center"
    lw: float = 1.0
    arrow: bool = True
    points: list[tuple[float, float]] = field(default_factory=list)


S: list[Shape] = []


def rect(x, y, w, h, label="", fill="#ffffff", stroke=LINE, **kw):  # type: ignore[no-untyped-def]
    S.append(Shape("rect", x, y, w, h, label, fill, stroke, **kw))


def node(cx, cy, r, fill, label="", stroke="none", **kw):  # type: ignore[no-untyped-def]
    S.append(Shape("ellipse", cx - r, cy - r, 2 * r, 2 * r, label, fill, stroke, **kw))


def text(x, y, s, size=10, **kw):  # type: ignore[no-untyped-def]
    S.append(Shape("text", x, y, 0, 0, s, size=size, **kw))


def edge(*pts, dashed=False, arrow=True, lw=1.0, stroke=LINE):  # type: ignore[no-untyped-def]
    S.append(Shape("edge", points=list(pts), dashed=dashed, arrow=arrow, lw=lw, stroke=stroke))


def vector(x, y, colors, cell=9):  # type: ignore[no-untyped-def]
    """A stacked feature vector, as drawn in GNN papers."""
    for i, c in enumerate(colors):
        rect(x, y + i * cell, cell, cell, fill=c, stroke=LINE, rounded=False, lw=0.6)


# ── Top banner: closed loop ─────────────────────────────────────────────────
text(
    600,
    18,
    "Closed loop (feedback runs): alerts are returned to the attacker",
    10.5,
    bold=True,
)
edge((1118, 118), (1118, 32), (10, 32), (10, 326), (22, 326), dashed=True)

# ── Stage separators and titles ─────────────────────────────────────────────
for x in (232, 488, 708, 900):
    edge((x, 52), (x, 438), dashed=True, arrow=False, stroke="#9aa0a6", lw=0.8)
titles = [
    (116, "(a) Seeded simulator"),
    (360, "(b) Incremental region"),
    (598, "(c) Hub-aware weighting"),
    (804, "(d) Detect and track"),
    (1046, "(e) Score and explain"),
]
for x, t in titles:
    text(x, 62, t, 9.5, bold=True)

# ── (a) Simulator ───────────────────────────────────────────────────────────
rect(22, 80, 196, 118, "", fill="#ffffff", stroke=PANEL, lw=1.0)
text(120, 94, "scenario + seed", 9, italic=True)
pts = {
    "u1": (58, 130),
    "u2": (92, 118),
    "u3": (80, 160),
    "r1": (150, 124),
    "r2": (180, 150),
    "r3": (146, 172),
}
for a, b in (
    ("u1", "u2"),
    ("u2", "u3"),
    ("u1", "u3"),
    ("r1", "r2"),
    ("r2", "r3"),
    ("r1", "r3"),
    ("u2", "r1"),
):
    edge(pts[a], pts[b], arrow=False, lw=0.8)
for k, (x, y) in pts.items():
    node(x, y, 8, BENIGN if k.startswith("u") else FRAUD)
text(120, 190, "users, hubs, planted rings", 8.5)
# event stream
text(120, 214, "event stream", 9, italic=True)
for i in range(8):
    rect(
        36 + i * 18,
        224,
        12,
        22,
        fill=BLUE if i % 3 else "#c9d9ee",
        stroke=LINE,
        rounded=False,
        lw=0.6,
    )
edge((182, 235), (258, 235))
text(206, 214, "50 / batch", 8)
rect(
    22,
    300,
    196,
    52,
    "adaptive attacker",
    fill="#fff4e0",
    stroke="#d29922",
    size=9.5,
)
edge((120, 300), (120, 254), (60, 254), (60, 246))
rect(
    22,
    372,
    196,
    52,
    "ground-truth ledger\n(hidden)",
    fill="#fbe3d6",
    stroke=FRAUD,
    size=9.5,
)
edge((214, 198), (226, 198), (226, 398), (218, 398), dashed=True)
text(200, 362, "labels", 8.5, italic=True)

# ── (b) Graph store and dirty region ────────────────────────────────────────
rect(258, 80, 214, 228, "", fill="#ffffff", stroke=PANEL, lw=1.0)
text(365, 94, "graph store", 9, italic=True)
S.append(Shape("ellipse", 270, 116, 150, 150, "", "none", "#d1242f", dashed=True, lw=1.0))
hub = (350, 196)
node(*hub, 15, HUB, "hub", size=8.5, stroke="none")
for i in range(12):
    xx = 350 + 62 * math.cos(i * 0.52)
    yy = 196 + 62 * math.sin(i * 0.52)
    edge(hub, (xx, yy), arrow=False, lw=0.6, stroke="#9aa0a6")
    node(xx, yy, 5.5, BENIGN if i % 4 else FRAUD)
node(440, 140, 7, FRAUD, stroke=LINE)
node(452, 178, 7, FRAUD, stroke=LINE)
edge((440, 140), (452, 178), arrow=False, lw=1.0)
edge((452, 178), (408, 196), arrow=False, lw=1.0)
text(365, 274, "touched users + 2 hops", 8.5)
text(365, 294, "fan-out caps 250 / 20", 8.5)
edge((472, 196), (512, 196))

# ── (c) Hub-aware weighting ─────────────────────────────────────────────────
rect(512, 80, 172, 228, "", fill="#ffffff", stroke=PANEL, lw=1.0)
node(548, 130, 9, FRAUD)
node(648, 130, 9, FRAUD)
node(598, 112, 7, NEUTRAL, stroke=LINE)
edge((548, 130), (598, 112), arrow=False, lw=2.6)
edge((598, 112), (648, 130), arrow=False, lw=2.6)
text(598, 96, "device, d = 3: w = 0.72", 8.5)
node(548, 196, 9, BENIGN)
node(648, 196, 9, BENIGN)
node(598, 178, 9, HUB, stroke=LINE)
edge((548, 196), (598, 178), arrow=False, lw=0.7)
edge((598, 178), (648, 196), arrow=False, lw=0.7)
text(598, 216, "office IP, d = 50: w = 0.25", 8.5)
text(598, 244, "W = Σ h(d)·δ(t) + β·T", 9, italic=True)
text(598, 262, "h(d) = 1 / ln(1 + d)", 9, italic=True)
text(598, 284, "W ≥ θe, peel cores", 9)
edge((684, 196), (726, 140))

# ── (d) Detectors, ensemble and tracker ─────────────────────────────────────
rect(726, 82, 156, 30, "hub-aware", fill="#e3edf9", stroke=PANEL, size=9)
rect(726, 118, 156, 30, "money cycle", fill="#e3edf9", stroke=PANEL, size=9)
rect(726, 154, 156, 30, "smurfing", fill="#e3edf9", stroke=PANEL, size=9)
rect(
    726,
    196,
    156,
    34,
    "baselines on timer",
    fill="#ffffff",
    stroke="#9aa0a6",
    dashed=True,
    size=8.5,
)
node(804, 258, 8, "#ffffff", "+", stroke=LINE, size=12)
for y in (97, 133, 169):
    edge((882, y), (892, y), (892, 248), (812, 255), arrow=True, lw=0.8)
rect(
    726,
    284,
    156,
    54,
    "ring tracker\nconfirm 2 hits\ndrop 3 misses",
    fill="#ffffff",
    stroke=LINE,
    size=8.5,
)
edge((804, 266), (804, 284))
edge((804, 230), (804, 250), dashed=True)
edge((882, 311), (922, 311), (922, 150), (936, 150))

# ── (e) Scoring, explanations, outputs ──────────────────────────────────────
text(962, 92, "features", 9, italic=True)
vector(950, 100, [BLUE, BLUE, "#b6d7a8", "#b6d7a8", "#f9cb9c", "#f9cb9c", NEUTRAL, NEUTRAL])
vector(962, 100, ["#c9d9ee", BLUE, "#b6d7a8", "#d9ead3", "#f9cb9c", "#fce5cd", NEUTRAL, "#d0d4d9"])
edge((976, 136), (1000, 136))
rect(
    1000,
    112,
    90,
    48,
    "calibrated\nscorer",
    fill="#ffffff",
    stroke=LINE,
    size=8.5,
)
edge((1090, 136), (1110, 136))
rect(1110, 118, 16, 70, fill="#ffffff", stroke=LINE, rounded=False, lw=0.6)
rect(1110, 140, 16, 48, fill=FRAUD, stroke="none", rounded=False)
edge((1104, 150), (1132, 150), arrow=False, dashed=True, lw=0.8, stroke="#d1242f")
text(1158, 150, "θa", 9, italic=True)
text(1118, 204, "risk", 8.5)
rect(
    936,
    222,
    186,
    46,
    "explanation:\nevidence, counterfactual",
    fill="#ffffff",
    stroke=PANEL,
    size=8.5,
)
edge((1045, 160), (1045, 222))
rect(936, 300, 76, 40, "dashboard", fill="#ffffff", stroke=LINE, size=8.5)
rect(1020, 300, 76, 40, "audit log", fill="#ffffff", stroke=LINE, size=8.5)
rect(1104, 300, 80, 40, "evaluation", fill="#ffffff", stroke=LINE, size=8.5)
edge((974, 268), (974, 300))
edge((1058, 268), (1058, 300))
edge((1122, 245), (1144, 245), (1144, 300))
edge((120, 424), (120, 456), (1144, 456), (1144, 340), dashed=True)
text(560, 466, "labels reach only the evaluation", 8.5, italic=True)

# ── Legend ──────────────────────────────────────────────────────────────────
ly = 500
items = [
    (BENIGN, "benign user"),
    (FRAUD, "ring member"),
    (NEUTRAL, "device / card / IP"),
    (HUB, "busy hub"),
]
x = 60
for color, label in items:
    node(x, ly, 7, color, stroke=LINE if color == NEUTRAL else "none")
    text(x + 14, ly, label, 10, align="left")
    x += 180
edge((x, ly), (x + 34, ly))
text(x + 42, ly, "data flow", 10, align="left")
x += 150
edge((x, ly), (x + 34, ly), dashed=True)
text(x + 42, ly, "labels / feedback", 10, align="left")
x += 190
rect(x, ly - 8, 22, 16, fill="none", stroke="#d1242f", dashed=True)
text(x + 30, ly, "dirty region", 10, align="left")


# ── Writers ─────────────────────────────────────────────────────────────────
def _style_font(s: Shape) -> str:
    fs = 1 if s.bold else 0
    fs += 2 if s.italic else 0
    return f"fontFamily={FONT};fontSize={s.size};fontStyle={fs};"


def write_drawio(path: Path) -> None:
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    for i, s in enumerate(S):
        cid = f"s{i}"
        value = html.escape(s.label).replace("\n", "&lt;br&gt;")
        if s.kind in ("rect", "ellipse"):
            style = (
                ("ellipse;" if s.kind == "ellipse" else f"rounded={int(s.rounded)};arcSize=12;")
                + f"whiteSpace=wrap;html=1;fillColor={s.fill};strokeColor={s.stroke};"
                + f"strokeWidth={s.lw};{'dashed=1;' if s.dashed else ''}"
                + _style_font(s)
            )
            cells.append(
                f'<mxCell id="{cid}" value="{value}" style="{style}" vertex="1" parent="1">'
                f'<mxGeometry x="{s.x:.1f}" y="{s.y:.1f}" width="{s.w:.1f}" height="{s.h:.1f}" as="geometry"/></mxCell>'
            )
        elif s.kind == "text":
            align = {"center": "center", "left": "left"}[s.align]
            width = 360
            x0 = s.x - width / 2 if s.align == "center" else s.x
            cells.append(
                f'<mxCell id="{cid}" value="{value}" style="text;html=1;align={align};verticalAlign=middle;'
                f'{_style_font(s)}" vertex="1" parent="1">'
                f'<mxGeometry x="{x0:.1f}" y="{s.y - 9:.1f}" width="{width}" height="18" as="geometry"/></mxCell>'
            )
        else:
            (x0, y0), (x1, y1) = s.points[0], s.points[-1]
            mid = "".join(f'<mxPoint x="{x:.1f}" y="{y:.1f}"/>' for x, y in s.points[1:-1])
            style = (
                f"endArrow={'block;endFill=1' if s.arrow else 'none'};html=1;strokeColor={s.stroke};"
                f"strokeWidth={s.lw};{'dashed=1;' if s.dashed else ''}rounded=0;"
            )
            cells.append(
                f'<mxCell id="{cid}" style="{style}" edge="1" parent="1"><mxGeometry relative="1" as="geometry">'
                f'<mxPoint x="{x0:.1f}" y="{y0:.1f}" as="sourcePoint"/><mxPoint x="{x1:.1f}" y="{y1:.1f}" as="targetPoint"/>'
                + (f'<Array as="points">{mid}</Array>' if mid else "")
                + "</mxGeometry></mxCell>"
            )
    xml = (
        '<mxfile host="fraudsim"><diagram name="architecture">'
        f'<mxGraphModel dx="{W}" dy="{H}" grid="1" gridSize="10" pageWidth="{W}" pageHeight="{H}" background="#ffffff">'
        "<root>" + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n"
    )
    path.write_text(xml, encoding="utf-8")


def render(paths: list[Path]) -> None:
    plt.rcParams["font.family"] = FONT
    plt.rcParams["mathtext.fontset"] = "stix"
    scale = 7.16 / W  # inches per unit at full IEEE text width
    fig, ax = plt.subplots(figsize=(W * scale, H * scale))
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.set_aspect("equal")
    ax.axis("off")
    pt = 0.68  # font points per drawio point at this figure size
    for s in S:
        style = "italic" if s.italic else "normal"
        weight = "bold" if s.bold else "normal"
        if s.kind == "rect":
            fc = "none" if s.fill == "none" else s.fill
            ec = "none" if s.stroke == "none" else s.stroke
            if s.rounded:
                patch = FancyBboxPatch(
                    (s.x, s.y),
                    s.w,
                    s.h,
                    boxstyle="round,pad=0,rounding_size=6",
                    fc=fc,
                    ec=ec,
                    lw=s.lw * 0.7,
                    ls="--" if s.dashed else "-",
                )
            else:
                patch = Rectangle(
                    (s.x, s.y), s.w, s.h, fc=fc, ec=ec, lw=s.lw * 0.7, ls="--" if s.dashed else "-"
                )
            ax.add_patch(patch)
            if s.label:
                ax.text(
                    s.x + s.w / 2,
                    s.y + s.h / 2,
                    s.label,
                    ha="center",
                    va="center",
                    fontsize=s.size * pt,
                    linespacing=1.15,
                    style=style,
                    weight=weight,
                )
        elif s.kind == "ellipse":
            fc = "none" if s.fill == "none" else s.fill
            ec = "none" if s.stroke == "none" else s.stroke
            if abs(s.w - s.h) < 1e-6:
                ax.add_patch(
                    Circle(
                        (s.x + s.w / 2, s.y + s.h / 2),
                        s.w / 2,
                        fc=fc,
                        ec=ec,
                        lw=s.lw * 0.7,
                        ls="--" if s.dashed else "-",
                    )
                )
            if s.label:
                ax.text(
                    s.x + s.w / 2,
                    s.y + s.h / 2,
                    s.label,
                    ha="center",
                    va="center",
                    fontsize=s.size * pt,
                    color="white" if s.fill in (HUB,) else LINE,
                )
        elif s.kind == "text":
            ax.text(
                s.x,
                s.y,
                s.label,
                ha=s.align,
                va="center",
                fontsize=s.size * pt,
                style=style,
                weight=weight,
            )
        else:
            ls = "--" if s.dashed else "-"
            for a, b in pairwise(s.points[:-1]):
                ax.plot([a[0], b[0]], [a[1], b[1]], color=s.stroke, lw=s.lw * 0.7, ls=ls)
            a, b = s.points[-2], s.points[-1]
            ax.add_patch(
                FancyArrowPatch(
                    a,
                    b,
                    arrowstyle="-|>" if s.arrow else "-",
                    mutation_scale=7,
                    lw=s.lw * 0.7,
                    ls=ls,
                    color=s.stroke,
                    shrinkA=0,
                    shrinkB=0,
                )
            )
    for p in paths:
        fig.savefig(p, bbox_inches="tight", dpi=300, pad_inches=0.02)
    plt.close(fig)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write_drawio(OUT / "architecture.drawio")
    render([OUT / "architecture.pdf", OUT / "architecture.png"])
    print(f"wrote {OUT / 'architecture.drawio'} (+ .pdf, .png)")


if __name__ == "__main__":
    main()
