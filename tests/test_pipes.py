"""Tester för Del B: kedjning, klusterval och ramfiltrering."""

from collections import Counter

import fitz
import pytest

from mangdning.config import Config
from mangdning.models import Segment
from mangdning.pipes import (DrawingData, build_chains, chain_segments,
                             filter_pipe_segments, flag_frame_chains,
                             select_pipe_cluster, select_pipe_clusters)


def seg(x1, y1, x2, y2, width=2.04, color=(0.0, 0.0, 0.0)):
    return Segment((float(x1), float(y1)), (float(x2), float(y2)), width, color)


def test_kedjning_slar_ihop_korta_segment_i_rad():
    """Rörlinjer ritas som många korta segment; ändpunkter inom toleransen
    ska kedjas till EN sammanhängande rörsträcka."""
    segments = [seg(0, 0, 10, 0), seg(10.5, 0, 20, 0), seg(20, 0.8, 20, 30)]
    groups = chain_segments(segments, tol_pt=1.5)
    assert len(groups) == 1
    assert len(groups[0][0]) == 3


def test_kedjning_haller_isar_avlagsna_segment():
    segments = [seg(0, 0, 10, 0), seg(100, 100, 110, 100)]
    groups = chain_segments(segments, tol_pt=1.5)
    assert len(groups) == 2


def test_kedjning_respekterar_tolerans():
    segments = [seg(0, 0, 10, 0), seg(15, 0, 25, 0)]  # 5 pt gap > 1.5 tol
    assert len(chain_segments(segments, tol_pt=1.5)) == 2
    assert len(chain_segments(segments, tol_pt=6.0)) == 1


def test_streckat_ror_bryggas_och_luckorna_raknas_som_langd():
    """AutoCAD exporterar streckade rör som fristående korta segment. Utan
    bryggning mäts bara "bläcket"; med bryggning blir det en sträcka vars
    längd även innehåller luckorna."""
    # 5 streck à 6 pt med 4 pt luckor => ritat 30 pt, verklig längd 46 pt
    segments = [seg(i * 10, 0, i * 10 + 6, 0) for i in range(5)]
    utan = chain_segments(segments, tol_pt=1.5)
    assert len(utan) == 5  # varje streck för sig

    grupper = chain_segments(segments, tol_pt=1.5, dash_gap_pt=9.0)
    assert len(grupper) == 1
    group, bridged = grupper[0]
    assert len(group) == 5
    assert bridged == pytest.approx(16.0)  # 4 luckor à 4 pt
    assert sum(s.length for s in group) + bridged == pytest.approx(46.0)


def test_bryggning_hoppar_over_parallella_grannror():
    """Två parallella rör bredvid varandra får INTE bryggas ihop – luckan
    går på tvären mot linjeriktningen."""
    segments = [seg(0, 0, 40, 0), seg(0, 5, 40, 5)]  # 5 pt isär i sidled
    grupper = chain_segments(segments, tol_pt=1.5, dash_gap_pt=9.0)
    assert len(grupper) == 2


def test_bryggning_hoppar_over_vinkelrata_segment():
    segments = [seg(0, 0, 20, 0), seg(24, 0, 24, 20)]  # 4 pt gap men 90 grader
    grupper = chain_segments(segments, tol_pt=1.5, dash_gap_pt=9.0)
    assert len(grupper) == 2


def test_build_chains_raknar_med_bryggad_langd():
    cfg = Config()
    cfg.dash_gap_pt = 9.0
    segments = [seg(i * 10, 0, i * 10 + 6, 0) for i in range(5)]
    chains = build_chains(segments, cfg)
    assert len(chains) == 1
    assert chains[0].length_pt == pytest.approx(46.0)


def test_build_chains_langd_och_endpoints():
    cfg = Config()
    segments = [seg(0, 0, 10, 0), seg(10, 0, 10, 20)]
    chains = build_chains(segments, cfg)
    assert len(chains) == 1
    assert chains[0].length_pt == pytest.approx(30.0)
    # fria rörändar = grad-1-noder: (0,0) och (10,20)
    assert sorted(chains[0].endpoints) == [(0.0, 0.0), (10.0, 20.0)]


def _drawing_data(segments):
    data = DrawingData(segments=segments)
    for s in segments:
        data.width_histogram[s.width] += 1
        data.width_color.setdefault(s.width, Counter())[s.color] += 1
    return data


def test_klusterval_valjer_sammanhangande_linjer_och_sorterar_bort_skraffering():
    """Rör löper sammanhängande längs sin linje; väggar/skraffering är
    korta streck utspridda längs samma linjer med stora tomrum. Det är den
    skillnaden – inte bredden – som skiljer dem åt."""
    # rör: sammanhängande sträcka av streck i rad
    pipes = [seg(i * 10, 50, i * 10 + 9, 50, width=2.04) for i in range(80)]
    # skraffering: korta streck glest utspridda längs samma linjer
    hatch = [seg(i * 60, 0, i * 60 + 3, 0, width=0.72) for i in range(500)]
    data = _drawing_data(hatch + pipes)
    clusters = select_pipe_clusters(data, Config())
    assert [w for w, _ in clusters] == [pytest.approx(2.04)]


def test_klusterval_tar_med_flera_rorbredder():
    """En ritning kan rita olika system med olika penna (spillvatten 2,04,
    tappvatten 1,44) – båda ska med."""
    spill = [seg(i * 10, 50, i * 10 + 9, 50, width=2.04) for i in range(80)]
    tapp = [seg(i * 10, 90, i * 10 + 9, 90, width=1.44) for i in range(80)]
    hatch = [seg(i * 60, 0, i * 60 + 3, 0, width=0.72) for i in range(500)]
    data = _drawing_data(hatch + spill + tapp)
    widths = [w for w, _ in select_pipe_clusters(data, Config())]
    assert widths == [pytest.approx(2.04), pytest.approx(1.44)]


