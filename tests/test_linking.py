"""Tester för Del C: ledartrådsidentifiering och kod<->rör-koppling."""

from collections import Counter

from mangdning.config import Config
from mangdning.linking import find_leader_candidates, link_codes_to_pipes
from mangdning.models import BBox, CodeHit, Segment
from mangdning.pipes import DrawingData, build_chains


def seg(x1, y1, x2, y2, width=2.04, color=(0.0, 0.0, 0.0)):
    return Segment((float(x1), float(y1)), (float(x2), float(y2)), width, color)


def code(id_, text, x, y, w=40.0, h=8.0):
    return CodeHit(id=id_, raw_text=text, base_code=text, count=1,
                   bbox=BBox(x, y, x + w, y + h), conf=90.0)


def _setup(cfg):
    # Rörsträcka: horisontell linje y=200, x 0..300
    pipe_segments = [seg(0, 200, 150, 200), seg(150, 200, 300, 200)]
    chains = build_chains(pipe_segments, cfg)
    # Ledartråd: tunn diagonal från nära koden (100,110) ner till röret (140,199)
    leader_seg = seg(100, 110, 140, 199, width=0.48)
    data = DrawingData(segments=pipe_segments + [leader_seg])
    for s in data.segments:
        data.width_histogram[s.width] += 1
        data.width_color.setdefault(s.width, Counter())[s.color] += 1
    return chains, data


def test_ledartrad_identifieras_och_kopplar_kod_till_ror():
    cfg = Config()
    chains, data = _setup(cfg)
    leaders = find_leader_candidates(data, 2.04, chains, cfg)
    assert len(leaders) == 1

    c = code(0, "S3-R8-75", 60, 95)  # bbox slutar vid (100,103); tråden börjar (100,110)
    link_codes_to_pipes([c], chains, leaders, cfg)
    assert c.linked_chain == chains[0].id
    assert c.link_method == "leader"
    assert leaders[0].code_id == 0
    assert chains[0].linked_codes == [0]


def test_rorsegment_ar_inte_ledartradskandidater():
    cfg = Config()
    chains, data = _setup(cfg)
    leaders = find_leader_candidates(data, 2.04, chains, cfg)
    # bara den tunna diagonalen, inte de breda rörsegmenten
    assert all(l.width < 2.04 for l in leaders)


def test_axelparallella_tunna_linjer_ar_inte_ledartradar():
    cfg = Config()
    data = DrawingData(segments=[seg(0, 0, 50, 0, width=0.48)])  # horisontell
    leaders = find_leader_candidates(data, 2.04, [], cfg)
    assert leaders == []


def test_kod_langt_fran_allt_flaggas_som_okopplad():
    cfg = Config()
    chains, data = _setup(cfg)
    leaders = find_leader_candidates(data, 2.04, chains, cfg)
    far = code(1, "B7-GOLVBRUNN", 2000, 1500)
    link_codes_to_pipes([far], chains, leaders, cfg)
    assert far.linked_chain is None


def test_narhetsfallback_utan_ledartrad():
    cfg = Config()
    pipe_segments = [seg(0, 200, 300, 200)]
    chains = build_chains(pipe_segments, cfg)
    near = code(0, "KV1-X31", 100, 185)  # 7 pt över röret, ingen ledartråd
    link_codes_to_pipes([near], chains, [], cfg)
    assert near.linked_chain == chains[0].id
    assert near.link_method == "proximity"


def test_exkluderad_kod_kopplas_inte():
    cfg = Config()
    chains, data = _setup(cfg)
    leaders = find_leader_candidates(data, 2.04, chains, cfg)
    c = code(0, "S3-R8-75", 60, 95)
    c.excluded = True
    link_codes_to_pipes([c], chains, leaders, cfg)
    assert c.linked_chain is None
