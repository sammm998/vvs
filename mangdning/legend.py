"""Legendläsning – systemkategorisering (Del D punkt 4) och antalsuppgifter
för punktkomponenter (Del D punkt 7).

Legenden ("FÖRKLARINGAR" med underrubriker som "SYSTEM TAPPVATTEN" /
"SYSTEM SPILLVATTEN") talar om vilka kodprefix som hör till vilket system.
Den läses via OCR-träffarna från Del A i stället för hårdkodade prefixlistor;
konfig-prefixen används som fallback.
"""

from __future__ import annotations

import logging
import re

from .config import Config
from .models import BBox, OcrHit

log = logging.getLogger(__name__)

# "SYSTEM TAPPVATTEN" -> facit-kategorin "Rör tappvatten"
SYSTEM_NAME_MAP = {
    "TAPPVATTEN": "Rör tappvatten",
    "SPILLVATTEN": "Spill- dagvatten",
    "DAGVATTEN": "Spill- dagvatten",
    "VÄRME": "Rör värme",
    "VARME": "Rör värme",
}

_COUNT_RE = re.compile(r"\(?\s*(\d+)\s*st\s*\)?", re.IGNORECASE)


def code_prefix(code: str) -> str:
    """Prefixet före första bindestrecket, utan siffror: "KV1-X31" => "KV",
    "S3-R8-75" => "S". Används för system-uppslag."""
    head = code.split("-")[0]
    letters = re.match(r"^[A-ZÅÄÖ]+", head)
    return letters.group(0) if letters else head


def full_prefix(code: str) -> str:
    """Prefixet inkl. siffra: "KV1-X31" => "KV1"."""
    return code.split("-")[0]


def _lines_from_hits(hits: list[OcrHit]) -> list[tuple[str, BBox]]:
    """Gruppera ordträffar till textrader (samma y-nivå, sorterade i x)."""
    remaining = sorted(hits, key=lambda h: (h.bbox.y0, h.bbox.x0))
    lines: list[list[OcrHit]] = []
    for hit in remaining:
        placed = False
        for line in lines:
            ref = line[-1]
            same_row = (abs(hit.bbox.center[1] - ref.bbox.center[1])
                        < max(ref.bbox.height, hit.bbox.height) * 0.6)
            near_x = 0 <= hit.bbox.x0 - ref.bbox.x1 < 40.0
            if same_row and near_x:
                line.append(hit)
                placed = True
                break
        if not placed:
            lines.append([hit])
    result = []
    for line in lines:
        text = " ".join(h.text for h in line)
        x0 = min(h.bbox.x0 for h in line)
        y0 = min(h.bbox.y0 for h in line)
        x1 = max(h.bbox.x1 for h in line)
        y1 = max(h.bbox.y1 for h in line)
        result.append((text, BBox(x0, y0, x1, y1)))
    return result


def parse_legend(all_hits: list[OcrHit], cfg: Config
                 ) -> tuple[dict[str, str], dict[str, int], BBox | None]:
    """Läs legenden ur OCR-träffarna.

    Returnerar (prefix->system, kod->förväntat antal (st), legend-bbox).
    """
    lines = _lines_from_hits(all_hits)
    code_re = cfg.compiled_code_regex()

    legend_bbox: BBox | None = None
    for text, bbox in lines:
        if "FÖRKLARINGAR" in text.upper() or "FORKLARINGAR" in text.upper():
            legend_bbox = bbox
            break

    # Systemrubriker: "SYSTEM TAPPVATTEN" etc.
    headers: list[tuple[str, BBox]] = []
    for text, bbox in lines:
        up = text.upper()
        if "SYSTEM" in up:
            for key, system in SYSTEM_NAME_MAP.items():
                if key in up:
                    headers.append((system, bbox))
                    break

    prefix_map: dict[str, str] = {}
    expected_counts: dict[str, int] = {}

    if headers:
        # Koder i legendkolumnen under en rubrik (fram till nästa rubrik)
        headers_sorted = sorted(headers, key=lambda h: h[1].y0)
        for i, (system, hb) in enumerate(headers_sorted):
            y_end = (headers_sorted[i + 1][1].y0
                     if i + 1 < len(headers_sorted) else hb.y1 + 400.0)
            for text, bbox in lines:
                if not (hb.y1 - 2.0 < bbox.y0 < y_end):
                    continue
                if abs(bbox.x0 - hb.x0) > 120.0:
                    continue
                first = text.split()[0] if text.split() else ""
                if code_re.match(first):
                    prefix_map[full_prefix(first)] = system
                    prefix_map.setdefault(code_prefix(first), system)
        if prefix_map:
            log.info("Legend: systemprefix lästa från ritningen: %s", prefix_map)

    # Antalsuppgifter: "B1-GOLVBRUNN 300×300 (8st)"
    for text, _bbox in lines:
        parts = text.split()
        if not parts or not code_re.match(parts[0]):
            continue
        m = _COUNT_RE.search(text)
        if m:
            expected_counts[parts[0]] = int(m.group(1))
    if expected_counts:
        log.info("Legend: antalsuppgifter (sanity-check): %s", expected_counts)

    if not prefix_map:
        log.info("Legend: inga systemrubriker kunde läsas – använder "
                 "konfigurerade fallback-prefix: %s", cfg.system_prefixes)
    return prefix_map, expected_counts, legend_bbox


def system_for_code(code: str, prefix_map: dict[str, str],
                    cfg: Config) -> str:
    """Slå upp systemkategori för en kod: legendens prefix först
    (längsta matchning), sedan konfig-fallback."""
    for source in (prefix_map, cfg.system_prefixes):
        fp = full_prefix(code)
        if fp in source:
            return source[fp]
        cp = code_prefix(code)
        if cp in source:
            return source[cp]
        # längsta prefix-matchning (t.ex. "VVC" före "VV")
        best = ""
        for prefix in source:
            if code.startswith(prefix) and len(prefix) > len(best):
                best = prefix
        if best:
            return source[best]
    return "Okänt system"
