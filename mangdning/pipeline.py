"""Hela pipelinen (Del A-D + output) som en återanvändbar funktion.

Används av både CLI:t (mangdning/cli.py) och webbappen (webapp/app.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import fitz

from .annotate import annotate_pdf
from .config import Config
from .legend import parse_legend
from .linking import find_leader_candidates, link_codes_to_pipes
from .models import BBox
from .ocr_codes import collect_hits, extract_codes
from .pipes import detect_pipes, format_histogram
from .quantify import build_quantities
from .report import (write_code_table, write_quantities_csv,
                     write_quantities_xlsx, write_run_report)
from .scale import determine_scale
from .validate import read_facit, validate_against_facit

log = logging.getLogger(__name__)

# Stegnamn för progressrapportering (on_stage-callbacken)
STAGES = [
    ("ocr", "Del A: läser textkoder (OCR)"),
    ("pipes", "Del B: detekterar rörledningar"),
    ("linking", "Del C: kopplar koder till rör"),
    ("scale", "Del D: bestämmer skala"),
    ("quantify", "Del D: mängdar"),
    ("output", "Skriver utdatafiler"),
]


@dataclass
class PipelineOutputs:
    annotated_pdf: Path
    code_table_csv: Path
    quantities_xlsx: Path
    quantities_csv: Path
    report_txt: Path
    validation_txt: Path | None = None
    validation_text: str | None = None
    summary: dict = field(default_factory=dict)


def run_pipeline(input_pdf: str | Path, cfg: Config, out_dir: str | Path,
                 facit: str | Path | None = None,
                 on_scale: Callable | None = None,
                 on_stage: Callable[[str, str], None] | None = None
                 ) -> PipelineOutputs:
    """Kör Del A-D på en PDF och skriv alla utdatafiler till out_dir.

    on_scale(scale, chains): anropas efter skalbestämningen, före mängdningen
        (CLI:t använder den för det interaktiva verifieringssteget).
    on_stage(stage_id, beskrivning): progressrapportering per steg.
    """
    def stage(stage_id: str):
        if on_stage is not None:
            label = dict(STAGES).get(stage_id, stage_id)
            on_stage(stage_id, label)

    input_pdf = Path(input_pdf)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = input_pdf.stem

    doc = fitz.open(str(input_pdf))
    try:
        if not (0 <= cfg.page < len(doc)):
            raise ValueError(
                f"Sida {cfg.page} finns inte (PDF:en har {len(doc)} sidor)")
        page = doc[cfg.page]

        # --- Del A: koder via OCR ---
        stage("ocr")
        all_hits, text_info = collect_hits(page, cfg)

        # Legend: systemkategorier + antalsuppgifter; auto-exkludera legendzon
        prefix_map, expected_counts, legend_bbox = parse_legend(all_hits, cfg)
        if legend_bbox is not None:
            zone = BBox(legend_bbox.x0 - 10, legend_bbox.y0 - 5,
                        legend_bbox.x0 + 260, page.rect.height)
            cfg.exclude_zones.append(zone.as_tuple())
            log.info("Legendzon auto-exkluderad: %s", zone.as_tuple())

        codes = extract_codes(all_hits, cfg)

        # --- Del B: rörsträckor ur vektordata ---
        stage("pipes")
        chains, drawing_data, pipe_width, pipe_color = detect_pipes(page, cfg)

        # --- Del C: koppla kod <-> rör via ledartrådar ---
        stage("linking")
        leaders = find_leader_candidates(drawing_data, pipe_width, chains, cfg)
        link_codes_to_pipes(codes, chains, leaders, cfg)

        # --- Del D: skala + mängdning ---
        stage("scale")
        scale = determine_scale(all_hits, cfg)
        if on_scale is not None:
            on_scale(scale, chains)
        stage("quantify")
        result = build_quantities(
            codes, chains, scale, prefix_map, expected_counts, cfg,
            document=input_pdf.name, sidetikett=f"Sida {cfg.page + 1}")

        # --- Output ---
        stage("output")
        outputs = PipelineOutputs(
            annotated_pdf=out_dir / f"{stem}_markerad.pdf",
            code_table_csv=out_dir / f"{stem}_koder.csv",
            quantities_xlsx=out_dir / f"{stem}_mangder.xlsx",
            quantities_csv=out_dir / f"{stem}_mangder.csv",
            report_txt=out_dir / f"{stem}_rapport.txt",
        )
        annotate_pdf(input_pdf, outputs.annotated_pdf, codes, chains,
                     leaders, cfg)
        write_code_table(codes, outputs.code_table_csv)
        write_quantities_xlsx(result, outputs.quantities_xlsx)
        write_quantities_csv(result, outputs.quantities_csv)
        write_run_report(
            outputs.report_txt, input_pdf=str(input_pdf),
            codes=codes, chains=chains, scale=scale, result=result,
            pipe_width=pipe_width, pipe_color=pipe_color, text_info=text_info,
            histogram_text=format_histogram(drawing_data))

        if facit:
            report = validate_against_facit(result, read_facit(facit))
            outputs.validation_txt = out_dir / f"{stem}_validering.txt"
            outputs.validation_txt.write_text(report.text(), encoding="utf-8")
            outputs.validation_text = report.text()
            log.info("Valideringsrapport sparad: %s", outputs.validation_txt)

        active_chains = [c for c in chains if not c.excluded]
        active_codes = [c for c in codes if not c.excluded]
        outputs.summary = {
            "native_words": text_info.native_words,
            "vector_paths": text_info.vector_paths,
            "ocr_anvands": text_info.use_ocr,
            "pipe_width": pipe_width,
            "n_koder": len(active_codes),
            "n_koder_kopplade": sum(
                1 for c in active_codes if c.linked_chain is not None),
            "n_rorstrackor": len(active_chains),
            "n_rorstrackor_kopplade": sum(
                1 for c in active_chains if c.linked_codes),
            "n_rader": len(result.rows),
            "n_unika_koder": len(result.aggregates),
            "skala_metod": scale.method,
            "skala_text": scale.scale_text,
            "pts_per_meter": scale.pts_per_meter,
            "varningar": list(result.warnings),
            "antal_kontroller": list(result.count_checks),
        }
        return outputs
    finally:
        doc.close()