def test_coverage_ratio_skiljer_ror_fran_skraffering():
    from mangdning.pipes import coverage_ratio
    pipe_run = [seg(i * 10, 0, i * 10 + 9, 0) for i in range(20)]
    scattered = [seg(i * 100, 0, i * 100 + 3, 0) for i in range(20)]
    assert coverage_ratio(pipe_run) > 0.8
    assert coverage_ratio(scattered) < 0.2


def test_klusterval_konfigurerad_bredd_vinner():
    cfg = Config()
    cfg.pipe_width = 0.72
    thin = [seg(i, 0, i + 1, 0, width=0.36) for i in range(100)]
    data = _drawing_data(thin + [seg(0, 5, 10, 5, width=0.72)])
    width, _ = select_pipe_cluster(data, cfg)
    assert width == pytest.approx(0.72)


def test_filter_pipe_segments_pa_bredd_och_farg():
    cfg = Config()
    segments = [
        seg(0, 0, 10, 0, width=2.04),
        seg(0, 5, 10, 5, width=2.04, color=(1.0, 0.0, 0.0)),  # fel färg
        seg(0, 10, 10, 10, width=0.36),                        # fel bredd
    ]
    data = _drawing_data(segments)
    kept = filter_pipe_segments(data, 2.04, (0.0, 0.0, 0.0), cfg)
    assert len(kept) == 1


def test_ramfiltrering_flaggar_langa_raka_ensamma_linjer():
    cfg = Config()
    frame = build_chains([seg(0, 0, 2000, 0)], cfg)
    pipes = build_chains(
        [seg(0, 50, 30, 50), seg(30, 50, 30, 80), seg(30, 80, 60, 80)], cfg)
    chains = frame + pipes
    flag_frame_chains(chains, fitz.Rect(0, 0, 2384, 1684), cfg)
    assert chains[0].excluded and "ram" in chains[0].excluded_reason
    assert not chains[1].excluded


def test_streckad_linje_slas_ihop_till_hel_linje():
    """AutoCAD exporterar streckade rör som fristående korta streck. Summan
    av strecken är bara "bläcket"; sträckans verkliga längd innehåller även
    luckorna."""
    from mangdning.pipes import merge_collinear_runs
    # 5 streck à 6 pt med 4 pt luckor => ritat 30 pt, verklig längd 46 pt
    segments = [seg(i * 10, 0, i * 10 + 6, 0) for i in range(5)]
    assert sum(s.length for s in segments) == pytest.approx(30.0)

    merged = merge_collinear_runs(segments, max_gap_pt=5.0)
    assert len(merged) == 1
    assert merged[0].length == pytest.approx(46.0)


def test_sammanslagning_haller_isar_parallella_ror():
    """Två rör bredvid varandra ligger på olika linjer och får aldrig slås
    ihop, hur nära de än går."""
    from mangdning.pipes import merge_collinear_runs
    segments = [seg(0, 0, 40, 0), seg(0, 3, 40, 3)]
    merged = merge_collinear_runs(segments, max_gap_pt=5.0)
    assert len(merged) == 2
    assert sum(s.length for s in merged) == pytest.approx(80.0)


def test_sammanslagning_haller_isar_skilda_ror_pa_samma_linje():
    """Två rör långt ifrån varandra på samma linje är skilda sträckor."""
    from mangdning.pipes import merge_collinear_runs
    segments = [seg(0, 0, 40, 0), seg(200, 0, 240, 0)]
    merged = merge_collinear_runs(segments, max_gap_pt=5.0)
    assert len(merged) == 2


def test_sammanslagning_avstangd_med_noll():
    from mangdning.pipes import merge_collinear_runs
    segments = [seg(i * 10, 0, i * 10 + 6, 0) for i in range(5)]
    assert len(merge_collinear_runs(segments, max_gap_pt=0.0)) == 5


def test_forgreningar_delar_kedjan_i_grenar():
    """Ett T-format nät ska bli tre sträckor (som i en manuell mängdning),
    inte en enda lång kedja rakt genom korsningen."""
    segments = [
        seg(0, 0, 100, 0),      # vänster gren
        seg(100, 0, 200, 0),    # höger gren (kollinjär fortsättning!)
        seg(100, 0, 100, 80),   # avstick nedåt
    ]
    cfg = Config()
    cfg.dash_gap_pt = 0.0   # ingen streck-sammanslagning i testet
    chains = build_chains(segments, cfg)
    assert len(chains) == 3
    assert sorted(round(c.length_pt) for c in chains) == [80, 100, 100]


def test_avstick_mitt_pa_ett_segment_klipper_det():
    """Avsticket ansluter mitt på ett långt segment – segmentet ska klippas
    vid anslutningen så att grenarna blir 100+100+80, inte 200+80."""
    segments = [
        seg(0, 0, 200, 0),      # ett obrutet segment
        seg(100, 0, 100, 80),   # avstick mitt på
    ]
    cfg = Config()
    cfg.dash_gap_pt = 0.0
    chains = build_chains(segments, cfg)
    assert len(chains) == 3
    assert sorted(round(c.length_pt) for c in chains) == [80, 100, 100]


def test_rak_ledning_utan_avstick_delas_inte():
    segments = [seg(0, 0, 100, 0), seg(100, 0, 200, 0), seg(200, 0, 200, 50)]
    cfg = Config()
    cfg.dash_gap_pt = 0.0
    chains = build_chains(segments, cfg)
    assert len(chains) == 1   # hörn är ingen förgrening
    assert chains[0].length_pt == pytest.approx(250.0)
