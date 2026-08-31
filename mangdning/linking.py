"""Del C – koppla ihop kod och rörsträcka via ledartrådar.

En rörkod sitter i regel fäst i en tunn ledartråd (diagonal linje, annan
bredd än rörlinjerna) som pekar från textens bounding box mot en punkt på
en rörsträcka. Koder utan koppling flaggas "ej kopplad till rör" – de
används dels för legendfiltrering, dels visas separat i output.
"""

from __future__ import annotations

import logging
import math

from .config import Config
from .models import (CodeHit, Leader, PipeChain, Point, Segment,
                     point_segment_distance)
from .pipes import DrawingData

log = logging.getLogger(__name__)


def _is_diagonal(p1: Point, p2: Point, min_deg: float) -> bool:
    dx = abs(p2[0] - p1[0])
    dy = abs(p2[1] - p1[1])
    if dx == 0.0 and dy == 0.0:
        return False
    ang = math.degrees(math.atan2(min(dx, dy), max(dx, dy)))
    return ang >= min_deg


def find_leader_candidates(data: DrawingData, pipe_width: float,
                           chains: list[PipeChain], cfg: Config) -> list[Leader]:
    """Identifiera ledartrådar: smalare än rörlinjerna, diagonala, lagom
    långa, och INTE en del av den hopkedjade rörgeometrin."""
    pipe_segs: set[tuple[Point, Point]] = set()
    for chain in chains:
        for s in chain.segments:
            pipe_segs.add((s.p1, s.p2))

    leaders: list[Leader] = []
    for seg in data.segments:
        if seg.width >= pipe_width * cfg.leader_max_width_ratio:
            continue
        if (seg.p1, seg.p2) in pipe_segs:
            continue
        if not (cfg.leader_min_len_pt <= seg.length <= cfg.leader_max_len_pt):
            continue
        if not _is_diagonal(seg.p1, seg.p2, cfg.diagonal_min_deg):
            continue
        leaders.append(Leader(len(leaders), seg.p1, seg.p2, seg.width))
    log.info("Ledartrådskandidater: %d", len(leaders))
    return leaders


def _nearest_chain(p: Point, chains: list[PipeChain], max_dist: float
                   ) -> tuple[PipeChain | None, float]:
    best: PipeChain | None = None
    best_d = max_dist
    for chain in chains:
        if chain.excluded:
            continue
        bb = chain.bbox.expanded(max_dist)
        if not bb.contains(p):
            continue
        for s in chain.segments:
            d = point_segment_distance(p, s.p1, s.p2)
            if d <= best_d:
                best_d, best = d, chain
    return best, best_d


def link_codes_to_pipes(codes: list[CodeHit], chains: list[PipeChain],
                        leaders: list[Leader], cfg: Config) -> None:
    """För varje godkänd kodträff: hitta närmaste ledartråd med en ändpunkt
    nära kodens bbox, följ den till andra änden, matcha mot närmaste punkt
    på en rörsträcka. Fallback: direkt närhet kod->rör."""
    used_leaders: set[int] = set()
    for code in codes:
        if code.excluded:
            continue
        bbox = code.bbox.expanded(cfg.leader_code_tol_pt)

        best_leader: Leader | None = None
        best_chain: PipeChain | None = None
        best_score = None
        for leader in leaders:
            if leader.id in used_leaders:
                continue
            for near, far in ((leader.p1, leader.p2), (leader.p2, leader.p1)):
                d_code = code.bbox.distance_to_point(near)
                if not bbox.contains(near) and d_code > cfg.leader_code_tol_pt:
                    continue
                chain, d_pipe = _nearest_chain(far, chains, cfg.leader_pipe_tol_pt)
                if chain is None:
                    continue
                score = d_code + d_pipe
                if best_score is None or score < best_score:
                    best_score = score
                    best_leader, best_chain = leader, chain
        if best_chain is not None and best_leader is not None:
            code.linked_chain = best_chain.id
            code.link_method = "leader"
            best_leader.code_id = code.id
            best_leader.chain_id = best_chain.id
            used_leaders.add(best_leader.id)
            best_chain.linked_codes.append(code.id)
            continue

        # Fallback: koden ligger direkt intill/på en rörsträcka
        chain, _d = _nearest_chain(code.bbox.center, chains, cfg.proximity_link_pt)
        if chain is not None:
            code.linked_chain = chain.id
            code.link_method = "proximity"
            chain.linked_codes.append(code.id)

    linked = sum(1 for c in codes if c.linked_chain is not None)
    unlinked = sum(1 for c in codes if not c.excluded and c.linked_chain is None)
    log.info("Kod<->rör-koppling: %d kopplade, %d ej kopplade till rör "
             "(kandidater till legend/tabelltext eller punktkomponenter)",
             linked, unlinked)
