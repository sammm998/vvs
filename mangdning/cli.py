"""CLI: python mangda_ritning.py input.pdf --output-dir out/

Kör hela pipelinen Del A-D och skriver:
  out/<namn>_markerad.pdf        markerad PDF med lager
  out/<namn>_koder.csv           kodtabell (position, confidence, koppling)
  out/<namn>_mangder.xlsx        mängdförteckning (facit-struktur + flikar)
  out/<namn>_mangder.csv         samma radnivå som CSV
  out/<namn>_rapport.txt         körningsrapport och kända begränsningar
  out/<namn>_validering.txt      avvikelserapport (om --facit anges)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import fitz

from . import __version__
from .config import Config, parse_vertical_heights, parse_zone
from .pipeline import run_pipeline
from .pipes import extract_drawings, format_histogram

log = logging.getLogger("mangdning")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mangda_ritning",
        description="Mängdning av CAD-exporterade VVS/VA-ritningar (PDF): "
                    "OCR-koddetektering + vektor-rördetektering + "
                    "mängdförteckning.")
    p.add_argument("input_pdf", help="CAD-exporterad PDF-ritning")
    p.add_argument("--output-dir", default="out", help="Utdatakatalog")
    p.add_argument("--page", type=int, default=0, help="Sidnummer (0-baserat)")
    p.add_argument("--config", help="JSON-konfigfil (överstyr standardvärden)")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-v", "--verbose", action="store_true")

    a = p.add_argument_group("Del A – OCR")
    a.add_argument("--dpi", type=int, help="Rastrerings-DPI (standard 450)")
    a.add_argument("--tile-size", type=int, help="OCR-rutstorlek i px")
    a.add_argument("--overlap", type=float, help="Rutöverlapp 0-1")
    a.add_argument("--psm", help="PSM-lägen, kommaseparerade (standard 11,6)")
    a.add_argument("--lang", help="Tesseract-språk (standard swe)")
    a.add_argument("--code-regex", help="Regex för rörkoder")
    a.add_argument("--force-ocr", action="store_true",
                   help="Kör OCR även om PDF:en har riktig text")
    a.add_argument("--no-ocr", action="store_true",
                   help="Hoppa över OCR (använd endast PDF-text)")
    a.add_argument("--exclude-zone", action="append", default=[],
                   metavar="x0,y0,x1,y1",
                   help="Exkluderingszon i PDF-punkter (upprepningsbar) – "
                        "för legend/titelblock/tabeller")

    b = p.add_argument_group("Del B – rördetektering")
    b.add_argument("--pipe-width", type=float,
                   help="Rörlinjernas bredd i pt (standard: auto via histogram)")
    b.add_argument("--pipe-color", metavar="R,G,B",
                   help="Rörlinjernas färg 0-1 (t.ex. 0,0,0)")
    b.add_argument("--chain-tol", type=float,
                   help="Ändpunktstolerans vid kedjning i pt (standard 1.5)")
    b.add_argument("--calibrate", action="store_true",
                   help="Skriv bara ut linjebredd-histogrammet och avsluta "
                        "(kalibreringsvy för --pipe-width)")

    d = p.add_argument_group("Del D – mängdning")
    d.add_argument("--scale", help='Skala, t.ex. "1:50" eller "56.69pt/m" '
                                   "(annars läses titelblock/skalstock)")
    d.add_argument("--vertikalhojd", metavar="SYS=M,...",
                   help='Vertikalhöjd per system, t.ex. '
                        '"Rör tappvatten=2.8,Spill- dagvatten=2.8"')
    d.add_argument("--facit", help="Facit-CSV att validera mot")
    d.add_argument("--layers", default="codes,pipes,links",
                   help="PDF-lager att rita (kommaseparerat: codes,pipes,links)")
    d.add_argument("--yes", action="store_true",
                   help="Hoppa över det interaktiva skalverifieringssteget")
    return p


def make_config(args: argparse.Namespace) -> Config:
    cfg = Config.from_file(args.config) if args.config else Config()
    if args.dpi:
        cfg.dpi = args.dpi
    if args.tile_size:
        cfg.tile_px = args.tile_size
    if args.overlap is not None:
        cfg.tile_overlap = args.overlap
    if args.psm:
        cfg.psm_modes = [int(v) for v in args.psm.split(",")]
    if args.lang:
        cfg.ocr_lang = args.lang
    if args.code_regex:
        cfg.code_regex = args.code_regex
    if args.force_ocr:
        cfg.force_ocr = True
    if args.no_ocr:
        cfg.skip_ocr = True
    for zone in args.exclude_zone:
        cfg.exclude_zones.append(parse_zone(zone))
    if args.pipe_width:
        cfg.pipe_width = args.pipe_width
    if args.pipe_color:
        cfg.pipe_color = tuple(float(v) for v in args.pipe_color.split(","))
    if args.chain_tol:
        cfg.chain_tol_pt = args.chain_tol
    if args.scale:
        cfg.scale = args.scale
    if args.vertikalhojd:
        cfg.vertical_heights.update(parse_vertical_heights(args.vertikalhojd))
    cfg.layers = [v.strip() for v in args.layers.split(",") if v.strip()]
    cfg.page = args.page
    return cfg


def confirm_scale(scale, chains, skip: bool) -> None:
    """Obligatoriskt verifieringssteg: visa uppmätt skala och en känd
    referenssträcka innan mängdförteckningen presenteras som färdig."""
    if not scale.known:
        return
    active = [c for c in chains if not c.excluded]
    ref = max(active, key=lambda c: c.length_pt, default=None)
    print("\n--- SKALVERIFIERING (måste bekräftas) ---")
    print(f"Metod: {scale.method}   Skaltext: {scale.scale_text or '-'}")
    print(f"Skalfaktor: {scale.pts_per_meter:.3f} PDF-punkter per meter")
    if ref is not None:
        print(f"Referens: längsta detekterade rörsträckan (#{ref.id}) är "
              f"{ref.length_pt:.0f} pt = {scale.to_meters(ref.length_pt):.1f} m "
              f"– rimligt för denna ritning?")
    for w in scale.warnings:
        print(f"VARNING: {w}")
    if skip or not sys.stdin.isatty():
        print("(verifiering ej interaktiv – kontrollera skalfliken i "
              "mängdförteckningen innan siffrorna används)\n")
        return
    answer = input("Är skalan korrekt? [J/n] ").strip().lower()
    if answer in ("n", "nej", "no"):
        print("Avbryter. Ange korrekt skala med --scale (t.ex. --scale 1:50).")
        sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s")

    input_pdf = Path(args.input_pdf)
    if not input_pdf.exists():
        log.error("Filen finns inte: %s", input_pdf)
        return 1
    cfg = make_config(args)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = input_pdf.stem

    if args.calibrate:
        doc = fitz.open(str(input_pdf))
        if not (0 <= cfg.page < len(doc)):
            log.error("Sida %d finns inte (PDF:en har %d sidor)",
                      cfg.page, len(doc))
            return 1
        data = extract_drawings(doc[cfg.page], cfg)
        doc.close()
        print(format_histogram(data))
        print("\nVälj rörklustrets bredd och kör igen med "
              "--pipe-width <bredd> (ev. --pipe-color R,G,B).")
        return 0

    try:
        outputs = run_pipeline(
            input_pdf, cfg, out_dir, facit=args.facit,
            on_scale=lambda scale, chains: confirm_scale(
                scale, chains, skip=args.yes))
    except ValueError as exc:
        log.error("%s", exc)
        return 1

    if outputs.validation_text:
        print("\n" + outputs.validation_text)
    print(f"\nKlart. Utdata i {out_dir}/ – läs {stem}_rapport.txt för "
          "kända begränsningar och osäkerhetsflaggor.")
    return 0
