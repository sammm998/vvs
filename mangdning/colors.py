"""Stabil färg per unik kod (Del D punkt 3).

Varje unik kod+dimension får en konsekvent färg genom hela körningen (och
mellan körningar – färgen härleds deterministiskt ur kodtexten), precis som
i facit där t.ex. "S3-R8-75" alltid är #8000FF.

Färgerna hämtas ur samma mättade CAD-palett som facit använder, så att den
markerade PDF:en och mängdförteckningen är visuellt jämförbara med en
professionell mängdning. Paletten gås igenom deterministiskt så att två
olika koder i samma ritning inte får samma färg förrän paletten är slut.
"""

from __future__ import annotations

import colorsys
import hashlib

# Palett observerad i facit-exporten (professionellt mängdningsverktyg).
FACIT_PALETTE: list[str] = [
    "#FF0000", "#0000FF", "#8000FF", "#0080C0", "#8080FF",
    "#00FFFF", "#FF0080", "#FF8000", "#FF80FF", "#FF6600",
    "#00C000", "#C000C0", "#0040FF", "#804000", "#00C0C0",
    "#FF4080", "#40C000", "#8040FF", "#C08000", "#0080FF",
]


def _hash(code: str) -> int:
    return int.from_bytes(hashlib.sha256(code.encode("utf-8")).digest()[:8], "big")


def color_for_code(code: str) -> str:
    """Deterministisk hexfärg för en kodtext, ur facit-paletten."""
    return FACIT_PALETTE[_hash(code) % len(FACIT_PALETTE)]


def assign_palette(codes: list[str]) -> dict[str, str]:
    """Tilldela distinkta färger till en känd uppsättning koder.

    Koderna sorteras för stabilitet, och paletten delas ut utan krockar så
    länge den räcker; därefter genereras extra färger deterministiskt.
    """
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for code in sorted(set(codes)):
        start = _hash(code) % len(FACIT_PALETTE)
        for offset in range(len(FACIT_PALETTE)):
            candidate = FACIT_PALETTE[(start + offset) % len(FACIT_PALETTE)]
            if candidate not in used:
                mapping[code] = candidate
                used.add(candidate)
                break
        else:
            mapping[code] = _generated_color(code)
    return mapping


def _generated_color(code: str) -> str:
    """Reservfärg när paletten är slut – mättad och läsbar på vitt."""
    digest = hashlib.sha256(code.encode("utf-8")).digest()
    hue = int.from_bytes(digest[:2], "big") / 65535.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.9, 0.8)
    return "#{:02X}{:02X}{:02X}".format(int(r * 255), int(g * 255), int(b * 255))


def hex_to_rgb01(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
