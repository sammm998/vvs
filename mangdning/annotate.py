"""Markerad PDF-output.

Ritar kod-markeringar, rör-markeringar och ledartrådskopplingar som separata
PDF-lager (Optional Content Groups, tänd/släck i PDF-läsaren). Varje unik
kod får sin stabila färg (Del D punkt 3); okopplade rörsträckor ritas i
blått och okopplade koder i rött för manuell verifiering.

Prestanda: en riktig A1-ritning kan ge tusentals rörsträckor, så all
ritning batchas – ETT shape-commit per lager (inte per kedja) och lätt
sparning utan full garbage collection.
"""

from __future__ import annotations

import logging
from pathlib import Path

import fitz

from .colors import color_for_code, hex_to_rgb01
from .config import Config
from .models import CodeHit, Leader, PipeChain

log = logging.getLogger(__name__)

# Sträck-id-etiketter ritas bara upp till detta antal kedjor – vid fler blir
# etiketterna oläsbart täta och skrivningen långsam.
MAX_CHAIN_LABELS = 400


def annotate_pdf(input_pdf: str | Path, output_pdf: str | Path,
                 codes: list[CodeHit], chains: list[PipeChain],
                 leaders: list[Leader], cfg: Config) -> None:
    doc = fitz.open(str(input_pdf))
    page = doc[cfg.page]
    codes_by_id = {c.id: c for c in codes}
    active_chains = [c for c in chains if not c.excluded]

    def make_ocg(name: str) -> int:
        try:
            return doc.add_ocg(name, on=True)
        except Exception:
            return 0  # äldre PyMuPDF utan OCG-stöd: rita utan lager

    if "pipes" in cfg.layers:
        oc = make_ocg("Rörsträckor")
        # Gruppera kedjor per färg => en finish per färg, ETT commit totalt
        by_color: dict[tuple[float, float, float], list[PipeChain]] = {}
        for chain in active_chains:
            if chain.linked_codes:
                code = codes_by_id.get(chain.linked_codes[0])
                color = (hex_to_rgb01(color_for_code(code.full_code))
                         if code else (0.0, 0.0, 1.0))
            else:
                color = (0.0, 0.0, 1.0)  # blå = okopplad rörsträcka
            by_color.setdefault(color, []).append(chain)

        shape = page.new_shape()
        for color, group in by_color.items():
            for chain in group:
                for seg in chain.segments:
                    shape.draw_line(fitz.Point(*seg.p1), fitz.Point(*seg.p2))
            shape.finish(color=color, width=2.5, stroke_opacity=0.6, oc=oc)
        shape.commit(overlay=True)

        # sträck-id för spårbarhet mot mängdförteckningens "källa"-kolumn
        if len(active_chains) <= MAX_CHAIN_LABELS:
            tw = fitz.TextWriter(page.rect)
            for chain in active_chains:
                cx, cy = chain.bbox.center
                tw.append(fitz.Point(cx, cy), f"#{chain.id}", fontsize=5)
            tw.write_text(page, color=(0.1, 0.1, 0.5), oc=oc)
        else:
            log.info("Hoppar över sträck-id-etiketter (%d kedjor > %d)",
                     len(active_chains), MAX_CHAIN_LABELS)

    if "codes" in cfg.layers:
        oc = make_ocg("Koder")
        shape = page.new_shape()
        n_unlinked = 0
        for code in codes:
            if code.excluded:
                continue
            r = code.bbox
            rect = fitz.Rect(r.x0 - 1, r.y0 - 1, r.x1 + 1, r.y1 + 1)
            if code.linked_chain is not None:
                shape.draw_rect(rect)
                shape.finish(color=hex_to_rgb01(color_for_code(code.full_code)),
                             width=0.8, oc=oc)
            else:
                n_unlinked += 1
        # röd = ej kopplad till rör – verifiera manuellt (en finish för alla)
        if n_unlinked:
            for code in codes:
                if code.excluded or code.linked_chain is not None:
                    continue
                r = code.bbox
                shape.draw_rect(
                    fitz.Rect(r.x0 - 1, r.y0 - 1, r.x1 + 1, r.y1 + 1))
            shape.finish(color=(1.0, 0.0, 0.0), width=0.8, oc=oc)
        shape.commit(overlay=True)

    if "links" in cfg.layers:
        oc = make_ocg("Ledartrådskopplingar")
        shape = page.new_shape()
        drew = False
        for leader in leaders:
            if leader.code_id is None:
                continue
            shape.draw_line(fitz.Point(*leader.p1), fitz.Point(*leader.p2))
            drew = True
        if drew:
            shape.finish(color=(0.0, 0.6, 0.0), width=1.2,
                         dashes="[2 2] 0", oc=oc)
        shape.commit(overlay=True)

    # deflate utan djup garbage collection – GC på en fil med tiotusentals
    # vektorobjekt kan ta många minuter och tillför inget här
    doc.save(str(output_pdf), deflate=True)
    doc.close()
    log.info("Markerad PDF sparad: %s (lager: %s)",
             output_pdf, ", ".join(cfg.layers) or "inga")
