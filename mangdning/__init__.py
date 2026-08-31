"""Mängdning av CAD-exporterade VVS/VA-ritningar i PDF-format.

Pipeline:
  Del A (ocr_codes)  – textkoder via OCR på rastrerad bild
  Del B (pipes)      – rörledningar via PDF:ens vektor-ritkommandon
  Del C (linking)    – koppling kod <-> rörsträcka via ledartrådar
  Del D (quantify)   – skalbestämning och mängdförteckning
"""

__version__ = "0.1.0"
