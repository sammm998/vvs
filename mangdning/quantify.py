"""Del D – mängdning (kvantitetsuttag).

Slutmålet: en mängdförteckning strukturellt jämförbar med facit-formatet
(en rad per sammanhängande rörsträcka/komponentinstans, INTE summerat per
kod) plus en separat aggregerad vy per kod.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .colors import color_for_code
from .config import Config
from .legend import system_for_code
from .models import CodeHit, PipeChain, QuantityRow, ScaleResult

log = logging.getLogger(__name__)

UNLINKED_SUBJECT = "OKOPPLAD RÖRSTRÄCKA"


@dataclass
class Aggregate:
    subject: str
    lager: str
    color: str
    n_strackor: int = 0
    total_langd_m: float = 0.0
    n_punkter: int = 0
    antal_vs: int = 0
    total_vertikalhojd_m: float = 0.0
    flaggor: set[str] = field(default_factory=set)


@dataclass
class QuantityResult:
    rows: list[QuantityRow]
    aggregates: list[Aggregate]
    scale: ScaleResult
    warnings: list[str]
    count_checks: list[str]  # legend-antal vs hittade instanser


def _round_m(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def build_quantities(codes: list[CodeHit], chains: list[PipeChain],
                     scale: ScaleResult, prefix_map: dict[str, str],
                     expected_counts: dict[str, int],
                     cfg: Config, document: str = "",
                     sidetikett: str = "") -> QuantityResult:
    rows: list[QuantityRow] = []
    warnings: list[str] = list(scale.warnings)
    codes_by_id = {c.id: c for c in codes}
    active_chains = [c for c in chains if not c.excluded]

    if not scale.known:
        warnings.append("SKALA OKÄND – längder anges i PDF-punkter, "
                        "inte meter. Mängderna kan INTE användas för kalkyl "
                        "förrän skalan är satt (--scale).")
    vh_warned = False

    # --- Rörsträckor: EN RAD PER SAMMANHÄNGANDE STRÄCKA (Del D punkt 2) ---
    for chain in active_chains:
        primary: CodeHit | None = None
        if chain.linked_codes:
            linked = [codes_by_id[i] for i in chain.linked_codes
                      if i in codes_by_id]
            # närmaste koden till kedjan blir primär beteckning
            if linked:
                primary = min(linked, key=lambda c: chain.bbox.distance_to_point(
                    c.bbox.center))

        length_m = scale.to_meters(chain.length_pt)
        flags: list[str] = []
        if not scale.known:
            flags.append("skala okänd (längd i pt)")

        if primary is None:
            rows.append(QuantityRow(
                subject=UNLINKED_SUBJECT,
                lager="Okänt system",
                color="#808080",
                langd_m=_round_m(length_m if scale.known else chain.length_pt),
                kalla=f"chain:{chain.id}",
                kommentar="; ".join(["okopplad rörsträcka"] + flags),
                document=document, sidetikett=sidetikett,
            ))
            continue

        subject = primary.full_code
        system = system_for_code(primary.base_code, prefix_map, cfg)
        n = primary.count
        eff_len = (length_m if scale.known else chain.length_pt)
        if n > 1:
            # Nx-notation: N parallella fysiska rör på en ritad linje
            eff_len = eff_len * n
            flags.append(f"{n} parallella rör ({primary.raw_text})")
        if primary.conf < 60:
            flags.append(f"låg OCR-confidence ({primary.conf:.0f})")

        antal_vs = None
        vh = None
        total_vh = None
        if chain.vertical_symbols:
            antal_vs = chain.vertical_symbols * n
            vh = cfg.vertical_heights.get(
                system, cfg.vertical_heights.get("Okänt system", 2.8))
            total_vh = antal_vs * vh
            flags.append(f"vertikalhöjd {vh} m är ett ANTAGANDE (konfig), "
                         "inte uppmätt")
            if not vh_warned:
                warnings.append(
                    "Vertikalhöjder är fasta konfigurerade antaganden per "
                    "system (--vertikalhojd) – en planvy visar aldrig vertikal "
                    "höjd geometriskt. Måste verifieras av användaren.")
                vh_warned = True

        rows.append(QuantityRow(
            subject=subject,
            lager=system,
            color=color_for_code(subject),
            langd_m=_round_m(eff_len),
            antal_vs=antal_vs,
            vertikal_hojd_m=vh,
            total_vertikalhojd_m=_round_m(total_vh),
            kalla=f"chain:{chain.id}",
            kommentar="; ".join(flags),
            document=document, sidetikett=sidetikett,
        ))

    # --- Punktkomponenter (Del D punkt 7): förstklassigt delresultat ---
    # Koder utan rörkoppling som inte exkluderats = punktmarkeringar
    # (brunnar, ventiler etc.). En rad per instans.
    for code in codes:
        if code.excluded or code.linked_chain is not None:
            continue
        subject = code.full_code
        system = system_for_code(code.base_code, prefix_map, cfg)
        flags = ["punktkomponent (ej kopplad till rörsträcka)"]
        if code.conf < 60:
            flags.append(f"låg OCR-confidence ({code.conf:.0f})")
        rows.append(QuantityRow(
            subject=subject,
            lager=system,
            color=color_for_code(subject),
            antal=code.count,
            kalla=f"code:{code.id}@({code.bbox.center[0]:.0f},"
                  f"{code.bbox.center[1]:.0f})",
            kommentar="; ".join(flags),
            document=document, sidetikett=sidetikett,
        ))

    # --- Aggregat per kod (sekundär vy – radnivån är huvudleveransen) ---
    agg_map: dict[str, Aggregate] = {}
    for row in rows:
        agg = agg_map.setdefault(row.subject, Aggregate(
            subject=row.subject, lager=row.lager, color=row.color))
        if row.antal is not None:
            agg.n_punkter += row.antal
        elif row.langd_m is not None:
            agg.n_strackor += 1
            agg.total_langd_m += row.langd_m
        if row.antal_vs:
            agg.antal_vs += row.antal_vs
            agg.total_vertikalhojd_m += row.total_vertikalhojd_m or 0.0
        if row.kommentar:
            for f in row.kommentar.split("; "):
                if f:
                    agg.flaggor.add(f)
    aggregates = sorted(agg_map.values(), key=lambda a: (a.lager, a.subject))
    for agg in aggregates:
        agg.total_langd_m = round(agg.total_langd_m, 2)
        agg.total_vertikalhojd_m = round(agg.total_vertikalhojd_m, 2)

    # --- Sanity-check mot legendens antalsuppgifter (Del D punkt 7) ---
    count_checks: list[str] = []
    for legend_code, expected in expected_counts.items():
        found = sum(a.n_punkter for a in aggregates
                    if a.subject == legend_code
                    or a.subject.startswith(legend_code + "-")
                    or legend_code.startswith(a.subject))
        status = "OK" if found == expected else "AVVIKELSE"
        msg = (f"{legend_code}: legend anger {expected} st, "
               f"hittade {found} st på ritningsytan [{status}]")
        count_checks.append(msg)
        if status == "AVVIKELSE":
            warnings.append("Antals-sanity-check: " + msg)

    warnings.append(
        "OCR är inte 100 % träffsäker på tät, liten CAD-text – enstaka koder "
        "kan saknas eller feltolkas (S/5, B/8, O/0). Mängderna är en "
        "UPPSKATTNING, inte en exakt mängdförteckning.")

    log.info("Mängdning: %d rader (%d rörsträckor, %d punktposter), "
             "%d unika koder",
             len(rows),
             sum(1 for r in rows if r.langd_m is not None and r.antal is None),
             sum(1 for r in rows if r.antal is not None),
             len(aggregates))
    return QuantityResult(rows=rows, aggregates=aggregates, scale=scale,
                          warnings=warnings, count_checks=count_checks)
