#!/usr/bin/env python3
"""Mängdning av CAD-exporterade VVS/VA-ritningar (PDF).

Användning:
    python mangda_ritning.py input.pdf --output-dir out/

Se README.md och `python mangda_ritning.py --help` för alla flaggor.
"""

import sys

from mangdning.cli import main

if __name__ == "__main__":
    sys.exit(main())
