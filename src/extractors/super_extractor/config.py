"""
Configuration - Externalized constants and thresholds
Used by all modules in super_extractor package
"""

from typing import Dict, List, Tuple

# =============================================================================
# VALIDATION THRESHOLDS
# =============================================================================

# Surface sum tolerance for validation (percentage)
SUM_TOLERANCE_ERROR: float = 0.12  # 12% - above = error
SUM_TOLERANCE_WARNING: float = 0.08  # 8% - above = warning

# Composite room detection tolerance (m²)
COMPOSITE_TOLERANCE: float = 0.5

# Room surface validation ranges (min, max) in m² - defined in models.py
# (using RoomType enum for type safety)

# =============================================================================
# SPATIAL EXTRACTION CONSTANTS
# =============================================================================

TOTAL_KEYWORDS: List[str] = [
    "TOTAL SURFACE HABITABLE", "SURFACE HABITABLE", "TOTAL SH",
    "SURFACE TOTALE HABITABLE", "HABITABLE", "PRIVATIVE",
]

ANNEX_KEYWORDS: List[str] = [
    "TOTAL SURFACE ANNEXE", "SURFACE ANNEXE", "TOTAL ANNEXE",
    "TOTAL EXTERIEURS", "TOTAL EXT",
]

SKIP_KEYWORDS_SPATIAL: List[str] = [
    "BATIMENT", "APPARTEMENT", "NIVEAU", "TYPE", "LEGENDE",
    "DATE", "IND", "PLAN", "ECHELLE", "SCCV", "VENTE", "TOTAL",
    "SURF. LOT", "SURF.LOT", "N° LOT", "N°LOT", "LOT:",
]

UNIQUE_ROOM_TYPES: List[str] = ["SEJOUR", "CUISINE", "SEJOUR/CUISINE", "ENTREE", "RECEPTION", "JARDIN", "CELLIER"]
NUMBERED_ROOM_TYPES: List[str] = ["CHAMBRE", "SDB", "SDE", "WC", "BALCON", "GARAGE", "BUANDERIE", "PLACARD"]

# =============================================================================
# SUPER EXTRACTOR CONSTANTS
# =============================================================================

SKIP_KEYWORDS_EXTRACTOR: List[str] = [
    "TOTAL", "SURFACE HABITABLE", "SURFACE ANNEXE",
    "PLAN", "VENTE", "DATE", "IND", "ECHELLE",
    "SURF", "LOT", "M00", "TYPE:", "N°",
]

TOTAL_KEYWORDS_EXTRACTOR: List[str] = [
    'HABITABLE', 'PRIVATIVE', 'ANNEXE', 'EXTÉRIEUR',
    'EXTERIEUR', 'À VIVRE', 'A VIVRE'
]

# Room inference thresholds (m²)
LIVING_ROOM_INFERENCE_MIN: float = 12.0
LIVING_ROOM_INFERENCE_MAX: float = 60.0
BEDROOM_INFERENCE_MIN: float = 5.0
BEDROOM_INFERENCE_MAX: float = 40.0

# =============================================================================
# METADATA EXTRACTION
# =============================================================================

REF_BLACKLIST: List[str] = [
    "R1", "R2", "R3", "T1", "T2", "T3", "T4", "T5",  # Too short
    "DATE", "TYPE", "PLAN", "NOTA", "IND",  # Not references
    "L261", "R261", "L111", "R111",  # Law articles
    "H180", "H214", "H250",  # Height codes
]

PROMOTER_SIGNATURES: Dict[str, str] = {
    "nexity|NEXITY": "Nexity",
    "bouygues|BOUYGUES": "Bouygues Immobilier",
    "vinci|VINCI": "Vinci Immobilier",
    "kaufman|KAUFMAN": "Kaufman & Broad",
    "altarea|ALTAREA": "Altarea Cogedim",
    "ogic|OGIC": "Ogic",
    "icade|ICADE": "Icade",
    "pitch|PITCH": "Pitch Promotion",
    "link|LINK": "Linkcity",
    " BNP |PARibas": "BNP Paribas Immobilier",
}

# =============================================================================
# ROOM PARSERS
# =============================================================================

SKIP_KEYWORDS_PARSERS: List[str] = [
    "TOTAL", "SURFACE HABITABLE", "SURFACE ANNEXE",
    "PLAN", "VENTE", "DATE", "IND", "ECHELLE",
    "SURF", "LOT", "TYPE", "N°",
]

# =============================================================================
# ROOM NORMALIZER
# =============================================================================

# Room aliases are in room_normalizer.py as complex regex patterns
# They are kept there for maintainability (500+ patterns)

# =============================================================================
# TEXT EXTRACTION
# =============================================================================

# OCR settings
OCR_DPI: int = 300
OCR_EXTRA_DPI: int = 400
OCR_LINE_TOLERANCE: int = 10  # pixels for same line

# =============================================================================
# FLOOR UTILITIES
# =============================================================================

FLOOR_CODE_MAPPING: Dict[str, str] = {
    "001": "R+1",
    "002": "R+2",
    "003": "R+3",
    "004": "R+4",
    "005": "R+5",
    "RDC": "RDC",
    "RDC/1": "RDC",
    "REZ": "RDC",
}
