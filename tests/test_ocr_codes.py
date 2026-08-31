"""Tester för Del A: regexfiltrering, dedup, Nx-notation och radparning."""

import re

import pytest

from mangdning.config import Config, DEFAULT_CODE_REGEX
from mangdning.models import BBox, OcrHit
from mangdning.ocr_codes import (dedup_hits, extract_codes,
                                 pair_dimension_lines, parse_nx)


CODE_RE = re.compile(DEFAULT_CODE_REGEX)


@pytest.mark.parametrize("text", [
    "S3-R8-75", "VG+1.46(L)", "KV2-X31", "2xVV1-X31", "B7-GOLVBRUNN",
    "S3-P2-160", "VVC1-X22", "D1-110",
])
def test_code_regex_matchar_riktiga_koder(text):
    assert CODE_RE.match(text), text


@pytest.mark.parametrize("text", [
    "SKALA", "FÖRKLARINGAR", "hej", "12345", "A", "PLAN", "160(L)", "75",
])
def test_code_regex_avvisar_icke_koder(text):
    assert not CODE_RE.match(text), text


def _hit(text, x, y, conf=90.0, w=40.0, h=8.0):
    return OcrHit(text, BBox(x, y, x + w, y + h), conf)


def test_dedup_slar_ihop_naraliggande_dubletter():
    hits = [_hit("S3-R8-110", 100, 100), _hit("S3-R8-110", 103, 102)]
    clusters = dedup_hits(hits, tol_pt=12.0)
    assert len(clusters) == 1
    assert len(clusters[0]) == 2


def test_dedup_kollapsar_inte_geometriskt_skilda_kluster():
    """Del A punkt 5: samma kodtext på många olika, långt ifrån varandra
    liggande platser får ALDRIG slås ihop bara för att texten är identisk.

    Råa träffar: 3 geometriskt urskiljbara kluster (varje kluster har flera
    råa träffar p.g.a. rutöverlapp/flera PSM-lägen). Efter dedup ska antalet
    matcha antalet kluster – inte kollapsa till 1.
    """
    raw_hits = [
        # kluster 1 vid (100, 100): 3 råa träffar
        _hit("S3-R8-110", 100, 100), _hit("S3-R8-110", 104, 101),
        _hit("S3-R8-110", 98, 103),
        # kluster 2 vid (900, 400): 2 råa träffar
        _hit("S3-R8-110", 900, 400), _hit("S3-R8-110", 905, 398),
        # kluster 3 vid (100, 1500): 2 råa träffar
        _hit("S3-R8-110", 100, 1500), _hit("S3-R8-110", 96, 1504),
    ]
    n_raw = len(raw_hits)
    clusters = dedup_hits(raw_hits, tol_pt=12.0)
    assert n_raw == 7
    assert len(clusters) == 3, (
        "dedup ska ge ett resultat per geometriskt kluster, inte per texttyp")
    assert sorted(len(c) for c in clusters) == [2, 2, 3]


def test_dedup_olika_text_pa_samma_plats_slas_inte_ihop():
    hits = [_hit("S3-R8-110", 100, 100), _hit("KV1-X31", 101, 101)]
    assert len(dedup_hits(hits, tol_pt=12.0)) == 2


def test_parse_nx():
    assert parse_nx("2xKV1-X31") == (2, "KV1-X31")
    assert parse_nx("5xVV1-X31") == (5, "VV1-X31")
    assert parse_nx("KV1-X31") == (1, "KV1-X31")
    # "x" mitt i en kod utan siffra före ska inte tolkas som Nx
    assert parse_nx("S3-R8-75") == (1, "S3-R8-75")


def test_nx_koder_bredvid_varandra_ar_olika_poster():
    """Del A punkt 8: olika Nx-koder horisontellt bredvid varandra är
    genuint olika koder och ska inte slås ihop trots närhet i sidled."""
    cfg = Config()
    hits = [_hit("2xKV1-X31", 100, 100), _hit("5xVV1-X31", 145, 100)]
    codes = extract_codes(hits, cfg)
    assert len(codes) == 2
    assert {(c.count, c.base_code) for c in codes} == {
        (2, "KV1-X31"), (5, "VV1-X31")}


def test_radparning_kod_plus_dimensionsrad():
    """Del A punkt 7: "S3-P2" med "160(L)" direkt under => S3-P2-160(L)."""
    cfg = Config()
    code_hit = _hit("S3-P2", 100, 100)
    dim_hit = _hit("160(L)", 102, 112)   # 4 pt gap under koden
    other = _hit("BRAVO", 500, 500)
    codes = extract_codes([code_hit, dim_hit, other], cfg)
    assert len(codes) == 1
    assert codes[0].dimension == "160(L)"
    assert codes[0].full_code == "S3-P2-160(L)"


def test_radparning_hoppar_over_kod_med_inbyggd_dimension():
    """"S3-P2-160" innehåller redan dimensionen => ingen parning."""
    cfg = Config()
    code_hit = _hit("S3-P2-160", 100, 100)
    below = _hit("75", 102, 112)
    codes = extract_codes([code_hit, below], cfg)
    assert len(codes) == 1
    assert codes[0].dimension is None
    assert codes[0].full_code == "S3-P2-160"


def test_radparning_kraver_horisontell_overlapp():
    cfg = Config()
    code_hit = _hit("S3-P2", 100, 100)
    far_right = _hit("160(L)", 300, 112)  # rätt höjd men ingen x-överlapp
    codes = extract_codes([code_hit, far_right], cfg)
    assert codes[0].dimension is None


def test_radparning_slar_inte_ihop_tva_staplade_koder():
    cfg = Config()
    upper = _hit("S3-P2", 100, 100)
    lower = _hit("KV1-X31", 100, 112)   # en egen kod, inte en dimensionsrad
    codes = extract_codes([upper, lower], cfg)
    assert len(codes) == 2
    assert all(c.dimension is None for c in codes)


def test_partiell_ocr_lasning_undertrycks():
    """"V1-X31" läst ovanpå "2xKV1-X31" (rutkant/PSM-variation) är en
    partiell läsning av samma text och ska tas bort."""
    cfg = Config()
    full = _hit("2xKV1-X31", 300, 190, w=60)
    partial = _hit("V1-X31", 325, 190, w=35)
    codes = extract_codes([full, partial], cfg)
    assert len(codes) == 1
    assert codes[0].raw_text == "2xKV1-X31"


def test_partiell_lasning_pa_annan_plats_behalls():
    """Samma delsträng långt bort är en egen riktig kod, inte en partiell."""
    cfg = Config()
    full = _hit("2xKV1-X31", 300, 190, w=60)
    elsewhere = _hit("V1-X31", 900, 800, w=35)
    codes = extract_codes([full, elsewhere], cfg)
    assert len(codes) == 2


def test_exkluderingszon():
    cfg = Config()
    cfg.exclude_zones.append((0, 0, 200, 200))
    inside = _hit("S3-R8-75", 100, 100)
    outside = _hit("S3-R8-75", 500, 500)
    codes = extract_codes([inside, outside], cfg)
    assert sum(1 for c in codes if c.excluded) == 1
    assert sum(1 for c in codes if not c.excluded) == 1
