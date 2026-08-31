"""Tester för legendläsningen (Del D punkt 4 och 7)."""

from mangdning.config import Config
from mangdning.legend import (code_prefix, full_prefix, parse_legend,
                              system_for_code)
from mangdning.models import BBox, OcrHit


def hit(text, x, y, w=None, h=8.0):
    w = w if w is not None else len(text) * 5.0
    return OcrHit(text, BBox(x, y, x + w, y + h), 90.0)


def test_prefix():
    assert full_prefix("KV1-X31") == "KV1"
    assert code_prefix("KV1-X31") == "KV"
    assert code_prefix("S3-R8-75") == "S"


def test_parse_legend_laser_system_och_antal():
    hits = [
        hit("FÖRKLARINGAR", 2000, 100),
        hit("SYSTEM", 2000, 130), hit("TAPPVATTEN", 2040, 130),
        hit("KV1-X31", 2000, 150), hit("TAPPKALLVATTEN", 2050, 150),
        hit("VV1-X31", 2000, 165), hit("TAPPVARMVATTEN", 2050, 165),
        hit("SYSTEM", 2000, 200), hit("SPILLVATTEN", 2040, 200),
        hit("S3-R8", 2000, 220), hit("SPILLEDNING", 2040, 220),
        hit("B1-GOLVBRUNN", 2000, 240), hit("300×300", 2070, 240),
        hit("(8st)", 2115, 240),
    ]
    cfg = Config()
    prefix_map, counts, legend_bbox = parse_legend(hits, cfg)
    assert legend_bbox is not None
    assert prefix_map.get("KV1") == "Rör tappvatten"
    assert prefix_map.get("VV1") == "Rör tappvatten"
    assert prefix_map.get("S3") == "Spill- dagvatten"
    assert counts.get("B1-GOLVBRUNN") == 8


def test_system_for_code_anvander_legend_fore_konfig():
    cfg = Config()
    # legenden säger (hypotetiskt) att S-koder är värme – legenden ska vinna
    prefix_map = {"S": "Rör värme"}
    assert system_for_code("S3-R8-75", prefix_map, cfg) == "Rör värme"
    # utan legendträff: konfig-fallback
    assert system_for_code("S3-R8-75", {}, cfg) == "Spill- dagvatten"
