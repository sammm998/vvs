"""Markerad PDF-output.

Ritar kod-markeringar, rör-markeringar och ledartrådskopplingar som separata
PDF-lager (Optional Content Groups, tänd/släck i PDF-läsaren). Varje unik
kod får sin stabila färg (Del D punkt 3); okopplade rörsträckor ritas i
blått och okopplade koder i rött för manuell verifiering.
"""

from __future__ import annotations

import logging
from pathlib import Path

import fitz

from .colors import color_for_code, hex_to_rgb01
from .config import Config
from .models import CodeHit, Leader, PipeChain

log = logging.getLogger(__name__)


def annotate_pdf(input_pdf: str | Path, output_pdf: str | Path,
                 codes: list[CodeHit], chains: list[PipeChain],
                 leaders: list[Leader], cfg: Config) -> None:
    doc = fitz.open(str(input_pdf))
    page = doc[cfg.page]
    codes_by_id = {c.id: c for c in codes}

    def make_ocg(name: str) -> int:
        try:
            return doc.add_ocg(name, on=True)
        except Exception:
            return 0  # äldre PyMuPDF utan OCG-stöd: rita utan lager

    if "pipes" in cfg.layers:
        oc = make_ocg("Rörsträckor")
        for chain in chains:
            if chain.excluded:
                continue
            if chain.linked_codes:
                code = codes_by_id.get(chain.linked_codes[0])
                color = (hex_to_rgb01(color_for_code(code.full_code))
                         if code else (0.0, 0.0, 1.0))
            else:
                color = (0.0, 0.0, 1.0)  # blå = okopplad rörsträcka
            shape = page.new_shape()
            for seg in chain.segments:
                shape.draw_line(fitz.Point(*seg.p1), fitz.Point(*seg.p2))
            shape.finish(color=color, width=2.5, stroke_opacity=0.6, oc=oc)
            shape.commit(overlay=True)
            # sträck-id för spårbarhet mot mängdförteckningens "källa"-kolumn
            cx, cy = chain.bbox.center
            page.insert_text(fitz.Point(cx, cy), f"#{chain.id}",
                             fontsize=5, color=color, oc=oc)

    if "codes" in cfg.layers:
        oc = make_ocg("Koder")
        shape = page.new_shape()
        linked_shape = page.new_shape()
        for code in codes:
            if code.excluded:
                continue
            r = code.bbox
            rect = fitz.Rect(r.x0 - 1, r.y0 - 1, r.x1 + 1, r.y1 + 1)
            if code.linked_chain is not None:
                color = hex_to_rgb01(color_for_code(code.full_code))
                linked_shape.draw_rect(rect)
                linked_shape.finish(color=color, width=0.8, oc=oc)
            else:
                # röd = ej kopplad till rör – verifiera manuellt
                shape.draw_rect(rect)
        shape.finish(color=(1.0, 0.0, 0.0), width=0.8, oc=oc)
        shape.commit(overlay=True)
        linked_shape.commit(overlay=True)

    if "links" in cfg.layers:
        oc = make_ocg("Ledartrådskopplingar")
        shape = page.new_shape()
        for leader in leaders:
            if leader.code_id is None:
                continue
            shape.draw_line(fitz.Point(*leader.p1), fitz.Point(*leader.p2))
        shape.finish(color=(0.0, 0.6, 0.0), width=1.2, dashes="[2 2] 0", oc=oc)
        shape.commit(overlay=True)

    doc.save(str(output_pdf), garbage=3, deflate=True)
    doc.close()
    log.info("Markerad PDF sparad: %s (lager: %s)",
             output_pdf, ", ".join(cfg.layers) or "inga")
