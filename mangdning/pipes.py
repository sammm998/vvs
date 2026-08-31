"""Del B – rördetektering direkt ur PDF:ens vektor-ritkommandon.

Rörledningar är riktiga vektorlinjer med konsekvent linjebredd/färg och ska
INTE OCR-läsas. Här byggs ett histogram över linjebredder, rätt kluster
identifieras (dynamiskt eller via konfig), och segmenten kedjas ihop till
sammanhängande rörsträckor med union-find.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field

import fitz

from .config import Config
from .models import BBox, PipeChain, Point, Segment, dist

log = logging.getLogger(__name__)


@dataclass
class DrawingData:
    """Alla stroke-segment ur sidan, plus småsymboler (cirklar m.m.)."""

    segments: list[Segment] = field(default_factory=list)
    # småsymboler: (centrum, diameter) för slutna kurv-paths (vertikalsymboler)
    small_symbols: list[tuple[Point, float]] = field(default_factory=list)
    width_histogram: Counter = field(default_factory=Counter)
    width_color: dict[float, Counter] = field(default_factory=dict)


def _color_key(color) -> tuple[float, float, float] | None:
    if color is None:
        return None
    return tuple(round(c, 2) for c in color)


def extract_drawings(page: fitz.Page, cfg: Config) -> DrawingData:
    """Plocka ut alla linjesegment (med bredd/färg) och små slutna symboler."""
    data = DrawingData()
    for d in page.get_drawings():
        width = round(d.get("width") or 0.0, 2)
        color = _color_key(d.get("color"))
        rect = d.get("rect")
        items = d.get("items") or []

        has_line = False
        curve_count = 0
        for item in items:
            kind = item[0]
            if kind == "l":
                p1 = (item[1].x, item[1].y)
                p2 = (item[2].x, item[2].y)
                if p1 != p2:
                    data.segments.append(Segment(p1, p2, width, color))
                    has_line = True
            elif kind == "re":
                r = item[1]
                corners = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                for a, b in zip(corners, corners[1:] + corners[:1]):
                    data.segments.append(Segment(a, b, width, color))
                has_line = True
            elif kind == "c":
                curve_count += 1
            elif kind == "qu":
                q = item[1]
                pts = [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y),
                       (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
                for a, b in zip(pts, pts[1:] + pts[:1]):
                    data.segments.append(Segment(a, b, width, color))
                has_line = True

        # Små slutna kurv-paths = kandidater till vertikalsymboler (⊙/pil)
        if curve_count and not has_line and rect is not None:
            diameter = max(rect.width, rect.height)
            if 0.0 < diameter <= cfg.symbol_max_diameter_pt:
                center = ((rect.x0 + rect.x1) / 2.0, (rect.y0 + rect.y1) / 2.0)
                data.small_symbols.append((center, diameter))

    for seg in data.segments:
        data.width_histogram[seg.width] += 1
        data.width_color.setdefault(seg.width, Counter())[seg.color] += 1
    return data


def format_histogram(data: DrawingData) -> str:
    """Läsbar kalibreringsvy över bredd/färg-kluster."""
    lines = ["Linjebredd-histogram (bredd pt : antal segment : vanligaste färg):"]
    for width, count in data.width_histogram.most_common():
        color = data.width_color[width].most_common(1)[0][0]
        lines.append(f"  {width:>6.2f} : {count:>6d} : {color}")
    return "\n".join(lines)


def select_pipe_cluster(data: DrawingData, cfg: Config
                        ) -> tuple[float, tuple[float, float, float] | None]:
    """Identifiera vilket bredd/färg-kluster som är rörlinjer.

    Inte hårdkodat: om cfg.pipe_width är satt används den, annars väljs
    dynamiskt. Heuristik: det vanligaste klustret är typiskt tunna streck
    (textkonturer/måttlinjer); det näst vanligaste distinkta klustret som är
    tydligt bredare brukar vara rörlinjerna. Loggas alltid så användaren kan
    verifiera/kalibrera med --calibrate eller --pipe-width.
    """
    if cfg.pipe_width is not None:
        color = cfg.pipe_color
        if color is None and data.width_color:
            best = None
            for width, colors in data.width_color.items():
                if abs(width - cfg.pipe_width) <= cfg.pipe_width * cfg.pipe_width_tol:
                    for c, n in colors.items():
                        if best is None or n > best[1]:
                            best = (c, n)
            color = best[0] if best else None
        log.info("Rörkluster (konfigurerat): bredd=%.2f pt, färg=%s",
                 cfg.pipe_width, color)
        return cfg.pipe_width, color

    common = data.width_histogram.most_common()
    if not common:
        raise ValueError("Inga stroke-segment i PDF:en – går inte att "
                         "identifiera rörlinjer.")
    if len(common) == 1:
        width = common[0][0]
    else:
        widest_common, _ = common[0]
        # Näst vanligaste distinkta klustret (tydligt skild bredd)
        candidates = [(w, n) for w, n in common[1:]
                      if abs(w - widest_common) > 0.05 and n >= 10]
        if candidates:
            # Föredra kluster som är bredare än det vanligaste (rörlinjer är
            # typiskt tjockare än text-streck/måttlinjer)
            wider = [(w, n) for w, n in candidates if w > widest_common]
            width = (wider or candidates)[0][0]
        else:
            width = widest_common
    color = data.width_color[width].most_common(1)[0][0]
    log.info("Rörkluster (auto): bredd=%.2f pt, färg=%s "
             "(verifiera med --calibrate, överstyr med --pipe-width)",
             width, color)
    return width, color


def _color_close(a, b, tol: float) -> bool:
    if a is None or b is None:
        return a == b
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def filter_pipe_segments(data: DrawingData, width: float,
                         color: tuple[float, float, float] | None,
                         cfg: Config) -> list[Segment]:
    """Behåll segment i rätt bredd/färg-kluster, utanför exkluderingszoner."""
    zones = cfg.exclude_bboxes()
    out = []
    for seg in data.segments:
        if abs(seg.width - width) > width * cfg.pipe_width_tol:
            continue
        if color is not None and not _color_close(seg.color, color, cfg.color_tol):
            continue
        mid = ((seg.p1[0] + seg.p2[0]) / 2.0, (seg.p1[1] + seg.p2[1]) / 2.0)
        if any(z.contains(mid) for z in zones):
            continue
        out.append(seg)
    return out


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, a: int) -> int:
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def union(self, a: int, b: int):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def chain_segments(segments: list[Segment], tol_pt: float) -> list[list[Segment]]:
    """Del B punkt 3: kedja ihop segment vars ändpunkter ligger inom tol_pt.

    Rörlinjer ritas ofta som många korta raka segment i rad; union-find på
    ändpunkterna slår ihop dem till sammanhängande rörsträckor.
    """
    if not segments:
        return []
    uf = _UnionFind(len(segments))
    cell = max(tol_pt, 0.5)
    grid: dict[tuple[int, int], list[tuple[int, Point]]] = {}
    for i, seg in enumerate(segments):
        for p in (seg.p1, seg.p2):
            grid.setdefault((int(p[0] // cell), int(p[1] // cell)), []).append((i, p))
    for i, seg in enumerate(segments):
        for p in (seg.p1, seg.p2):
            gx, gy = int(p[0] // cell), int(p[1] // cell)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j, q in grid.get((gx + dx, gy + dy), []):
                        if j != i and dist(p, q) <= tol_pt:
                            uf.union(i, j)
    groups: dict[int, list[Segment]] = {}
    for i, seg in enumerate(segments):
        groups.setdefault(uf.find(i), []).append(seg)
    return list(groups.values())


def _chain_endpoints(segments: list[Segment], tol_pt: float) -> list[Point]:
    """Grad-1-noder i kedjan (fria rörändar) – används för vertikalsymboler."""
    counts: list[tuple[Point, int]] = []
    for seg in segments:
        for p in (seg.p1, seg.p2):
            for i, (q, n) in enumerate(counts):
                if dist(p, q) <= tol_pt:
                    counts[i] = (q, n + 1)
                    break
            else:
                counts.append((p, 1))
    return [p for p, n in counts if n == 1]


def build_chains(segments: list[Segment], cfg: Config) -> list[PipeChain]:
    """Bygg PipeChain-objekt med punktlista, längd och bbox (Del B punkt 4)."""
    chains: list[PipeChain] = []
    for group in chain_segments(segments, cfg.chain_tol_pt):
        length = sum(s.length for s in group)
        if length < cfg.min_chain_len_pt:
            continue
        xs = [c for s in group for c in (s.p1[0], s.p2[0])]
        ys = [c for s in group for c in (s.p1[1], s.p2[1])]
        points = []
        for s in group:
            points.extend([s.p1, s.p2])
        chain = PipeChain(
            id=len(chains), segments=group, points=points,
            length_pt=length,
            bbox=BBox(min(xs), min(ys), max(xs), max(ys)),
            endpoints=_chain_endpoints(group, cfg.chain_tol_pt),
        )
        chains.append(chain)
    return chains


def flag_frame_chains(chains: list[PipeChain], page_rect: fitz.Rect,
                      cfg: Config) -> None:
    """Del B punkt 2: mycket långa, helt raka ensamma linjer utan
    avgreningar är ofta rutnät/ram/titelblock – inte rörnät."""
    for chain in chains:
        if len(chain.segments) <= 2:
            straight = sum(s.length for s in chain.segments)
            axis = all(s.is_axis_aligned() for s in chain.segments)
            if axis and straight >= cfg.frame_len_pt:
                chain.excluded = True
                chain.excluded_reason = "ram/rutnät (lång rak ensam linje)"
        # kedjor som spänner nästan hela sidan = ram
        if (chain.bbox.width > page_rect.width * 0.9
                and chain.bbox.height > page_rect.height * 0.9):
            chain.excluded = True
            chain.excluded_reason = "sidram"


def assign_vertical_symbols(chains: list[PipeChain],
                            symbols: list[tuple[Point, float]],
                            cfg: Config) -> int:
    """Del D punkt 6: räkna vertikala rörfall-symboler (små ⊙/pil-symboler)
    vid rörändpunkter. Returnerar antal tilldelade symboler."""
    assigned = 0
    active = [c for c in chains if not c.excluded]
    for center, _diameter in symbols:
        best_chain = None
        best_d = None
        for chain in active:
            for p in (chain.endpoints or chain.points):
                d = dist(center, p)
                if best_d is None or d < best_d:
                    best_d, best_chain = d, chain
        if best_chain is not None and best_d is not None \
                and best_d <= cfg.symbol_pipe_tol_pt:
            best_chain.vertical_symbols += 1
            assigned += 1
    return assigned


def detect_pipes(page: fitz.Page, cfg: Config
                 ) -> tuple[list[PipeChain], DrawingData, float,
                            tuple[float, float, float] | None]:
    """Hela Del B: extrahera, välj kluster, filtrera, kedja, flagga ram."""
    data = extract_drawings(page, cfg)
    log.info("Vektordata: %d segment, %d småsymboler",
             len(data.segments), len(data.small_symbols))
    log.debug("%s", format_histogram(data))
    width, color = select_pipe_cluster(data, cfg)
    pipe_segments = filter_pipe_segments(data, width, color, cfg)
    log.info("Rörsegment i valt kluster: %d", len(pipe_segments))
    chains = build_chains(pipe_segments, cfg)
    flag_frame_chains(chains, page.rect, cfg)
    n_symbols = assign_vertical_symbols(chains, data.small_symbols, cfg)
    active = [c for c in chains if not c.excluded]
    log.info("Rörsträckor: %d (varav %d exkluderade som ram/rutnät), "
             "%d vertikalsymboler tilldelade",
             len(chains), len(chains) - len(active), n_symbols)
    return chains, data, width, color
