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


def _flatten_bezier(p0: Point, p1: Point, p2: Point, p3: Point,
                    steps: int) -> list[tuple[Point, Point]]:
    """Platta ut en kubisk Bezier till korta räta segment."""
    pts: list[Point] = []
    for i in range(steps + 1):
        t = i / steps
        u = 1.0 - t
        x = (u * u * u * p0[0] + 3 * u * u * t * p1[0]
             + 3 * u * t * t * p2[0] + t * t * t * p3[0])
        y = (u * u * u * p0[1] + 3 * u * u * t * p1[1]
             + 3 * u * t * t * p2[1] + t * t * t * p3[1])
        pts.append((x, y))
    return list(zip(pts, pts[1:]))


def extract_drawings(page: fitz.Page, cfg: Config) -> DrawingData:
    """Plocka ut alla linjesegment (med bredd/färg) och små slutna symboler."""
    data = DrawingData()
    for d in page.get_drawings():
        width = round(d.get("width") or 0.0, 2)
        color = _color_key(d.get("color"))
        rect = d.get("rect")
        items = d.get("items") or []

        has_line = False
        curve_items = [it for it in items if it[0] == "c"]

        # Ett litet path som BARA består av kurvor är en symbol (⊙/pil vid
        # rörände, brunn), inte rörgeometri – det ska inte plattas ut till
        # rörsegment.
        is_symbol = False
        if curve_items and len(curve_items) == len(items) and rect is not None:
            diameter = max(rect.width, rect.height)
            if 0.0 < diameter <= cfg.symbol_max_diameter_pt:
                is_symbol = True
                data.small_symbols.append(
                    (((rect.x0 + rect.x1) / 2.0, (rect.y0 + rect.y1) / 2.0),
                     diameter))

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
            elif kind == "c" and not is_symbol:
                # Rörböjar ritas som Bezier-kurvor. Utan dem tappas både
                # krökens längd och kopplingen mellan de raka delarna, så
                # varje kurva plattas ut till korta segment.
                for a, b in _flatten_bezier(
                        (item[1].x, item[1].y), (item[2].x, item[2].y),
                        (item[3].x, item[3].y), (item[4].x, item[4].y),
                        cfg.bezier_steps):
                    if a != b:
                        data.segments.append(Segment(a, b, width, color))
                has_line = True
            elif kind == "qu":
                q = item[1]
                pts = [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y),
                       (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
                for a, b in zip(pts, pts[1:] + pts[:1]):
                    data.segments.append(Segment(a, b, width, color))
                has_line = True

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
    total = sum(data.width_histogram.values())
    # Rörledningar ritas med den grövsta pennan; text-konturer, skraffering,
    # måttlinjer och ledartrådar är alla tunnare. Välj därför det BREDASTE
    # klustret som är signifikant (nog många segment för att vara ett riktigt
    # lager, inte enstaka specialobjekt). Bredd 0 = hårfin linje, aldrig rör.
    significant = [
        (w, n) for w, n in common
        if w > 0 and n >= max(cfg.min_cluster_count, total * cfg.min_cluster_frac)
    ]
    if significant:
        width = max(significant, key=lambda item: item[0])[0]
    else:
        width = max((w for w, _ in common if w > 0), default=common[0][0])
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


def _direction(seg: Segment) -> tuple[float, float]:
    dx = seg.p2[0] - seg.p1[0]
    dy = seg.p2[1] - seg.p1[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n else (0.0, 0.0)


def _parallel(d1: tuple[float, float], d2: tuple[float, float],
              cos_tol: float) -> bool:
    """Riktningarna är (anti)parallella inom vinkeltoleransen."""
    return abs(d1[0] * d2[0] + d1[1] * d2[1]) >= cos_tol


def merge_collinear_runs(segments: list[Segment], max_gap_pt: float,
                         angle_deg: float = 6.0,
                         offset_tol_pt: float = 1.0) -> list[Segment]:
    """Slå ihop streckade linjer till hela linjer (Del B punkt 3).

    AutoCAD exporterar en streckad rörlinje som fristående korta streck med
    luft emellan. Summan av strecken är bara "bläcket" och underskattar
    rörlängden (i vår testfil 117 m ritat mot 141 m verklig geometri). Streck
    som ligger på SAMMA oändliga linje – samma riktning OCH samma vinkelräta
    läge – och vars lucka längs linjen är högst max_gap_pt ersätts därför av
    ETT segment som spänner hela sträckan, luckorna inräknade.

    Kravet på samma linje (inte bara samma riktning) är det som gör att två
    parallella rör bredvid varandra aldrig slås ihop.
    """
    if max_gap_pt <= 0 or not segments:
        return list(segments)

    buckets: dict[tuple, list[Segment]] = {}
    loose: list[Segment] = []
    for seg in segments:
        dx, dy = _direction(seg)
        if (dx, dy) == (0.0, 0.0):
            loose.append(seg)
            continue
        if dx < 0 or (dx == 0.0 and dy < 0):      # kanonisk riktning
            dx, dy = -dx, -dy
        offset = dx * seg.p1[1] - dy * seg.p1[0]  # vinkelrätt läge för linjen
        # Exakt riktning + vinkelrätt läge: bara streck på PRECIS samma
        # linje hamnar i samma hink. Grövre vinkelhinkar drar in närliggande
        # rör och skulle då kapa bort verklig längd.
        key = (round(dx, 3), round(dy, 3), round(offset / offset_tol_pt))
        buckets.setdefault(key, []).append(seg)

    merged: list[Segment] = list(loose)
    for group in buckets.values():
        if len(group) == 1:
            merged.append(group[0])
            continue
        dx, dy = _direction(group[0])
        if dx < 0 or (dx == 0.0 and dy < 0):
            dx, dy = -dx, -dy
        spans = []
        for seg in group:
            t1 = seg.p1[0] * dx + seg.p1[1] * dy
            t2 = seg.p2[0] * dx + seg.p2[1] * dy
            spans.append((min(t1, t2), max(t1, t2), seg))
        spans.sort()

        run_start, run_end, first = spans[0]
        for start, end, seg in spans[1:]:
            if start - run_end <= max_gap_pt:     # samma streckade linje
                run_end = max(run_end, end)
            else:
                merged.append(_span_segment(first, run_start, run_end, dx, dy))
                run_start, run_end, first = start, end, seg
        merged.append(_span_segment(first, run_start, run_end, dx, dy))
    return merged


def _span_segment(ref: Segment, t0: float, t1: float,
                  dx: float, dy: float) -> Segment:
    """Ett segment längs ref:s linje som spänner projektionsintervallet."""
    base = ref.p1
    t_base = base[0] * dx + base[1] * dy
    p0 = (base[0] + (t0 - t_base) * dx, base[1] + (t0 - t_base) * dy)
    p1 = (base[0] + (t1 - t_base) * dx, base[1] + (t1 - t_base) * dy)
    return Segment(p0, p1, ref.width, ref.color)


def chain_segments(segments: list[Segment], tol_pt: float,
                   dash_gap_pt: float = 0.0,
                   dash_angle_deg: float = 8.0
                   ) -> list[tuple[list[Segment], float]]:
    """Del B punkt 3: kedja ihop segment vars ändpunkter ligger inom tol_pt.

    Rörlinjer ritas ofta som många korta raka segment i rad; union-find på
    ändpunkterna slår ihop dem till sammanhängande rörsträckor.

    STRECKADE rör: AutoCAD exporterar streckade linjetyper som fristående
    korta segment med luft emellan (inte som ett PDF-dash-mönster). Summan av
    de ritade strecken blir då bara "bläcket" och underskattar den verkliga
    rörlängden kraftigt. Med dash_gap_pt > 0 bryggas därför luckor upp till
    den längden mellan segment som är kollinjära – parallella OCH i varandras
    förlängning – och luckans längd räknas som rör.

    Returnerar en lista av (segment i kedjan, bryggad extralängd i pt).
    """
    if not segments:
        return []
    uf = _UnionFind(len(segments))

    def _pairs(radius: float):
        """Alla ändpunktspar inom radius, via rutnätsindex."""
        cell = max(radius, 0.5)
        grid: dict[tuple[int, int], list[tuple[int, Point]]] = {}
        for i, seg in enumerate(segments):
            for p in (seg.p1, seg.p2):
                grid.setdefault((int(p[0] // cell), int(p[1] // cell)),
                                []).append((i, p))
        for i, seg in enumerate(segments):
            for p in (seg.p1, seg.p2):
                gx, gy = int(p[0] // cell), int(p[1] // cell)
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for j, q in grid.get((gx + dx, gy + dy), []):
                            if j > i:
                                yield i, j, p, q

    # Fas 1: sammanfallande ändpunkter (hörn och delade noder)
    for i, j, p, q in _pairs(tol_pt):
        if dist(p, q) <= tol_pt:
            uf.union(i, j)

    # Fas 2: bryggning över streckluckor mellan kollinjära segment
    bridged: dict[int, float] = {}
    if dash_gap_pt > tol_pt:
        cos_tol = math.cos(math.radians(dash_angle_deg))
        candidates = []
        for i, j, p, q in _pairs(dash_gap_pt):
            gap = dist(p, q)
            if not (tol_pt < gap <= dash_gap_pt):
                continue
            di, dj = _direction(segments[i]), _direction(segments[j])
            if not _parallel(di, dj, cos_tol):
                continue
            # luckan ska fortsätta linjen, inte gå på tvären
            gd = ((q[0] - p[0]) / gap, (q[1] - p[1]) / gap)
            if not (_parallel(gd, di, cos_tol) and _parallel(gd, dj, cos_tol)):
                continue
            candidates.append((gap, i, j))
        # kortaste luckorna först => varje streck binds till närmaste granne
        for gap, i, j in sorted(candidates):
            if uf.find(i) != uf.find(j):
                uf.union(i, j)
                bridged[uf.find(i)] = bridged.get(uf.find(i), 0.0) + gap

    groups: dict[int, list[Segment]] = {}
    for i, seg in enumerate(segments):
        groups.setdefault(uf.find(i), []).append(seg)
    # bryggad längd kan ha bokförts på en rot som senare slogs ihop
    totals: dict[int, float] = {}
    for root, extra in bridged.items():
        totals[uf.find(root)] = totals.get(uf.find(root), 0.0) + extra
    return [(segs, totals.get(root, 0.0)) for root, segs in groups.items()]


def _chain_endpoints(segments: list[Segment], tol_pt: float) -> list[Point]:
    """Grad-1-noder i kedjan (fria rörändar) – används för vertikalsymboler.

    Rutnätsindexerad så att även mycket stora kedjor (tusentals segment i en
    riktig CAD-export) hanteras i linjär tid.
    """
    cell = max(tol_pt, 0.5)
    grid: dict[tuple[int, int], list[list]] = {}  # cell -> [[punkt, antal], ...]
    for seg in segments:
        for p in (seg.p1, seg.p2):
            gx, gy = int(p[0] // cell), int(p[1] // cell)
            entry = None
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for e in grid.get((gx + dx, gy + dy), []):
                        if dist(p, e[0]) <= tol_pt:
                            entry = e
                            break
                    if entry:
                        break
                if entry:
                    break
            if entry is not None:
                entry[1] += 1
            else:
                grid.setdefault((gx, gy), []).append([p, 1])
    return [e[0] for entries in grid.values() for e in entries if e[1] == 1]


def build_chains(segments: list[Segment], cfg: Config) -> list[PipeChain]:
    """Bygg PipeChain-objekt med punktlista, längd och bbox (Del B punkt 4)."""
    # Streckade linjer slås först ihop till hela linjer, så att luckorna
    # räknas som rör (och bara en gång) innan nätet kedjas ihop.
    segments = merge_collinear_runs(segments, cfg.dash_gap_pt,
                                    cfg.dash_angle_deg)
    chains: list[PipeChain] = []
    for group, bridged in chain_segments(segments, cfg.chain_tol_pt):
        length = sum(s.length for s in group) + bridged
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
    # Rutnätsindex över rörändpunkter: en vektoriserad CAD-text kan ge
    # tusentals småsymbol-kandidater, så uppslag måste vara O(1) per symbol.
    cell = max(cfg.symbol_pipe_tol_pt, 1.0)
    grid: dict[tuple[int, int], list[tuple[Point, PipeChain]]] = {}
    for chain in active:
        for p in (chain.endpoints or chain.points):
            grid.setdefault((int(p[0] // cell), int(p[1] // cell)),
                            []).append((p, chain))
    for center, _diameter in symbols:
        gx, gy = int(center[0] // cell), int(center[1] // cell)
        best_chain = None
        best_d = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for p, chain in grid.get((gx + dx, gy + dy), []):
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
