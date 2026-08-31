"""Tester för Del D punkt 1: skalbestämning."""

import pytest

from mangdning.config import Config
from mangdning.models import BBox, OcrHit
from mangdning.scale import (determine_scale, parse_scale_arg,
                             ratio_to_pts_per_meter, scale_from_scale_bar,
                             scale_from_title_text)


def hit(text, x, y, w=30.0, h=8.0):
    return OcrHit(text, BBox(x, y, x + w, y + h), 90.0)


def test_ratio_till_punkter_per_meter():
    # 1:50 => 1 m = 20 mm på papper = 20/25.4*72 ≈ 56.69 pt
    assert ratio_to_pts_per_meter(50) == pytest.approx(56.6929, abs=0.01)
    assert ratio_to_pts_per_meter(100) == pytest.approx(28.3465, abs=0.01)


def test_parse_scale_arg():
    assert parse_scale_arg("1:50") == pytest.approx(56.6929, abs=0.01)
    assert parse_scale_arg("56.69pt/m") == pytest.approx(56.69)
    with pytest.raises(ValueError):
        parse_scale_arg("femtio")


def test_skaltext_nara_ordet_skala_foredras():
    hits = [
        hit("SKALA", 2000, 1500),
        hit("1:50", 2040, 1500),
        hit("1:400", 100, 100),  # t.ex. en situationsplan någon annanstans
    ]
    result = scale_from_title_text(hits)
    assert result is not None
    pts, text = result
    assert text == "1:50"
    assert pts == pytest.approx(56.6929, abs=0.01)


def test_skalstock_ger_punkter_per_meter():
    # skalstock "0 1 2 3 4 5 m" med 56.7 pt mellan siffrorna (skala 1:50)
    hits = [hit(str(i), 100 + i * 56.7, 1500, w=6.0) for i in range(6)]
    hits.append(hit("m", 100 + 5 * 56.7 + 15, 1500, w=8.0))
    pts = scale_from_scale_bar(hits)
    assert pts == pytest.approx(56.7, rel=0.01)


def test_skalstock_avvisar_ojamna_mellanrum():
    xs = [100, 150, 260, 300, 340, 380]  # ojämnt
    hits = [hit(str(i), x, 1500, w=6.0) for i, x in enumerate(xs)]
    hits.append(hit("m", 400, 1500, w=8.0))
    assert scale_from_scale_bar(hits) is None


def test_cli_skala_har_hogsta_prioritet():
    cfg = Config()
    cfg.scale = "1:100"
    hits = [hit("SKALA", 0, 0), hit("1:50", 40, 0)]
    result = determine_scale(hits, cfg)
    assert result.method == "cli"
    assert result.pts_per_meter == pytest.approx(28.3465, abs=0.01)


def test_varning_nar_titelblock_och_skalstock_skiljer_sig():
    cfg = Config()
    hits = [hit("SKALA", 2000, 100), hit("1:50", 2040, 100)]
    # skalstock som motsvarar 1:100 (28.3 pt/m) => konflikt
    hits += [hit(str(i), 100 + i * 28.35, 1500, w=6.0) for i in range(6)]
    hits.append(hit("m", 100 + 5 * 28.35 + 12, 1500, w=8.0))
    result = determine_scale(hits, cfg)
    assert result.method == "titelblock"
    assert any("olika svar" in w for w in result.warnings)


def test_okand_skala_ger_varning():
    result = determine_scale([hit("PLAN", 0, 0)], Config())
    assert not result.known
    assert result.method == "okänd"
    assert result.warnings
