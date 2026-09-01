"""Tester för tolkningen av CAD-lagernamn (svensk standard SB11/BSAB)."""

import pytest

from mangdning.cadlayers import (classify_layers, discipline, is_pipe_layer,
                                 layer_name, system_for_layer,
                                 system_id_for_layer)


def test_lagernamn_utan_xref_prefix():
    assert layer_name("268140-W-50-P-A-00|V-53BB-FE--S3-") == "V-53BB-FE--S3-"
    assert layer_name("V-53BB-FE--S3-") == "V-53BB-FE--S3-"
    assert layer_name(None) == ""


def test_disciplin():
    assert discipline("268140|V-53BB-FE--S3-") == "V"
    assert discipline("HUS A|K-27B---EI_") == "K"
    assert discipline("A-------EXN") == "A"


@pytest.mark.parametrize("layer", [
    "268140-W-50-P-A-00|V-53BB-FE--S3-",
    "268140-W-50-P-A-00|V-52BB-FE--V1-",
    "268140-W-50-P-A-00|V-52BC-FE--V1-",
    "V-52B--FE--V1--",
])
def test_ror_ar_vvs_ledningslager(layer):
    assert is_pipe_layer(layer)


@pytest.mark.parametrize("layer", [
    "HUS A - GRUNDPLAN|K-27B---EI_",       # konstruktion (vägg)
    "A-------EXN",                          # arkitekt
    "268140-W-50-P-A-00|V-53BB--T--S3--",   # VVS men TEXT
    "268140-W-50-P-A-00|V-52B---T--V1--",   # VVS men text
    "Kristianstad_logo_cmyk vit text$0$Layer 1",
    "0",
])
def test_ovriga_lager_ar_inte_ror(layer):
    assert not is_pipe_layer(layer)


def test_system_ur_byggdelskod():
    assert system_for_layer("p|V-53BB-FE--S3-") == "Spill- dagvatten"
    assert system_for_layer("p|V-52BB-FE--V1-") == "Rör tappvatten"
    assert system_for_layer("p|V-56----S-N") == "Rör värme"


def test_systembeteckning():
    assert system_id_for_layer("p|V-53BB-FE--S3-") == "S3"
    assert system_id_for_layer("p|V-52BB-FE--V1-") == "V1"


def test_klassificering_delar_upp_lagren():
    lengths = {
        "p|V-53BB-FE--S3-": 6600.0,
        "p|V-52BB-FE--V1-": 960.0,
        "HUS A|K-27B---EI_": 12000.0,
        "p|V-53BB--T--S3--": 34000.0,
    }
    pipes, systems = classify_layers(lengths)
    assert set(pipes) == {"p|V-53BB-FE--S3-", "p|V-52BB-FE--V1-"}
    assert systems["p|V-53BB-FE--S3-"] == "Spill- dagvatten"
    assert systems["p|V-52BB-FE--V1-"] == "Rör tappvatten"


def test_eget_monster_for_ritningar_utanfor_standarden():
    lengths = {"PIPES_MAIN": 100.0, "WALLS": 500.0}
    pipes, _ = classify_layers(lengths, pattern=r"^PIPES")
    assert pipes == ["PIPES_MAIN"]
