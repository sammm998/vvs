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

from .cadlayers import classify_layers, layer_name
from .config import Config
from .models import (BBox, PipeChain, Point, Segment, dist,
                     point_segment_distance)

log = logging.getLogger(__name__)


@dataclass
class DrawingData:
    """Alla stroke-segment ur sidan, plus småsymboler (cirklar m.m.)."""

    segments: list[Segment] = field(default_factory=list)
    # småsymboler: (centrum, diameter) för slutna kurv-paths (vertikalsymboler)
    small_symbols: list[tuple[Point, float]] = field(default_factory=list)
    width_histogram: Counter = field(default_factory=Counter)
    width_color: dict[float, Counter] = field(default_factory=dict)
    # CAD-lager: namn -> total ritad längd (pt), och vilka som valts som rör
    layer_lengths: dict[str, float] = field(default_factory=dict)
    pipe_layers: list[str] = field(default_factory=list)


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
        layer = d.get("layer") or ""
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
                    data.segments.append(Segment(p1, p2, width, color, layer))
                    has_line = True
            elif kind == "re":
                r = item[1]
                corners = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                for a, b in zip(corners, corners[1:] + corners[:1]):
                    data.segments.append(Segment(a, b, width, color, layer))
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
                        data.segments.append(Segment(a, b, width, color, layer))
                has_line = True
            elif kind == "qu":
                q = item[1]
                pts = [(q.ul.x, q.ul.y), (q.ur.x, q.ur.y),
                       (q.lr.x, q.lr.y), (q.ll.x, q.ll.y)]
                for a, b in zip(pts, pts[1:] + pts[:1]):
                    data.segments.append(Segment(a, b, width, color, layer))
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


def coverage_ratio(segments: list[Segment]) -> float:
    """Hur sammanhängande ligger segmenten längs sina egna linjer?

    Segmenten grupperas per exakt linje (riktning + vinkelrätt läge) och
    ritad längd ("bläck") jämförs med linjens totala utsträckning.

    Rörledningar löper som sammanhängande sträckor: ritat ≈ utsträckning,
    kvot nära 1 (streckade rör ligger runt 0,8). Väggar, skraffering,
    rutnät och måttlinjer består i stället av korta streck utspridda längs
    samma linjer, med stora tomrum emellan – kvoten hamnar under 0,3.
    Det är den skillnaden som gör att byggnadsstommen kan sorteras bort
    automatiskt i stället för att användaren ska rita uteslutningszoner.
    """
    if not segments:
        return 0.0
    groups: dict[tuple, list[float]] = {}
    ink = 0.0
    for seg in segments:
        ink += seg.length
        dx, dy = _direction(seg)
        if (dx, dy) == (0.0, 0.0):
            continue
        if dx < 0 or (dx == 0.0 and dy < 0):
            dx, dy = -dx, -dy
        key = (round(dx, 3), round(dy, 3),
               round(dx * seg.p1[1] - dy * seg.p1[0]))
        ts = groups.setdefault(key, [])
        ts.append(seg.p1[0] * dx + seg.p1[1] * dy)
        ts.append(seg.p2[0] * dx + seg.p2[1] * dy)
    span = sum(max(ts) - min(ts) for ts in groups.values())
    return ink / span if span > 0 else 0.0


