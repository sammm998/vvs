"""Del D punkt 1 – skalbestämning.

Ritningens PDF-punkter omvandlas till meter via:
  1. CLI-parametern --scale (högsta prioritet, t.ex. "1:50" eller "56.69pt/m"),
  2. titelblockets skaltext (t.ex. "SKALA A1 (A3) 1:50 (1:100)") via OCR,
  3. den grafiska skalstocken ("0 1 2 3 4 5 m") – avstånd mellan siffrorna.

Skalfaktorn är alltid en explicit, loggad parameter i output – aldrig tyst
gissad. Ger titelblock och skalstock olika svar flaggas en varning.
"""

from __future__ import annotations

import logging
import re

from .config import Config
from .models import OcrHit, ScaleResult

log = logging.getLogger(__name__)

# 1 m i verkligheten vid skala 1:N motsvarar (1000/N) mm på papperet;
# 1 mm = 72/25.4 pt.
MM_PER_PT = 25.4 / 72.0

_SCALE_TEXT_RE = re.compile(r"\b1\s*[:;]\s*(\d{1,4})\b")
_PT_PER_M_RE = re.compile(r"^([0-9]+(?:[\.,][0-9]+)?)\s*pt/m$", re.IGNORECASE)
_RATIO_RE = re.compile(r"^1\s*:\s*(\d{1,4})$")


def ratio_to_pts_per_meter(n: int) -> float:
    """Skala 1:N => PDF-punkter per verklig meter."""
    mm_on_paper = 1000.0 / n
    return mm_on_paper / MM_PER_PT


def parse_scale_arg(spec: str) -> float:
    """Tolka --scale: "1:50" eller "56.69pt/m"."""
    spec = spec.strip()
    m = _RATIO_RE.match(spec)
    if m:
        return ratio_to_pts_per_meter(int(m.group(1)))
    m = _PT_PER_M_RE.match(spec)
    if m:
        return float(m.group(1).replace(",", "."))
    raise ValueError(f"Ogiltig skala '{spec}' – använd '1:50' eller '56.69pt/m'")


def scale_from_title_text(hits: list[OcrHit]) -> tuple[float, str] | None:
    """Leta efter skaltext i OCR-träffarna. Föredrar träffar nära ordet
    'SKALA'; annars den vanligaste 1:N-siffran på sidan."""
    candidates: list[tuple[int, str, bool]] = []
    skala_boxes = [h.bbox for h in hits if "SKALA" in h.text.upper()]
    for h in hits:
        for m in _SCALE_TEXT_RE.finditer(h.text):
            n = int(m.group(1))
            if not (1 <= n <= 2000):
                continue
            near_skala = any(
                b.expanded(150.0).contains(h.bbox.center) for b in skala_boxes)
            candidates.append((n, m.group(0), near_skala))
    if not candidates:
        return None
    near = [c for c in candidates if c[2]]
    pool = near or candidates
    # första (minsta N brukar vara huvudskalan i "1:50 (1:100)")
    n = min(c[0] for c in pool)
    text = next(c[1] for c in pool if c[0] == n)
    return ratio_to_pts_per_meter(n), f"1:{n}"


def scale_from_scale_bar(hits: list[OcrHit]) -> float | None:
    """Grafisk skalstock: hitta siffrorna 0..N i en horisontell rad med
    jämna mellanrum, med ett 'm' i närheten. Avstånd 0->N = N meter."""
    digit_hits = [h for h in hits if re.fullmatch(r"[0-9]{1,2}", h.text)]
    m_hits = [h for h in hits if h.text.lower() in ("m", "meter")]
    if not digit_hits or not m_hits:
        return None

    best: float | None = None
    best_n = 0
    for zero in [h for h in digit_hits if h.text == "0"]:
        zy = zero.bbox.center[1]
        row = [h for h in digit_hits
               if abs(h.bbox.center[1] - zy) < 6.0
               and h.bbox.center[0] >= zero.bbox.center[0]]
        try:
            row_vals = sorted({int(h.text): h for h in row}.items())
        except ValueError:
            continue
        # kräver 0,1,2,... i följd med ungefär jämna mellanrum
        seq = [row_vals[0]] if row_vals and row_vals[0][0] == 0 else []
        for val, h in row_vals[1:]:
            if seq and val == seq[-1][0] + 1:
                seq.append((val, h))
        if len(seq) < 3:
            continue
        # ett 'm' nära radens slut?
        end = seq[-1][1].bbox.center
        if not any(abs(mh.bbox.center[1] - zy) < 8.0
                   and 0 < mh.bbox.center[0] - end[0] < 60.0 for mh in m_hits):
            continue
        gaps = [b[1].bbox.center[0] - a[1].bbox.center[0]
                for a, b in zip(seq, seq[1:])]
        mean_gap = sum(gaps) / len(gaps)
        if mean_gap <= 0 or any(abs(g - mean_gap) > 0.25 * mean_gap for g in gaps):
            continue
        n_meters = seq[-1][0]
        span = seq[-1][1].bbox.center[0] - seq[0][1].bbox.center[0]
        if n_meters > best_n:
            best_n = n_meters
            best = span / n_meters
    return best


def determine_scale(hits: list[OcrHit], cfg: Config) -> ScaleResult:
    result = ScaleResult(pts_per_meter=None, method="okänd")

    if cfg.scale:
        result.pts_per_meter = parse_scale_arg(cfg.scale)
        result.method = "cli"
        result.scale_text = cfg.scale
        log.info("Skala (CLI): %s => %.3f pt/m", cfg.scale, result.pts_per_meter)
        return result

    title = scale_from_title_text(hits)
    if title:
        result.title_pts_per_meter, result.scale_text = title
    bar = scale_from_scale_bar(hits)
    if bar:
        result.bar_pts_per_meter = bar

    if result.title_pts_per_meter and result.bar_pts_per_meter:
        diff = abs(result.title_pts_per_meter - result.bar_pts_per_meter)
        rel = diff / result.title_pts_per_meter
        if rel > 0.05:
            result.warnings.append(
                f"Titelblock ({result.scale_text} = "
                f"{result.title_pts_per_meter:.2f} pt/m) och skalstock "
                f"({result.bar_pts_per_meter:.2f} pt/m) ger olika svar "
                f"({rel * 100:.0f} % skillnad) – verifiera skalan manuellt!")
        result.pts_per_meter = result.title_pts_per_meter
        result.method = "titelblock"
    elif result.title_pts_per_meter:
        result.pts_per_meter = result.title_pts_per_meter
        result.method = "titelblock"
    elif result.bar_pts_per_meter:
        result.pts_per_meter = result.bar_pts_per_meter
        result.method = "skalstock"
    else:
        result.warnings.append(
            "Ingen skala kunde läsas ur titelblock eller skalstock – "
            "längder redovisas i PDF-punkter. Ange skala med --scale.")

    if result.known:
        log.info("Skala (%s): %s => %.3f pt/m",
                 result.method, result.scale_text or "-", result.pts_per_meter)
    for w in result.warnings:
        log.warning("%s", w)
    return result
