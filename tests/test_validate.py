"""Tester för Del D punkt 9: validering mot facit."""

import textwrap

import pytest

from mangdning.models import ScaleResult
from mangdning.quantify import Aggregate, QuantityResult
from mangdning.validate import read_facit, validate_against_facit


FACIT_CSV = textwrap.dedent("""\
    Version;Document;Subject;Sorterings_siffror;Sidetikett;Color;Kommentarer;Längd;unit;Lager;Antal_VS;Vertikal_höjd_VS;Total_vertikalhöjd_VS;unit2;Kapitel_i_Sektionsdata_VS;Exportval_Vs;Byggdelsnummer;Kontroll_Vs
    ;plan.pdf;S3-R8-75;;Sida 1;#8000FF;;5,8;m;Spill- dagvatten;;;;;;;;
    ;plan.pdf;S3-R8-75;;Sida 1;#8000FF;;7,0;m;Spill- dagvatten;;;;;;;;
    ;plan.pdf;S3-R8-75;;Sida 1;#8000FF;;8,0;m;Spill- dagvatten;2;2,80;5,60;m;;;;
    ;plan.pdf;VV1-X31-16;;Sida 1;#FF0000;;12,5;m;Rör tappvatten;;;;;;;;
    ;plan.pdf;Markera;;Sida 1;#00FF00;;;;Spill- dagvatten;;;;;;;;
    ;plan.pdf;Markera;;Sida 1;#00FF00;;;;Spill- dagvatten;;;;;;;;
""")


def make_result(aggregates):
    return QuantityResult(rows=[], aggregates=aggregates,
                          scale=ScaleResult(56.69, "cli"), warnings=[],
                          count_checks=[])


def test_read_facit_grupperar_per_subject(tmp_path):
    path = tmp_path / "facit.csv"
    path.write_text(FACIT_CSV, encoding="utf-8")
    facit = read_facit(path)
    assert set(facit) == {"S3-R8-75", "VV1-X31-16", "Markera"}
    s3 = facit["S3-R8-75"]
    assert s3.n_length_rows == 3
    assert s3.total_length == 5.8 + 7.0 + 8.0
    assert s3.antal_vs == 2
    assert facit["Markera"].n_points == 2


def test_avvikelserapport_inom_och_utom_tolerans(tmp_path):
    path = tmp_path / "facit.csv"
    path.write_text(FACIT_CSV, encoding="utf-8")
    facit = read_facit(path)

    ours = [
        Aggregate("S3-R8-75", "Spill- dagvatten", "#8000FF",
                  n_strackor=3, total_langd_m=20.8),          # exakt
        Aggregate("VV1-X31-16", "Rör tappvatten", "#FF0000",
                  n_strackor=1, total_langd_m=10.0),          # -20 %
    ]
    report = validate_against_facit(make_result(ours), facit)
    text = report.text()
    assert "S3-R8-75: facit 3 sträckor / 20,8 m totalt" in text
    assert "vårt resultat 3 sträckor / 20,8 m totalt" in text
    line = next(l for l in report.lines if l.startswith("VV1-X31-16"))
    assert "-20 %" in line
    assert "[AVVIKELSE]" in line
    assert report.n_codes_compared == 3
    assert report.n_codes_ok == 1  # bara S3-R8-75 inom tolerans


def test_kod_som_saknas_hos_oss_rapporteras(tmp_path):
    path = tmp_path / "facit.csv"
    path.write_text(FACIT_CSV, encoding="utf-8")
    facit = read_facit(path)
    report = validate_against_facit(make_result([]), facit)
    assert any("SAKNAS helt" in l for l in report.lines)


def test_kod_som_bara_finns_hos_oss_rapporteras(tmp_path):
    path = tmp_path / "facit.csv"
    path.write_text(FACIT_CSV, encoding="utf-8")
    facit = read_facit(path)
    ours = [Aggregate("5S-R8-75", "Okänt system", "#123456",
                      n_strackor=1, total_langd_m=3.0)]  # OCR-feltolkning S/5
    report = validate_against_facit(make_result(ours), facit)
    assert any("saknas i facit" in l for l in report.lines)
    assert any("5S-R8-75" in l for l in report.lines)


def test_las_facit_fran_xlsx(tmp_path):
    """Facit levereras i praktiken som XLSX från mängdningsverktyget."""
    from openpyxl import Workbook

    path = tmp_path / "facit.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["Version", "Document", "Subject", "Sorterings_siffror",
               "Sidetikett", "Color", "Kommentarer", "Längd", "unit", "Lager",
               "Antal_VS", "Vertikal_höjd_VS", "Total_vertikalhöjd_VS"])
    ws.append(["1.0", "p.pdf", "S3-R8-75", "", "[1] p", "#8000FF", "5,8 m",
               "5,8", "m", "Spill- dagvatten", "", "0,00", "0,00"])
    ws.append(["1.0", "p.pdf", "S3-R8-75", "", "[1] p", "#8000FF", "7,0 m",
               "7,0", "m", "Spill- dagvatten", "", "0,00", "0,00"])
    ws.append(["1.0", "p.pdf", "S3-R8-75 Vertikal", "", "[1] p", "#8000FF",
               "2", "", "", "Spill- dagvatten", "2,00", "2,80", "5,60"])
    ws.append(["1.0", "p.pdf", "Markera", "", "[1] p", "#FF0080", "", "", "",
               "", "", "", "0,00"])
    wb.save(str(path))

    facit = read_facit(path)
    assert facit["S3-R8-75"].n_length_rows == 2
    assert facit["S3-R8-75"].total_length == pytest.approx(12.8)
    # vertikalrader räknas som vertikaler, inte som punktmarkeringar
    assert facit["S3-R8-75 Vertikal"].antal_vs == 2
    assert facit["S3-R8-75 Vertikal"].n_points == 0
    assert facit["S3-R8-75 Vertikal"].n_vertical_rows == 1
    assert facit["Markera"].n_points == 1


def test_xlsx_med_numeriska_celler(tmp_path):
    """XLSX kan ge riktiga tal i stället för strängar med decimalkomma."""
    from openpyxl import Workbook

    path = tmp_path / "facit.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.append(["Subject", "Längd", "Lager", "Antal_VS"])
    ws.append(["KV1-X31-16", 8.7, "Rör tappvatten", None])
    ws.append(["KV1-X31-16", 8.7, "Rör tappvatten", None])
    wb.save(str(path))

    facit = read_facit(path)
    assert facit["KV1-X31-16"].n_length_rows == 2
    assert facit["KV1-X31-16"].total_length == pytest.approx(17.4)