def select_pipe_clusters(data: DrawingData, cfg: Config
                         ) -> list[tuple[float, tuple[float, float, float] | None]]:
    """Identifiera vilka bredd/färg-kluster som är rörlinjer.

    Inte hårdkodat. Är cfg.pipe_widths (eller cfg.pipe_width) satt används
    dessa. Annars: rör ritas med de grövsta pennorna medan text-konturer,
    skraffering, måttlinjer och byggnadsstommen är tunnare – och ett och
    samma system-set kan rita olika system med olika penna (i vår testfil
    spillvatten 2,04 pt och tappvatten 1,44 pt). Därför väljs ALLA
    signifikanta kluster som är minst pipe_width_ratio av det bredaste.

    Loggas alltid, så valet kan verifieras med --calibrate och överstyras
    med --pipe-width.
    """
    explicit = cfg.pipe_widths or (
        [cfg.pipe_width] if cfg.pipe_width is not None else None)
    if explicit:
        out = []
        for width in explicit:
            color = cfg.pipe_color or _dominant_color(data, width, cfg)
            out.append((width, color))
        log.info("Rörkluster (konfigurerat): %s",
                 ", ".join(f"{w:.2f} pt {c}" for w, c in out))
        return out

    common = data.width_histogram.most_common()
    if not common:
        raise ValueError("Inga stroke-segment i PDF:en – går inte att "
                         "identifiera rörlinjer.")
    total = sum(data.width_histogram.values())
    significant = [
        (w, n) for w, n in common
        if w > 0 and n >= max(cfg.min_cluster_count, total * cfg.min_cluster_frac)
    ]
    if not significant:
        width = max((w for w, _ in common if w > 0), default=common[0][0])
        return [(width, data.width_color[width].most_common(1)[0][0])]

    by_width: dict[float, list[Segment]] = {}
    for seg in data.segments:
        by_width.setdefault(seg.width, []).append(seg)

    # Två krav, som tillsammans skiljer rör från allt annat:
    #  1. sammanhang – rör löper i sträckor, väggar/skraffering är utspridda
    #  2. grovlek – ledartrådar och byggnadsstomme är sammanhängande men
    #     ritas med tunnare penna än rören
    scored = [(w, coverage_ratio(by_width[w])) for w, _ in significant]
    contiguous = [w for w, cov in scored if cov >= cfg.min_coverage]
    if contiguous:
        widest = max(contiguous)
        chosen = sorted((w for w in contiguous
                         if w >= widest * cfg.pipe_width_ratio), reverse=True)
    else:   # ingen klass ser ut som rör – ta den bredaste ändå
        chosen = [max(w for w, _ in significant)]
        log.warning("Ingen linjeklass har sammanhängande sträckor "
                    "(kvot >= %.2f) – faller tillbaka på den bredaste. "
                    "Kalibrera med --calibrate/--pipe-width.", cfg.min_coverage)

    out = [(w, data.width_color[w].most_common(1)[0][0]) for w in chosen]
    log.info("Rörkluster (auto): %s", ", ".join(
        f"{w:.2f} pt {c} [{data.width_histogram[w]} seg, "
        f"sammanhang {dict(scored).get(w, 0):.2f}]" for w, c in out))
    for w, cov in sorted(scored, reverse=True):
        if w in chosen:
            continue
        why = ("utspridda streck => byggnadsstomme/skraffering"
               if cov < cfg.min_coverage
               else "för tunn penna => ledartråd/stomlinje")
        log.info("Bortsorterat: %.2f pt (sammanhang %.2f) – %s", w, cov, why)
    return out


def _dominant_color(data: DrawingData, width: float, cfg: Config):
    best = None
    for w, colors in data.width_color.items():
        if abs(w - width) <= width * cfg.pipe_width_tol:
            for c, n in colors.items():
                if best is None or n > best[1]:
                    best = (c, n)
    return best[0] if best else None


def select_pipe_cluster(data: DrawingData, cfg: Config
                        ) -> tuple[float, tuple[float, float, float] | None]:
    """Bakåtkompatibelt: det bredaste (primära) rörklustret."""
    return select_pipe_clusters(data, cfg)[0]


def _color_close(a, b, tol: float) -> bool:
    if a is None or b is None:
        return a == b
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def filter_pipe_segments(data: DrawingData, width, color, cfg: Config
                         ) -> list[Segment]:
    """Behåll segment i något av rörklustren, utanför exkluderingszoner.

    width/color kan vara ett enskilt kluster eller en lista av (bredd, färg).
    """
    if isinstance(width, list):
        clusters = width
    else:
        clusters = [(width, color)]
    zones = cfg.exclude_bboxes()
    out = []
    for seg in data.segments:
        for w, c in clusters:
            if abs(seg.width - w) > w * cfg.pipe_width_tol:
                continue
            if c is not None and not _color_close(seg.color, c, cfg.color_tol):
                continue
            break
        else:
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
        for i, seg in enumerate(group):
            t1 = seg.p1[0] * dx + seg.p1[1] * dy
            t2 = seg.p2[0] * dx + seg.p2[1] * dy
            # index som tie-break: Segment saknar ordning
            spans.append((min(t1, t2), max(t1, t2), i, seg))
        spans.sort()

        run_start, run_end, _i, first = spans[0]
        for start, end, _i, seg in spans[1:]:
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
    return Segment(p0, p1, ref.width, ref.color, ref.layer)


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


