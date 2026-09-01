"""Lagerbaserad identifiering av rörledningar.

AutoCAD-exporterade PDF:er bär i regel med sig ritningens lagerstruktur som
"optional content groups". Det är originalinformation från CAD-modellen och
alltså exakt – till skillnad från att gissa utifrån linjebredd, färg eller
hur sammanhängande linjerna ligger. Finns lagren behöver ingenting gissas:
vi vet vilka linjer som är rör, vilket system de tillhör, och vilka som är
väggar, text eller ram.

Lagernamnen följer svensk byggstandard (SB11/BSAB), t.ex.

    268140-W-50-P-A-00|V-53BB-FE--S3-
    \\_____ xref-prefix ___/ \\_ lagernamn _/

I själva lagernamnet är
  * första tecknet disciplinen: V = VVS, K = konstruktion, A = arkitekt,
    E = el, M = mark,
  * mittfältet byggdelen (53BB = spillvattenledning, 52BB = tappvatten),
  * typfältet innehållet: FE = ledning, T = text, S = symbol,
  * sista fältet systembeteckningen (S1, S3, V1, V2 ...).

Standarden är gemensam för svenska projektörer, men ingen ritning är
skyldig att följa den. Därför är mönstren konfigurerbara, valet loggas
alltid, och användaren kan välja lager själv (--pipe-layer / webbgränssnittet).
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# Disciplinbokstav i början av lagernamnet.
DISCIPLINE_VVS = "V"

# Typfält som betyder "ledning" (den ritade röret) respektive text/symbol.
LINE_TYPE_CODES = ("FE",)
TEXT_TYPE_CODES = ("T", "TX")

# Byggdelskod -> systemkategori, samma kategorinamn som facit använder.
BUILDING_PART_SYSTEMS = {
    "52": "Rör tappvatten",
    "53": "Spill- dagvatten",
    "56": "Rör värme",
    "55": "Rör kyla",
    "57": "Luftbehandling",
}

# Systembeteckning i sista fältet -> kategori, som komplement till byggdelen.
SYSTEM_ID_SYSTEMS = {
    "S": "Spill- dagvatten",
    "D": "Spill- dagvatten",
    "V": "Rör tappvatten",
    "K": "Rör tappvatten",
}


def layer_name(raw: str | None) -> str:
    """Lagernamnet utan xref-prefix ("proj|LAGER" -> "LAGER")."""
    if not raw:
        return ""
    return raw.split("|")[-1].strip()


def _fields(name: str) -> list[str]:
    """Dela lagernamnet i dess bindestrecksfält."""
    return [f for f in name.split("-")]


def discipline(name: str) -> str:
    name = layer_name(name)
    return name[:1].upper() if name else ""


def is_pipe_layer(raw: str) -> bool:
    """Är lagret en VVS-ledning (och inte text, symbol eller stomme)?"""
    name = layer_name(raw)
    if discipline(name) != DISCIPLINE_VVS:
        return False
    fields = _fields(name)
    # typfältet ligger efter disciplin och byggdel; leta efter ledningskoden
    # bland fälten och avvisa uttryckliga textlager.
    upper = [f.upper() for f in fields if f]
    if any(f in TEXT_TYPE_CODES for f in upper):
        return False
    return any(f in LINE_TYPE_CODES for f in upper)


def system_for_layer(raw: str) -> str | None:
    """Systemkategori ur lagernamnet, t.ex. "Spill- dagvatten"."""
    name = layer_name(raw)
    fields = [f for f in _fields(name) if f]
    # byggdelskoden är fältet efter disciplinen, t.ex. "53BB"
    for field in fields[1:]:
        digits = re.match(r"^(\d{2})", field)
        if digits and digits.group(1) in BUILDING_PART_SYSTEMS:
            return BUILDING_PART_SYSTEMS[digits.group(1)]
    # annars systembeteckningen i sista fältet, t.ex. "S3" eller "V1"
    for field in reversed(fields):
        m = re.match(r"^([A-ZÅÄÖ])\d", field.upper())
        if m and m.group(1) in SYSTEM_ID_SYSTEMS:
            return SYSTEM_ID_SYSTEMS[m.group(1)]
    return None


def system_id_for_layer(raw: str) -> str | None:
    """Systembeteckningen i lagret, t.ex. "S3" eller "V1"."""
    fields = [f for f in _fields(layer_name(raw)) if f]
    for field in reversed(fields):
        m = re.match(r"^([A-ZÅÄÖ]\d{1,2})$", field.upper())
        if m:
            return m.group(1)
    return None


def classify_layers(layer_lengths: dict[str, float],
                    pattern: str | None = None,
                    ) -> tuple[list[str], dict[str, str]]:
    """Dela upp ritningens lager i rörlager och övriga.

    layer_lengths: lagernamn -> total ritad längd (pt), används bara för
    loggning och för att sortera bort tomma lager.
    pattern: valfritt eget regex som ersätter standardtolkningen, för
    ritningar som inte följer SB11.

    Returnerar (rörlager, lager -> systemkategori).
    """
    if pattern:
        rx = re.compile(pattern)
        pipes = [name for name in layer_lengths if rx.search(layer_name(name))]
    else:
        pipes = [name for name in layer_lengths if is_pipe_layer(name)]

    systems = {}
    for name in pipes:
        system = system_for_layer(name)
        if system:
            systems[name] = system

    if pipes:
        log.info("Rörlager ur CAD-lagren (%d st): %s", len(pipes), ", ".join(
            f"{layer_name(n)} [{systems.get(n, 'okänt system')}]"
            for n in sorted(pipes, key=lambda n: -layer_lengths.get(n, 0))))
    else:
        log.info("Inga VVS-ledningslager hittades bland %d lager – faller "
                 "tillbaka på linjebredd/geometri.", len(layer_lengths))
    return pipes, systems
