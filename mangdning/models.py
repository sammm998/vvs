"""Gemensamma datamodeller för hela pipelinen.

Alla koordinater är i PDF-punkter (1 pt = 1/72 tum) i sidans koordinatsystem,
oavsett vilken DPI som användes vid rastrering för OCR.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


Point = tuple[float, float]


@dataclass
class BBox:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def center(self) -> Point:
        return ((self.x0 + self.x1) / 2.0, (self.y0 + self.y1) / 2.0)

    def contains(self, p: Point) -> bool:
        return self.x0 <= p[0] <= self.x1 and self.y0 <= p[1] <= self.y1

    def expanded(self, margin: float) -> "BBox":
        return BBox(self.x0 - margin, self.y0 - margin,
                    self.x1 + margin, self.y1 + margin)

    def h_overlap(self, other: "BBox") -> float:
        """Horisontell överlappning i punkter (<=0 om ingen)."""
        return min(self.x1, other.x1) - max(self.x0, other.x0)

    def distance_to_point(self, p: Point) -> float:
        dx = max(self.x0 - p[0], 0.0, p[0] - self.x1)
        dy = max(self.y0 - p[1], 0.0, p[1] - self.y1)
        return math.hypot(dx, dy)

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


@dataclass
class OcrHit:
    """En rå OCR-träff (eller ett riktigt PDF-textord). Sparas ALLTID,
    även om texten inte matchar kod-regexen – behövs för radparning,
    legend-läsning och skaltext."""

    text: str
    bbox: BBox
    conf: float           # 0-100; 100.0 för riktiga PDF-textord
    source: str = "ocr"   # "ocr" | "pdftext"
    psm: int | None = None


@dataclass
class CodeHit:
    """En godkänd kodträff efter regexfiltrering, dedup och radparning."""

    id: int
    raw_text: str                 # texten som den lästes, t.ex. "2xKV1-X31"
    base_code: str                # utan Nx-prefix, t.ex. "KV1-X31"
    count: int                    # N från "Nx"-notation, annars 1
    bbox: BBox
    conf: float
    dimension: str | None = None  # parad dimensionsrad, t.ex. "160(L)"
    excluded: bool = False        # legend/titelblock/exkluderingszon
    excluded_reason: str | None = None
    linked_chain: int | None = None   # PipeChain.id
    link_method: str | None = None    # "leader" | "proximity"
    raw_cluster_size: int = 1     # antal råa OCR-träffar som dedupades ihop

    @property
    def full_code(self) -> str:
        """Kod inkl. dimension, t.ex. 'S3-P2-160(L)'."""
        if self.dimension:
            return f"{self.base_code}-{self.dimension}"
        return self.base_code


@dataclass
class Segment:
    p1: Point
    p2: Point
    width: float
    color: tuple[float, float, float] | None
    layer: str = ""          # CAD-lagret linjen ritades på (om PDF:en bär det)

    @property
    def length(self) -> float:
        return math.hypot(self.p2[0] - self.p1[0], self.p2[1] - self.p1[1])

    def is_axis_aligned(self, tol_deg: float = 2.0) -> bool:
        dx = abs(self.p2[0] - self.p1[0])
        dy = abs(self.p2[1] - self.p1[1])
        if dx == 0.0 or dy == 0.0:
            return True
        ang = math.degrees(math.atan2(min(dx, dy), max(dx, dy)))
        return ang <= tol_deg


@dataclass
class PipeChain:
    """En hopkedjad sammanhängande rörsträcka (Del B punkt 3-4)."""

    id: int
    segments: list[Segment]
    points: list[Point]           # alla ändpunkter (för uppslag/ritning)
    length_pt: float              # total längd i PDF-punkter
    bbox: BBox
    layer: str = ""               # CAD-lagret sträckan tillhör
    system: str | None = None     # systemkategori ur lagret, om känd
    endpoints: list[Point] = field(default_factory=list)  # grad-1-noder
    linked_codes: list[int] = field(default_factory=list)  # CodeHit.id
    vertical_symbols: int = 0     # antal vertikala rörfall-symboler på kedjan
    excluded: bool = False
    excluded_reason: str | None = None


@dataclass
class Leader:
    """En ledartråd (tunn diagonal linje) mellan text och rör."""

    id: int
    p1: Point
    p2: Point
    width: float
    code_id: int | None = None
    chain_id: int | None = None


@dataclass
class ScaleResult:
    """Resultatet av skalbestämningen – alltid explicit och loggad."""

    pts_per_meter: float | None
    method: str                   # "cli" | "titelblock" | "skalstock" | "okänd"
    scale_text: str | None = None       # t.ex. "1:50"
    bar_pts_per_meter: float | None = None
    title_pts_per_meter: float | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def known(self) -> bool:
        return self.pts_per_meter is not None and self.pts_per_meter > 0

    def to_meters(self, length_pt: float) -> float | None:
        if not self.known:
            return None
        return length_pt / self.pts_per_meter


@dataclass
class QuantityRow:
    """En rad i mängdförteckningen: en rörsträcka ELLER en punktkomponent
    ELLER en vertikalpost – strukturellt jämförbar med facit-formatet."""

    subject: str                  # kod inkl. dimension
    lager: str                    # systemkategori, t.ex. "Rör tappvatten"
    color: str                    # stabil hex-färg per unik kod
    langd_m: float | None = None  # längd i meter (rörsträckor)
    antal: int | None = None      # antal (punktkomponenter)
    antal_vs: int | None = None   # antal vertikala rörfall
    vertikal_hojd_m: float | None = None
    total_vertikalhojd_m: float | None = None
    kalla: str = ""               # "chain:17" / "code:42" – spårbarhet
    kommentar: str = ""           # osäkerhetsflaggor m.m.
    document: str = ""
    sidetikett: str = ""


def dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def point_segment_distance(p: Point, a: Point, b: Point) -> float:
    """Kortaste avstånd från punkt p till segmentet a-b."""
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))
