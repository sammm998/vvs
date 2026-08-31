"""Stabil färg per unik kod (Del D punkt 3).

Varje unik kod+dimension får en konsekvent hex-färg genom hela körningen
(och mellan körningar – färgen härleds deterministiskt ur kodtexten), precis
som i facit där t.ex. "S3-R8-75" alltid är #8000FF.
"""

from __future__ import annotations

import colorsys
import hashlib


def color_for_code(code: str) -> str:
    """Deterministisk, väl separerad hex-färg för en kodtext."""
    digest = hashlib.sha256(code.encode("utf-8")).digest()
    hue = int.from_bytes(digest[:2], "big") / 65535.0
    sat = 0.75 + (digest[2] / 255.0) * 0.25          # 0.75-1.0
    val = 0.55 + (digest[3] / 255.0) * 0.35          # 0.55-0.90 (läsbart på vitt)
    r, g, b = colorsys.hsv_to_rgb(hue, sat, val)
    return "#{:02X}{:02X}{:02X}".format(int(r * 255), int(g * 255), int(b * 255))


def hex_to_rgb01(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
