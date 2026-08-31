"""Del A – koddetektering via OCR.

AutoCAD exporterar text som vektoriserade konturer (särskilt SHX-typsnitt),
så texten måste läsas med OCR på en rastrerad bild. Riktiga PDF-textord
(om de finns) tas också med. Rörledningarna hanteras INTE här – de läses
direkt ur vektordatan i pipes.py.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import fitz  # PyMuPDF

from .config import Config
from .models import BBox, CodeHit, OcrHit

log = logging.getLogger(__name__)

_NX_RE = re.compile(r"^([0-9]+)[xX](?=[A-ZÅÄÖ])")


@dataclass
class TextLayerInfo:
    native_words: int
    vector_paths: int
    use_ocr: bool


def inspect_text_layer(page: fitz.Page, cfg: Config) -> TextLayerInfo:
    """Kontrollera om PDF:en har riktig maskinläsbar text eller om texten är
    vektoriserad (få riktiga ord, många vektor-paths => OCR krävs)."""
    words = page.get_text("words")
    paths = page.get_drawings()
    use_ocr = len(words) < cfg.native_word_threshold
    if cfg.force_ocr:
        use_ocr = True
    if cfg.skip_ocr:
        use_ocr = False
    log.info(
        "Textlager: %d riktiga textord, %d vektor-paths => %s",
        len(words), len(paths),
        "OCR krävs (vektoriserad text)" if use_ocr else "PDF-text används",
    )
    return TextLayerInfo(len(words), len(paths), use_ocr)


def native_word_hits(page: fitz.Page) -> list[OcrHit]:
    hits = []
    for x0, y0, x1, y1, word, *_ in page.get_text("words"):
        word = word.strip()
        if word:
            hits.append(OcrHit(word, BBox(x0, y0, x1, y1), 100.0, source="pdftext"))
    return hits


def iter_tiles(width_px: int, height_px: int, tile_px: int, overlap: float):
    """Överlappande rutor som täcker hela bilden."""
    step = max(1, int(tile_px * (1.0 - overlap)))
    xs = list(range(0, max(width_px - tile_px, 0) + 1, step))
    ys = list(range(0, max(height_px - tile_px, 0) + 1, step))
    if not xs or xs[-1] + tile_px < width_px:
        xs.append(max(width_px - tile_px, 0))
    if not ys or ys[-1] + tile_px < height_px:
        ys.append(max(height_px - tile_px, 0))
    for y in sorted(set(ys)):
        for x in sorted(set(xs)):
            yield x, y, min(tile_px, width_px - x), min(tile_px, height_px - y)


def render_page(page: fitz.Page, dpi: int):
    """Rastrera sidan till en PIL-bild i gråskala."""
    from PIL import Image

    pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
    return Image.frombytes("L", (pix.width, pix.height), pix.samples)


def _ocr_one(tile_img, tx: int, ty: int, psm: int, cfg: Config,
             px_to_pt: float) -> list[OcrHit]:
    import pytesseract

    hits: list[OcrHit] = []
    data = pytesseract.image_to_data(
        tile_img, lang=cfg.ocr_lang,
        config=f"--psm {psm}",
        output_type=pytesseract.Output.DICT,
    )
    for j in range(len(data["text"])):
        text = data["text"][j].strip()
        if not text:
            continue
        try:
            conf = float(data["conf"][j])
        except (TypeError, ValueError):
            continue
        if conf < cfg.min_conf:
            continue
        x = (tx + data["left"][j]) * px_to_pt
        y = (ty + data["top"][j]) * px_to_pt
        w = data["width"][j] * px_to_pt
        h = data["height"][j] * px_to_pt
        hits.append(OcrHit(text, BBox(x, y, x + w, y + h),
                           conf, source="ocr", psm=psm))
    return hits


def ocr_page(page: fitz.Page, cfg: Config,
             progress: "callable | None" = None) -> list[OcrHit]:
    """Kör OCR i överlappande rutor med flera PSM-lägen och slå ihop allt.

    ALLA träffar returneras – filtrering mot kod-regexen sker senare, eftersom
    icke-kod-träffar behövs för radparning, legend och skaltext.

    Tesseract körs som separata processer, så rutorna OCR-läses parallellt
    (cfg.ocr_threads; 0 = antal CPU-kärnor, max 4). progress(klara, totalt)
    anropas löpande – en A1-ritning i 450 DPI är flera hundra OCR-anrop.
    """
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed

    img = render_page(page, cfg.dpi)
    px_to_pt = 72.0 / cfg.dpi
    tiles = list(iter_tiles(img.width, img.height, cfg.tile_px, cfg.tile_overlap))
    jobs = [(tx, ty, tw, th, psm)
            for (tx, ty, tw, th) in tiles for psm in cfg.psm_modes]
    n_threads = cfg.ocr_threads or min(os.cpu_count() or 1, 4)
    log.info("OCR: %d rutor à %dpx (%d%% överlapp), PSM %s, %d DPI, "
             "%d parallella tesseract-processer => %d anrop",
             len(tiles), cfg.tile_px, int(cfg.tile_overlap * 100),
             cfg.psm_modes, cfg.dpi, n_threads, len(jobs))

    hits: list[OcrHit] = []
    done = 0
    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = [
            pool.submit(_ocr_one, img.crop((tx, ty, tx + tw, ty + th)),
                        tx, ty, psm, cfg, px_to_pt)
            for (tx, ty, tw, th, psm) in jobs]
        for future in as_completed(futures):
            hits.extend(future.result())
            done += 1
            if progress is not None:
                progress(done, len(jobs))
            if done % 40 == 0:
                log.info("OCR: %d/%d anrop klara (%d träffar hittills)",
                         done, len(jobs), len(hits))
    log.info("OCR: totalt %d råa träffar", len(hits))
    return hits


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


def dedup_hits(hits: list[OcrHit], tol_pt: float) -> list[list[OcrHit]]:
    """Deduplicering av OCR-träffar (Del A punkt 5).

    Två träffar slås ihop ENDAST om BÅDE texten är identisk OCH positionerna
    ligger inom tol_pt av varandra. Samma kodtext på geometriskt skilda
    platser förblir alltså separata träffar – varje geometriskt kluster
    returneras som en egen lista.
    """
    by_text: dict[str, list[int]] = {}
    for i, h in enumerate(hits):
        by_text.setdefault(h.text, []).append(i)

    uf = _UnionFind(len(hits))
    for indices in by_text.values():
        # Rumsligt rutnät per textvärde för att undvika O(n^2) i värsta fall
        cell = max(tol_pt, 1.0)
        grid: dict[tuple[int, int], list[int]] = {}
        for i in indices:
            cx, cy = hits[i].bbox.center
            grid.setdefault((int(cx // cell), int(cy // cell)), []).append(i)
        for i in indices:
            cx, cy = hits[i].bbox.center
            gx, gy = int(cx // cell), int(cy // cell)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j in grid.get((gx + dx, gy + dy), []):
                        if j <= i:
                            continue
                        ox, oy = hits[j].bbox.center
                        if abs(cx - ox) <= tol_pt and abs(cy - oy) <= tol_pt:
                            uf.union(i, j)

    clusters: dict[int, list[OcrHit]] = {}
    for i in range(len(hits)):
        clusters.setdefault(uf.find(i), []).append(hits[i])
    return list(clusters.values())


def best_of_cluster(cluster: list[OcrHit]) -> OcrHit:
    return max(cluster, key=lambda h: (h.source == "pdftext", h.conf))


def parse_nx(text: str) -> tuple[int, str]:
    """Tolka "Nx"-notation: "2xKV1-X31" => (2, "KV1-X31"). En kodpost som
    representerar N parallella rör med samma beteckning."""
    m = _NX_RE.match(text)
    if m:
        return int(m.group(1)), text[m.end():]
    return 1, text


def suppress_partial_reads(codes: list[CodeHit]) -> list[CodeHit]:
    """Ta bort partiella OCR-läsningar: en kod vars text är en delsträng av
    en annan kods text OCH vars position ligger inom den andres bbox är en
    ofullständig läsning av samma text (t.ex. "V1-X31" ovanpå "2xKV1-X31"
    från en rutkant eller ett annat PSM-läge)."""
    keep: list[CodeHit] = []
    for a in codes:
        partial = False
        for b in codes:
            if a is b or len(a.raw_text) >= len(b.raw_text):
                continue
            if a.raw_text in b.raw_text \
                    and b.bbox.expanded(3.0).contains(a.bbox.center):
                partial = True
                break
        if not partial:
            keep.append(a)
    if len(keep) != len(codes):
        log.info("Partiella OCR-läsningar borttagna: %d", len(codes) - len(keep))
    return keep


def _has_inline_dimension(base_code: str, cfg: Config) -> bool:
    """Innehåller koden redan sin dimension på egen rad? T.ex. "S3-R8-110"
    (sista delen är numerisk) => ingen radparning behövs."""
    parts = base_code.split("-")
    if len(parts) < 2:
        return False
    return bool(cfg.compiled_dim_part_regex().match(parts[-1]))


def pair_dimension_lines(codes: list[CodeHit], all_hits: list[OcrHit],
                         cfg: Config) -> None:
    """Del A punkt 7: para ihop en kodrad med dimensionsraden direkt under,
    oavsett om den undre raden matchar kod-regexen."""
    dim_re = cfg.compiled_dim_regex()
    code_re = cfg.compiled_code_regex()
    for code in codes:
        if _has_inline_dimension(code.base_code, cfg):
            continue
        best: OcrHit | None = None
        best_gap = None
        for hit in all_hits:
            if hit.bbox.as_tuple() == code.bbox.as_tuple():
                continue
            gap = hit.bbox.y0 - code.bbox.y1
            if not (cfg.pair_gap_min_pt <= gap <= cfg.pair_gap_max_pt):
                continue
            overlap = code.bbox.h_overlap(hit.bbox)
            min_w = max(min(code.bbox.width, hit.bbox.width), 1.0)
            if overlap / min_w < cfg.pair_min_h_overlap:
                continue
            # Raden under ska se ut som en dimension/nivå – men får inte vara
            # en egen fristående kod (två staplade koder ska inte slås ihop).
            if code_re.match(hit.text) and not dim_re.match(hit.text):
                continue
            if not dim_re.match(hit.text):
                continue
            if best is None or gap < best_gap:
                best, best_gap = hit, gap
        if best is not None:
            code.dimension = best.text
    return None


def apply_exclusion_zones(codes: list[CodeHit], cfg: Config) -> None:
    """Del A punkt 9: manuella exkluderingszoner (legend, titelblock)."""
    zones = cfg.exclude_bboxes()
    for code in codes:
        for zone in zones:
            if zone.contains(code.bbox.center):
                code.excluded = True
                code.excluded_reason = "exkluderingszon"
                break


def extract_codes(all_hits: list[OcrHit], cfg: Config) -> list[CodeHit]:
    """Filtrera, deduplicera och radpara OCR-träffarna till kodposter."""
    code_re = cfg.compiled_code_regex()
    candidates = [h for h in all_hits if code_re.match(h.text)]
    clusters = dedup_hits(candidates, cfg.dedup_tol_pt)
    log.info("Kodkandidater: %d råa träffar => %d efter dedup",
             len(candidates), len(clusters))

    codes: list[CodeHit] = []
    for i, cluster in enumerate(sorted(
            clusters, key=lambda c: (c[0].bbox.y0, c[0].bbox.x0))):
        best = best_of_cluster(cluster)
        count, base = parse_nx(best.text)
        codes.append(CodeHit(
            id=i, raw_text=best.text, base_code=base, count=count,
            bbox=best.bbox, conf=best.conf, raw_cluster_size=len(cluster),
        ))
    codes = suppress_partial_reads(codes)
    for new_id, code in enumerate(codes):
        code.id = new_id
    pair_dimension_lines(codes, all_hits, cfg)
    apply_exclusion_zones(codes, cfg)
    return codes


def collect_hits(page: fitz.Page, cfg: Config,
                 progress: "callable | None" = None
                 ) -> tuple[list[OcrHit], TextLayerInfo]:
    """Hela Del A-insamlingen: PDF-textord + OCR vid behov."""
    info = inspect_text_layer(page, cfg)
    hits = native_word_hits(page)
    if info.use_ocr:
        hits.extend(ocr_page(page, cfg, progress=progress))
    return hits, info