def _split_at_contacts(segments: list[Segment], tol: float) -> list[Segment]:
    """Klipp segment där andra segments ändpunkter ansluter mitt på dem.

    Ett avstick ritas ofta mot mitten av ett längre segment; utan klippning
    ser ändpunktskedjnigen aldrig anslutningen, och utan brytpunkten kan
    grenen inte delas vid avsticket."""
    if len(segments) <= 1:
        return list(segments)
    cell = max(tol, 0.5)
    grid: dict[tuple[int, int], list[Point]] = {}
    for seg in segments:
        for p in (seg.p1, seg.p2):
            grid.setdefault((int(p[0] // cell), int(p[1] // cell)), []).append(p)

    pieces: list[Segment] = []
    for seg in segments:
        length = seg.length
        if length <= 2 * tol:
            pieces.append(seg)
            continue
        dx = (seg.p2[0] - seg.p1[0]) / length
        dy = (seg.p2[1] - seg.p1[1]) / length
        x0 = min(seg.p1[0], seg.p2[0]) - tol
        x1 = max(seg.p1[0], seg.p2[0]) + tol
        y0 = min(seg.p1[1], seg.p2[1]) - tol
        y1 = max(seg.p1[1], seg.p2[1]) + tol
        cuts: list[float] = []
        for gx in range(int(x0 // cell), int(x1 // cell) + 1):
            for gy in range(int(y0 // cell), int(y1 // cell) + 1):
                for p in grid.get((gx, gy), []):
                    t = (p[0] - seg.p1[0]) * dx + (p[1] - seg.p1[1]) * dy
                    if not (tol < t < length - tol):
                        continue
                    if point_segment_distance(p, seg.p1, seg.p2) <= tol:
                        if all(abs(t - c) > tol for c in cuts):
                            cuts.append(t)
        if not cuts:
            pieces.append(seg)
            continue
        prev = 0.0
        for t in sorted(cuts) + [length]:
            a = (seg.p1[0] + prev * dx, seg.p1[1] + prev * dy)
            b = (seg.p1[0] + t * dx, seg.p1[1] + t * dy)
            pieces.append(Segment(a, b, seg.width, seg.color, seg.layer))
            prev = t
    return pieces


def _decompose_into_branches(pieces: list[Segment], tol: float
                             ) -> list[list[Segment]]:
    """Dela upp ett sammanhängande rörnät i grenar vid förgreningspunkterna.

    En mängdning redovisar varje rör mellan två avstick som en egen sträcka
    (facit: 8,7 m + 6,3 m), medan en naiv kedjning gärna mäter rakt igenom
    korsningen (15,0 m). Nodgraf över (redan klippta) segment; grenar
    vandras fram mellan noder som inte har exakt två anslutningar."""
    if len(pieces) <= 1:
        return [list(pieces)]
    cell = max(tol, 0.5)

    # Nod-id per (klustrad) ändpunkt
    node_pts: list[Point] = []
    node_grid: dict[tuple[int, int], list[int]] = {}

    def node_id(p: Point) -> int:
        gx, gy = int(p[0] // cell), int(p[1] // cell)
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                for idx in node_grid.get((gx + ddx, gy + ddy), []):
                    if dist(p, node_pts[idx]) <= tol:
                        return idx
        node_pts.append(p)
        node_grid.setdefault((gx, gy), []).append(len(node_pts) - 1)
        return len(node_pts) - 1

    ends: list[tuple[int, int]] = []
    adj: dict[int, list[int]] = {}
    for i, piece in enumerate(pieces):
        a, b = node_id(piece.p1), node_id(piece.p2)
        ends.append((a, b))
        adj.setdefault(a, []).append(i)
        adj.setdefault(b, []).append(i)

    # 3. Vandra grenar mellan noder med grad != 2
    used: set[int] = set()
    branches: list[list[Segment]] = []

    def walk(start: int, from_node: int) -> list[Segment]:
        chain = []
        i, n = start, from_node
        while True:
            used.add(i)
            chain.append(pieces[i])
            a, b = ends[i]
            other = b if a == n else a
            if len(adj[other]) != 2:
                break
            nxt = [j for j in adj[other] if j != i and j not in used]
            if not nxt:
                break
            i, n = nxt[0], other
        return chain

    for node, incident in adj.items():
        if len(incident) == 2:
            continue
        for i in incident:
            if i not in used:
                branches.append(walk(i, node))
    for i in range(len(pieces)):     # rena cykler utan ändar
        if i not in used:
            branches.append(walk(i, ends[i][0]))
    return branches


def build_chains(segments: list[Segment], cfg: Config,
                 pts_per_meter: float | None = None) -> list[PipeChain]:
    """Bygg PipeChain-objekt med punktlista, längd och bbox (Del B punkt 4)."""
    # Streckade linjer slås först ihop till hela linjer, så att luckorna
    # räknas som rör (och bara en gång) innan nätet kedjas ihop. Därefter
    # klipps segmenten där avstick ansluter mitt på dem, så att både
    # kedjningen och grendelningen ser anslutningarna.
    segments = merge_collinear_runs(segments, cfg.dash_gap_pt,
                                    cfg.dash_angle_deg)
    segments = _split_at_contacts(segments, cfg.chain_tol_pt)
    min_len = cfg.min_chain_len_pt
    if pts_per_meter and cfg.min_run_m > 0:
        min_len = max(min_len, cfg.min_run_m * pts_per_meter)

    # Kedja per lager: två system som korsar varandra på ritningen hör inte
    # ihop, och en sträcka får aldrig löpa från spillvatten över i tappvatten.
    by_layer: dict[str, list[Segment]] = {}
    for seg in segments:
        by_layer.setdefault(seg.layer, []).append(seg)

    chains: list[PipeChain] = []
    dropped = 0
    dropped_len = 0.0
    groups = [g for segs in by_layer.values()
              for g in chain_segments(segs, cfg.chain_tol_pt)]
    for group, bridged in groups:
        for branch in (_decompose_into_branches(group, cfg.chain_tol_pt)
                       if cfg.split_at_junctions else [group]):
            length = sum(s.length for s in branch)
            if length < min_len:
                dropped += 1
                dropped_len += length
                continue
            chains.extend(_make_chain(branch, 0.0, cfg, next_id=len(chains)))
    if dropped:
        log.info("Kortare grenar än %.2f pt bortsorterade som "
                 "kopplingsstumpar: %d st (%.0f pt totalt)",
                 min_len, dropped, dropped_len)
    return chains


def _make_chain(group: list[Segment], bridged: float, cfg: Config,
                next_id: int) -> list[PipeChain]:
    length = sum(s.length for s in group) + bridged
    xs = [c for s in group for c in (s.p1[0], s.p2[0])]
    ys = [c for s in group for c in (s.p1[1], s.p2[1])]
    points = []
    for s in group:
        points.extend([s.p1, s.p2])
    return [PipeChain(
        id=next_id, segments=group, points=points,
        length_pt=length,
        bbox=BBox(min(xs), min(ys), max(xs), max(ys)),
        endpoints=_chain_endpoints(group, cfg.chain_tol_pt),
    )]


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


def select_by_layers(data: DrawingData, cfg: Config
                     ) -> tuple[list[Segment], dict[str, str]] | None:
    """Välj rörsegment utifrån CAD-lagren, om PDF:en bär dem.

    Det här är den exakta vägen: lagret kommer från CAD-modellen och säger
    både vad linjen är och vilket system den tillhör. Först när lager
    saknas (eller inget lager ser ut som en VVS-ledning) faller vi tillbaka
    på att gissa utifrån linjebredd och geometri.
    """
    if not cfg.use_layers:
        return None
    lengths: dict[str, float] = {}
    for seg in data.segments:
        if seg.layer:
            lengths[seg.layer] = lengths.get(seg.layer, 0.0) + seg.length
    if not lengths:
        return None
    pipe_layers, systems = classify_layers(lengths, cfg.pipe_layer_regex)
    if cfg.pipe_layers is not None:
        # användaren har valt lager själv i gränssnittet
        wanted = set(cfg.pipe_layers)
        pipe_layers = [n for n in lengths if layer_name(n) in wanted or n in wanted]
        log.info("Rörlager valda av användaren: %d st", len(pipe_layers))
    if not pipe_layers:
        return None
    chosen = set(pipe_layers)
    segments = [s for s in data.segments if s.layer in chosen]
    return segments, systems


def detect_pipes(page: fitz.Page, cfg: Config,
                 pts_per_meter: float | None = None
                 ) -> tuple[list[PipeChain], DrawingData, float,
                            tuple[float, float, float] | None]:
    """Hela Del B: extrahera, välj rörlinjer, kedja, flagga ram."""
    data = extract_drawings(page, cfg)
    log.info("Vektordata: %d segment, %d småsymboler",
             len(data.segments), len(data.small_symbols))
    log.debug("%s", format_histogram(data))

    data.layer_lengths = {}
    for seg in data.segments:
        if seg.layer:
            data.layer_lengths[seg.layer] = (
                data.layer_lengths.get(seg.layer, 0.0) + seg.length)

    by_layer = select_by_layers(data, cfg)
    if by_layer is not None:
        data.pipe_layers = sorted({s.layer for s in by_layer[0]})
        pipe_segments, layer_systems = by_layer
        widths = Counter(s.width for s in pipe_segments)
        width = widths.most_common(1)[0][0] if widths else 0.0
        color = None
        log.info("Rörlinjer valda via CAD-lager: %d segment", len(pipe_segments))
    else:
        layer_systems = {}
        clusters = select_pipe_clusters(data, cfg)
        width, color = clusters[0]
        pipe_segments = filter_pipe_segments(data, clusters, None, cfg)
        log.info("Rörsegment i valt kluster: %d", len(pipe_segments))

    chains = build_chains(pipe_segments, cfg, pts_per_meter)
    for chain in chains:
        chain.layer = chain.segments[0].layer if chain.segments else ""
        chain.system = layer_systems.get(chain.layer)
    flag_frame_chains(chains, page.rect, cfg)
    n_symbols = assign_vertical_symbols(chains, data.small_symbols, cfg)
    active = [c for c in chains if not c.excluded]
    log.info("Rörsträckor: %d (varav %d exkluderade som ram/rutnät), "
             "%d vertikalsymboler tilldelade",
             len(chains), len(chains) - len(active), n_symbols)
    return chains, data, width, color
