"""Delade testfixturer."""

import fitz
import pytest


def make_drawing_pdf(path):
    """Syntetisk A3-ritning: två rörsträckor (bredd 2.0), en ledartråd,
    kodtexter, skaltext och en lång ramlinje."""
    doc = fitz.open()
    page = doc.new_page(width=1190, height=842)  # A3 liggande

    # Varje linje ritas som en egen path (page.draw_line) – som segmenterad
    # CAD-export. OBS: Shape med flera draw_line per finish skapar
    # kopplingssegment mellan linjerna och duger inte som testdata.
    def line(p1, p2, width):
        page.draw_line(fitz.Point(*p1), fitz.Point(*p2),
                       color=(0, 0, 0), width=width)

    # Rörsträcka 1: L-form av många korta segment, total 300 pt ≈ 5.29 m
    for x0, x1 in ((100, 150), (150, 200), (200, 250), (250, 300)):
        line((x0, 400), (x1, 400), 2.0)
    for y0, y1 in ((400, 450), (450, 500)):
        line((300, y0), (300, y1), 2.0)
    # Rörsträcka 2: separat, 150 pt i tre segment
    for x0, x1 in ((600, 650), (650, 700), (700, 750)):
        line((x0, 300), (x1, 300), 2.0)
    # Ledartråd (tunn diagonal) från kodtexten mot rörsträcka 1
    line((168, 352), (220, 398), 0.4)
    # Tunna streck (simulerar textkonturer) så rörbredden blir "näst vanligast"
    for i in range(30):
        line((50 + i, 50), (50 + i, 55), 0.4)
    # Ramlinje: lång, rak, ensam
    line((20, 20), (1170, 20), 2.0)

    page.insert_text(fitz.Point(130, 348), "S3-R8-75", fontsize=8)
    page.insert_text(fitz.Point(600, 290), "2xKV1-X31", fontsize=8)
    page.insert_text(fitz.Point(900, 700), "B7-GOLVBRUNN", fontsize=8)
    page.insert_text(fitz.Point(950, 820), "SKALA", fontsize=8)
    page.insert_text(fitz.Point(990, 820), "1:50", fontsize=8)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def drawing_pdf(tmp_path):
    return make_drawing_pdf(tmp_path / "ritning.pdf")
