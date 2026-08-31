"""Konfiguration för hela pipelinen.

Allt som är filspecifikt (regex, linjebredder, zoner, vertikalhöjder,
systemprefix) ligger här och kan överstyras via CLI-flaggor eller en
JSON-konfigfil (--config). Ingenting av detta ska vara hårdkodat i
pipelinemodulerna.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .models import BBox

# Standard-regex för rörkoder (Del A punkt 6). Fångar t.ex.
# S3-R8-75, VG+1.46(L), KV2-X31, 2xVV1-X31, B7-GOLVBRUNN.
DEFAULT_CODE_REGEX = r"^([0-9]x)?[A-ZÅÄÖ]{1,4}[0-9=+\-][A-ZÅÄÖ0-9+=\.\(\)\-]*$"

# En dimensionsrad (raden under koden) börjar typiskt med siffra eller
# nivåprefix, t.ex. "160(L)", "75", "VG+1.65(L)", "+2.30".
DEFAULT_DIM_REGEX = r"^[0-9VF+ØÖ]"

# Sista bindestrecks-delen av en kod som redan ÄR en dimension,
# t.ex. "110" i "S3-R8-110" – då görs ingen radparning.
DEFAULT_DIM_PART_REGEX = r"^[0-9]+([\.,][0-9]+)?(\(.*\))?$"

# Fallback för systemkategorisering om legenden inte kan OCR-läsas.
DEFAULT_SYSTEM_PREFIXES: dict[str, str] = {
    "KV": "Rör tappvatten",
    "VV": "Rör tappvatten",
    "VVC": "Rör tappvatten",
    "S": "Spill- dagvatten",
    "D": "Spill- dagvatten",
}

DEFAULT_VERTICAL_HEIGHTS: dict[str, float] = {
    "Rör tappvatten": 2.8,
    "Spill- dagvatten": 2.8,
    "Okänt system": 2.8,
}


@dataclass
class Config:
    # --- Del A: OCR ---
    dpi: int = 450                  # minst 400-450 DPI
    tile_px: int = 1200             # rutstorlek i pixlar
    tile_overlap: float = 0.25      # ~25 % överlapp
    psm_modes: list[int] = field(default_factory=lambda: [11, 6])
    ocr_lang: str = "swe"
    min_conf: float = 30.0          # OCR-träffar under detta ignoreras
    code_regex: str = DEFAULT_CODE_REGEX
    dim_regex: str = DEFAULT_DIM_REGEX
    dim_part_regex: str = DEFAULT_DIM_PART_REGEX
    dedup_tol_pt: float = 12.0      # samma text inom detta avstånd = dubblett
    pair_gap_min_pt: float = -2.0   # vertikalt gap kod -> dimensionsrad
    pair_gap_max_pt: float = 20.0
    pair_min_h_overlap: float = 0.3  # andel av smalaste boxens bredd
    force_ocr: bool = False
    skip_ocr: bool = False
    native_word_threshold: int = 100  # fler riktiga ord än så => PDF-text räcker
    ocr_threads: int = 0            # parallella tesseract-processer; 0 = auto
    # Rutor med mindre andel mörka pixlar än så här saknar text och
    # OCR-läses inte alls (tom ritningsyta på stora format).
    min_tile_ink: float = 0.002

    # --- Del B: rördetektering ---
    pipe_width: float | None = None       # None = auto via histogram
    # En ritning kan rita olika system med olika penna (i vår testfil
    # spillvatten på 2,04 pt och tappvatten på 1,44 pt). Alla signifikanta
    # kluster som är minst så här stor andel av det bredaste räknas som rör.
    pipe_widths: list[float] | None = None   # None = auto
    pipe_width_ratio: float = 0.45
    # Minsta "sammanhang" (ritad längd / linjens utsträckning) för att en
    # linjeklass ska räknas som rör. Rör löper sammanhängande (0,8-1,3);
    # väggar, skraffering och rutnät är utspridda streck (under 0,3).
    min_coverage: float = 0.5
    pipe_width_tol: float = 0.15          # relativ tolerans mot klustret
    # Ett bredd-kluster räknas som ett riktigt ritlager (och inte enstaka
    # specialobjekt) om det har minst så här många segment – båda villkoren
    # ska uppfyllas. Styr vilket kluster autovalet får landa på.
    min_cluster_count: int = 40
    min_cluster_frac: float = 0.002
    pipe_color: tuple[float, float, float] | None = None  # None = auto
    color_tol: float = 0.15
    chain_tol_pt: float = 1.5             # ändpunktstolerans vid kedjning
    # Streckade rör exporteras som fristående korta segment; luckor upp till
    # denna längd mellan kollinjära segment bryggas och räknas som rörlängd.
    # 0 = av (behandla varje streck som en egen sträcka).
    dash_gap_pt: float = 5.0
    dash_angle_deg: float = 8.0
    bezier_steps: int = 4                 # utplattning av rörböjar (kurvor)
    frame_len_pt: float = 700.0           # längre raka ensamma linjer = ram/rutnät
    min_chain_len_pt: float = 3.0         # kortare kedjor ignoreras (brus)
    # Etiketter under denna längd ritas inte ut – korta stumpar skulle annars
    # täcka ritningen med "0,2 m"-rutor. Raderna finns kvar i förteckningen.
    label_min_m: float = 1.0

    # --- Del C: ledartrådar ---
    leader_max_width_ratio: float = 0.8   # ledartråd smalare än rörbredd * ratio
    leader_min_len_pt: float = 4.0
    leader_max_len_pt: float = 250.0
    leader_code_tol_pt: float = 15.0      # ändpunkt nära kodens bbox
    leader_pipe_tol_pt: float = 8.0       # andra änden nära rörsträcka
    proximity_link_pt: float = 25.0       # fallback: kod direkt nära rör
    diagonal_min_deg: float = 4.0         # min vinkel mot axlarna

    # --- Del D: mängdning ---
    scale: str | None = None              # t.ex. "1:50" eller "56.7pt/m"
    vertical_heights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_VERTICAL_HEIGHTS))
    system_prefixes: dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_SYSTEM_PREFIXES))
    # "5xKV2-X31" betyder fem parallella rör. I de ritningsmallar vi sett
    # ritas de fem rören som fem egna linjer, och de mäts då redan var för
    # sig – att dessutom multiplicera längden med N dubbelräknar dem
    # (facit: KV2-X31-16 = 5 rader à ca 6,7 m, inte 5 x 33,4 m). Sätt till
    # True bara för mallar där bunten ritas som EN linje.
    nx_multiplies_length: bool = False
    symbol_max_diameter_pt: float = 10.0  # vertikalsymbolens storlek
    symbol_pipe_tol_pt: float = 6.0

    # --- Zoner (exkludering av legend/titelblock, Del A punkt 9) ---
    exclude_zones: list[tuple[float, float, float, float]] = field(
        default_factory=list)

    # --- Output ---
    # Ledartrådslagret ritar en grön linje per kopplad kod och gör ritningen
    # svårläst på täta ritningar – med som val, men inte som standard.
    layers: list[str] = field(
        default_factory=lambda: ["codes", "pipes"])
    page: int = 0

    def compiled_code_regex(self) -> re.Pattern:
        return re.compile(self.code_regex)

    def compiled_dim_regex(self) -> re.Pattern:
        return re.compile(self.dim_regex)

    def compiled_dim_part_regex(self) -> re.Pattern:
        return re.compile(self.dim_part_regex)

    def exclude_bboxes(self) -> list[BBox]:
        return [BBox(*z) for z in self.exclude_zones]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_file(cls, path: str | Path) -> "Config":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        cfg = cls()
        for key, value in data.items():
            if not hasattr(cfg, key):
                raise KeyError(f"Okänd konfignyckel: {key}")
            if key == "pipe_color" and value is not None:
                value = tuple(value)
            if key == "exclude_zones":
                value = [tuple(z) for z in value]
            setattr(cfg, key, value)
        return cfg


def parse_vertical_heights(spec: str) -> dict[str, float]:
    """Tolka CLI-flaggan --vertikalhojd "Rör tappvatten=2.8,Spill- dagvatten=2.8"."""
    result: dict[str, float] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(
                f"Ogiltig vertikalhöjd '{part}' – förväntat format System=meter")
        name, value = part.rsplit("=", 1)
        result[name.strip()] = float(value.replace(",", "."))
    return result


def parse_zone(spec: str) -> tuple[float, float, float, float]:
    """Tolka "x0,y0,x1,y1" till en exkluderingszon."""
    parts = [float(v) for v in spec.split(",")]
    if len(parts) != 4:
        raise ValueError(f"Ogiltig zon '{spec}' – förväntat x0,y0,x1,y1")
    x0, y0, x1, y1 = parts
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
