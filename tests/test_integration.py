"""Integrationstest: en syntetisk ritnings-PDF byggs med PyMuPDF och körs
genom hela pipelinen (utan OCR – texten läggs in som riktig PDF-text, vilket
textlagerkontrollen då också ska upptäcka)."""

import fitz
import pytest

from mangdning.config import Config
from mangdning.linking import find_leader_candidates, link_codes_to_pipes
from mangdning.ocr_codes import collect_hits, extract_codes, inspect_text_layer
from mangdning.pipes import detect_pipes
from mangdning.quantify import build_quantities
from mangdning.scale import determine_scale

PTS_PER_M = 56.6929  # 1:50


def test_hela_pipelinen_pa_syntetisk_ritning(drawing_pdf):
    cfg = Config()
    cfg.skip_ocr = True  # riktig PDF-text i testfilen

    doc = fitz.open(str(drawing_pdf))
    page = doc[0]

    info = inspect_text_layer(page, cfg)
    assert not info.use_ocr  # texten är riktig => ingen OCR

    all_hits, _ = collect_hits(page, cfg)
    assert any(h.text == "S3-R8-75" for h in all_hits)

    codes = extract_codes(all_hits, cfg)
    code_texts = {c.raw_text for c in codes}
    assert {"S3-R8-75", "2xKV1-X31", "B7-GOLVBRUNN"} <= code_texts
    assert "SKALA" not in code_texts

    chains, data, pipe_width, _color = detect_pipes(page, cfg)
    assert pipe_width == pytest.approx(2.0, abs=0.1)
    active = [c for c in chains if not c.excluded]
    excluded = [c for c in chains if c.excluded]
    assert len(active) == 2      # två rörsträckor
    assert len(excluded) == 1    # ramlinjen bortfiltrerad
    lengths = sorted(c.length_pt for c in active)
    assert lengths[0] == pytest.approx(150.0, abs=1.0)
    assert lengths[1] == pytest.approx(300.0, abs=1.0)

    leaders = find_leader_candidates(data, pipe_width, chains, cfg)
    link_codes_to_pipes(codes, chains, leaders, cfg)
    s3 = next(c for c in codes if c.raw_text == "S3-R8-75")
    assert s3.linked_chain is not None
    kv = next(c for c in codes if c.raw_text == "2xKV1-X31")
    assert kv.linked_chain is not None  # närhetsfallback
    brunn = next(c for c in codes if c.raw_text == "B7-GOLVBRUNN")
    assert brunn.linked_chain is None   # punktkomponent

    scale = determine_scale(all_hits, cfg)
    assert scale.method == "titelblock"
    assert scale.pts_per_meter == pytest.approx(PTS_PER_M, abs=0.01)

    result = build_quantities(codes, chains, scale, {}, {}, cfg,
                              document="ritning.pdf")
    subjects = {r.subject for r in result.rows}
    assert "S3-R8-75" in subjects
    assert "B7-GOLVBRUNN" in subjects

    s3_row = next(r for r in result.rows if r.subject == "S3-R8-75")
    assert s3_row.langd_m == pytest.approx(300.0 / PTS_PER_M, abs=0.05)
    kv_row = next(r for r in result.rows if "KV1-X31" in r.subject)
    # Nx multiplicerar inte som standard – de två rören ritas var för sig
    assert kv_row.langd_m == pytest.approx(150.0 / PTS_PER_M, abs=0.05)
    assert "ej multiplicerad" in kv_row.kommentar
    brunn_row = next(r for r in result.rows if r.subject == "B7-GOLVBRUNN")
    assert brunn_row.antal == 1
    doc.close()


def test_output_filer_skrivs(drawing_pdf, tmp_path):
    from mangdning.cli import main

    out_dir = tmp_path / "out"
    rc = main([str(drawing_pdf), "--output-dir", str(out_dir),
               "--no-ocr", "--yes"])
    assert rc == 0
    stem = "ritning"
    for suffix in ("_markerad.pdf", "_koder.csv", "_mangder.xlsx",
                   "_mangder.csv", "_rapport.txt"):
        assert (out_dir / f"{stem}{suffix}").exists(), suffix

    rapport = (out_dir / f"{stem}_rapport.txt").read_text(encoding="utf-8")
    assert "Skala" in rapport
    assert "UPPSKATTNING" in rapport

    # markerad PDF ska ha fler ritobjekt än originalet
    marked = fitz.open(str(out_dir / f"{stem}_markerad.pdf"))
    assert len(marked[0].get_drawings()) > 5
    marked.close()


def test_kalibreringsvy(drawing_pdf, capsys):
    from mangdning.cli import main

    rc = main([str(drawing_pdf), "--calibrate"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "histogram" in out.lower()
    assert "2.0" in out
