"""Tester för Del D: radnivå per rörsträcka, Nx-multiplikation,
punktkomponenter, vertikaler, systemkategorisering och färgkodning."""

import pytest

from mangdning.colors import color_for_code
from mangdning.config import Config
from mangdning.legend import system_for_code
from mangdning.models import BBox, CodeHit, PipeChain, ScaleResult, Segment
from mangdning.quantify import UNLINKED_SUBJECT, build_quantities


PTS_PER_M = 56.6929  # 1:50


def make_scale():
    return ScaleResult(pts_per_meter=PTS_PER_M, method="cli", scale_text="1:50")


def make_chain(id_, length_pt, linked=None, vertical=0):
    return PipeChain(
        id=id_, segments=[Segment((0, 0), (length_pt, 0), 2.04, (0, 0, 0))],
        points=[(0, 0), (length_pt, 0)], length_pt=length_pt,
        bbox=BBox(0, 0, length_pt, 1), endpoints=[(0, 0), (length_pt, 0)],
        linked_codes=list(linked or []), vertical_symbols=vertical)


def make_code(id_, text, count=1, linked_chain=None, dimension=None):
    c = CodeHit(id=id_, raw_text=text, base_code=text.split("x")[-1]
                if "x" in text[:3] else text,
                count=count, bbox=BBox(0, 0, 40, 8), conf=90.0,
                dimension=dimension)
    c.linked_chain = linked_chain
    return c


def test_en_rad_per_rorstracka_inte_summerat_per_kod():
    """Del D punkt 2: samma kod på tre olika sträckor => TRE rader."""
    cfg = Config()
    codes = [make_code(i, "S3-R8-75", linked_chain=i) for i in range(3)]
    chains = [make_chain(i, 100 * (i + 1), linked=[i]) for i in range(3)]
    result = build_quantities(codes, chains, make_scale(), {}, {}, cfg)
    pipe_rows = [r for r in result.rows if r.langd_m is not None]
    assert len(pipe_rows) == 3
    assert all(r.subject == "S3-R8-75" for r in pipe_rows)
    lengths = sorted(r.langd_m for r in pipe_rows)
    assert lengths == [pytest.approx(100 * (i + 1) / PTS_PER_M, abs=0.01)
                       for i in range(3)]
    # aggregatet finns som separat vy
    agg = next(a for a in result.aggregates if a.subject == "S3-R8-75")
    assert agg.n_strackor == 3
    assert agg.total_langd_m == pytest.approx(600 / PTS_PER_M, abs=0.05)


def test_nx_kod_multiplicerar_langden():
    """Del D punkt 5: "2xKV1-X31" på en 10 m-sträcka => 20 m i mängdningen."""
    cfg = Config()
    length_pt = 10 * PTS_PER_M
    codes = [make_code(0, "2xKV1-X31", count=2, linked_chain=0)]
    codes[0].base_code = "KV1-X31"
    chains = [make_chain(0, length_pt, linked=[0])]
    result = build_quantities(codes, chains, make_scale(), {}, {}, cfg)
    row = next(r for r in result.rows if r.langd_m is not None)
    assert row.langd_m == pytest.approx(20.0, abs=0.05)
    assert "parallella" in row.kommentar


def test_punktkomponenter_ar_forstklassiga_rader():
    """Del D punkt 7: okopplade koder blir punktposter med antal."""
    cfg = Config()
    codes = [make_code(0, "B7-GOLVBRUNN"), make_code(1, "B7-GOLVBRUNN")]
    result = build_quantities(codes, [], make_scale(), {}, {}, cfg)
    point_rows = [r for r in result.rows if r.antal is not None]
    assert len(point_rows) == 2
    assert all(r.antal == 1 for r in point_rows)
    agg = next(a for a in result.aggregates if a.subject == "B7-GOLVBRUNN")
    assert agg.n_punkter == 2


def test_vertikaler_blir_egna_rader_som_i_facit():
    """Del D punkt 6: vertikala rörfall redovisas som EGNA rader med
    Subject "<kod> Vertikal" och Antal_VS × Vertikal_höjd_VS =
    Total_vertikalhöjd_VS – aldrig som längd, och aldrig som kolumner på
    rörraden (så gör facit)."""
    cfg = Config()
    cfg.vertical_heights["Spill- dagvatten"] = 2.8
    codes = [make_code(0, "S3-R8-75", linked_chain=0)]
    chains = [make_chain(0, 100, linked=[0], vertical=2)]
    result = build_quantities(codes, chains, make_scale(), {}, {}, cfg)

    pipe_row = next(r for r in result.rows if r.subject == "S3-R8-75")
    assert pipe_row.langd_m is not None
    assert pipe_row.antal_vs is None  # vertikalen ligger inte på rörraden

    vert = next(r for r in result.rows if r.subject == "S3-R8-75 Vertikal")
    assert vert.langd_m is None      # vertikaler har antal, inte längd
    assert vert.antal_vs == 2
    assert vert.vertikal_hojd_m == pytest.approx(2.8)
    assert vert.total_vertikalhojd_m == pytest.approx(5.6)
    assert "ANTAGANDE" in vert.kommentar
    assert vert.color == pipe_row.color  # samma färg som sin rörkod
    assert any("verifieras" in w for w in result.warnings)


def test_okopplad_rorstracka_flaggas():
    cfg = Config()
    chains = [make_chain(0, 100)]
    result = build_quantities([], chains, make_scale(), {}, {}, cfg)
    row = result.rows[0]
    assert row.subject == UNLINKED_SUBJECT
    assert "okopplad" in row.kommentar


def test_systemkategori_fran_legendens_prefix():
    cfg = Config()
    prefix_map = {"KV1": "Rör tappvatten", "S": "Spill- dagvatten"}
    assert system_for_code("KV1-X31", prefix_map, cfg) == "Rör tappvatten"
    assert system_for_code("S3-R8-75", prefix_map, cfg) == "Spill- dagvatten"


def test_systemkategori_fallback_till_konfig():
    cfg = Config()
    assert system_for_code("VVC1-X22", {}, cfg) == "Rör tappvatten"
    assert system_for_code("D1-110", {}, cfg) == "Spill- dagvatten"
    assert system_for_code("ZZ9-QQQ", {}, cfg) == "Okänt system"


def test_stabil_farg_per_kod():
    """Del D punkt 3: samma kod => alltid samma hexfärg; olika koder skiljer."""
    assert color_for_code("S3-R8-75") == color_for_code("S3-R8-75")
    assert color_for_code("S3-R8-75") != color_for_code("VV1-X31-16")
    assert color_for_code("S3-R8-75").startswith("#")
    assert len(color_for_code("S3-R8-75")) == 7


def test_antal_sanity_check_mot_legend():
    cfg = Config()
    codes = [make_code(0, "B1-GOLVBRUNN")]
    result = build_quantities(codes, [], make_scale(), {},
                              {"B1-GOLVBRUNN": 8}, cfg)
    assert any("AVVIKELSE" in c for c in result.count_checks)
    codes = [make_code(i, "B1-GOLVBRUNN") for i in range(8)]
    result = build_quantities(codes, [], make_scale(), {},
                              {"B1-GOLVBRUNN": 8}, cfg)
    assert any("[OK]" in c for c in result.count_checks)


def test_okand_skala_ger_tydlig_varning():
    cfg = Config()
    result = build_quantities([], [make_chain(0, 100)],
                              ScaleResult(None, "okänd"), {}, {}, cfg)
    assert any("SKALA OKÄND" in w for w in result.warnings)
