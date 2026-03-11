# SuperExtractor Module Documentation

## Overview

**SuperExtractor** is an architectural plan extraction system that extracts structured data from French real estate floor plans (PDF format). It identifies:
- Lot reference (e.g., "A008", "M011")
- Property type (apartment/house)
- Typology (T1, T2, T3, etc.)
- Floor level (RDC, R+1, R+2, etc.)
- Living space and room surfaces
- Room inventory (bedrooms, bathrooms, kitchen, etc.)
- Exterior spaces (balcony, garden, garage, etc.)

**Version**: 3.0 (modular architecture)
**Location**: `src/extractors/super_extractor/`

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          SuperExtractor                                 │
│                     (Main Orchestrator)                                 │
│                                                                         │
│  1. TextExtractor     → Raw text (PyMuPDF + OCR fallback)               │
│  2. SpatialExtractor  → Summary table by spatial position               │
│  3. RoomNormalizer    → Normalize room names (500+ patterns)            │
│  4. CompositeResolver → Detect "Reception = Séjour + Cuisine"           │
│  5. MetadataExtractor→ Reference, floor, building, promoter             │
│  6. PlanValidator     → Mathematical validation                         │
│  7. RoomInference     → Infer missing rooms from surface gaps           │
│  8. Deduplication     → Remove duplicate rooms                          │
│  9. FloorUtils        → Multi-floor splitting                           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Module Details (13 Files)

### 1. `models.py` (242 lines)
**Purpose**: Data classes and enumerations shared across all modules.

#### Dependencies:
```python
from typing import Dict, Optional, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum
```

#### Classes:

##### `RoomType` (Enum)
21 room types for classification:
```python
ENTRY = "entree"
LIVING_ROOM = "sejour"
KITCHEN = "cuisine"
LIVING_KITCHEN = "sejour_cuisine"
RECEPTION = "reception"
BEDROOM = "chambre"
BATHROOM = "salle_de_bain"
SHOWER_ROOM = "salle_d_eau"
WC = "wc"
CIRCULATION = "circulation"
STORAGE = "storage"
DRESSING = "dressing"
BALCONY = "balcon"
TERRACE = "terrasse"
GARDEN = "jardin"
LOGGIA = "loggia"
PATIO = "patio"
PARKING = "parking"
CELLAR = "cave"
UNKNOWN = "unknown"
```

##### `SURFACE_RANGES` (Dict)
Expected surface ranges per room type (in m²):
```python
RoomType.ENTRY: (1.0, 25.0)
RoomType.LIVING_ROOM: (8.0, 80.0)
RoomType.BEDROOM: (5.0, 40.0)
RoomType.BATHROOM: (2.0, 20.0)
RoomType.GARDEN: (5.0, 5000.0)
# ... etc
```

##### `ExtractedRoom` (Dataclass)
Represents a single room:
```python
@dataclass
class ExtractedRoom:
    name_raw: str                    # Original name from PDF
    name_normalized: str              # Normalized name (e.g., "sejour_cuisine")
    surface: float                   # Surface in m²
    room_type: RoomType              # Classification
    is_exterior: bool = False        # Is exterior room?
    is_composite: bool = False       # Is composite (Séjour+Cuisine)?
    room_number: Optional[int] = None # Room number (for bedrooms 1, 2, 3...)
    source: str = "unknown"          # Source: "spatial", "ocr", "inferred"
    confidence: float = 1.0          # Confidence score 0-1
    bbox: Optional[Tuple] = None     # Bounding box
    children: List[str] = field(default_factory=list)  # Child rooms for composites
```

##### `ExtractionResult` (Dataclass)
Complete extraction result for one lot:
```python
@dataclass
class ExtractionResult:
    reference: str = ""               # Lot reference (e.g., "A008")
    parcel_label: str = ""            # Label (e.g., "M011")
    page_number: int = 0             # PDF page number
    property_type: str = "appartment" # "appartment" or "house"
    typology: str = ""               # "T2", "T3", "T4", etc.
    floor: str = ""                  # "RDC", "R+1", "R+1+R+2"
    building: str = ""               # Building identifier
    program_name: str = ""          # Residence/program name
    address: str = ""               # Postal code + city
    living_space: float = 0.0       # Declared habitable surface
    annex_space: float = 0.0        # Declared annex surface
    surface_propriete: float = 0.0  # Total property surface (houses)
    surface_espaces_verts: float = 0.0  # Green space surface
    niveaux: List[str] = field(default_factory=list)  # Floor levels
    rooms: List[ExtractedRoom] = field(default_factory=list)  # All rooms
    composites: Dict[str, List[str]] = field(default_factory=dict)  # Composite rooms
    validation_errors: List[str] = field(default_factory=list)  # Validation errors
    validation_warnings: List[str] = field(default_factory=list)  # Warnings
    sources: Dict[str, str] = field(default_factory=dict)  # Room sources
    raw_text: str = ""              # Raw extracted text
    floor_results: List[Any] = field(default_factory=list)  # For duplex/maison
    promoter_detected: str = ""     # Detected promoter name
```

#### Key Methods:

##### `to_legacy_format(include_raw_text: bool = False) -> Dict[str, Any]`
Converts ExtractionResult to legacy dict format for backward compatibility:
```python
result = {
    "A008": {
        "parcelTypeId": "appartment",
        "parcelTypeLabel": "Appartement",
        "typology": "T2",
        "floor": "RDC",
        "living_space": "45.50",
        "annex_space": "5.20",
        "surfaceDetail": {
            "sejour_cuisine": 24.50,
            "chambre_1": 12.30,
            "salle_de_bain": 5.20,
            "wc": 3.50
        },
        "option": {
            "balcony": False,
            "terrace": False,
            "garden": True,
            "loggia": False,
            "parking": False,
            "garage": False
        },
        "_validation": {
            "is_valid": True,
            "errors": [],
            "warnings": []
        }
    }
}
```

---

### 2. `text_extractor.py` (286 lines)
**Purpose**: Extract raw text from PDFs using PyMuPDF with OCR fallback.

#### Dependencies:
```python
import re
import logging
from pathlib import Path
from typing import Optional
# External: fitz (PyMuPDF), PIL, pytesseract
```

#### Class: `TextExtractor`

##### `__init__(use_ocr: bool = True, tesseract_path: Optional[str] = None)`
```python
def __init__(self, use_ocr: bool = True, tesseract_path: Optional[str] = None):
    self.use_ocr = use_ocr
    self.tesseract_path = tesseract_path
```

##### `extract(pdf_path: str, page_num: Optional[int] = None, force_ocr: bool = False) -> dict`
Main extraction method. Returns:
```python
{
    "text_pymupdf": str,          # Cleaned PyMuPDF text
    "raw_pymupdf": str,          # Raw text with line structure preserved
    "text_ocr": str,             # OCR text (if used)
    "primary_source": str,       # "pymupdf" or "ocr"
    "pages_data": list,          # PyMuPDF block data with coordinates
    "ocr_pages_data": list,      # OCR data with word positions
}
```

#### Internal Methods:

##### `_extract_pymupdf(path: Path, page_num: Optional[int]) -> tuple`
- Uses `fitz` (PyMuPDF) to extract text
- Returns `(full_text, pages_data)` where pages_data contains:
  - `page_num`: Page index
  - `width`, `height`: Page dimensions
  - `blocks`: Text blocks with coordinates

##### `_extract_ocr(path: Path) -> str`
- Uses Tesseract OCR via `pytesseract`
- Converts PDF page to image (300 DPI)
- Returns cleaned text

##### `_extract_ocr_with_data(path: Path, page_num: Optional[int]) -> tuple`
- OCR with structural data for spatial analysis
- Returns `(text, pages_data)` with word positions
- Also extracts right column at higher DPI for better table capture

##### `_group_words_into_lines(words: list) -> list`
- Groups OCR words into lines based on Y position
- Tolerance: 10 pixels for same line

##### `_clean_text(text: str) -> str`
- Normalizes whitespace
- Converts French commas to dots: `24,50 m²` → `24.50 m²`

---

### 3. `spatial_extractor.py` (541 lines)
**Purpose**: Extract room/surface table from summary tables using spatial positioning.

#### Dependencies:
```python
import re
import logging
from typing import List, Dict, Tuple, Optional
```

#### Class: `SpatialExtractor`

##### Constants:
```python
TOTAL_KEYWORDS = [
    "TOTAL SURFACE HABITABLE", "SURFACE HABITABLE", "TOTAL SH",
]
ANNEX_KEYWORDS = [
    "TOTAL SURFACE ANNEXE", "SURFACE ANNEXE", "TOTAL ANNEXE",
    "TOTAL EXTERIEURS", "TOTAL EXT",
]
SKIP_KEYWORDS = [
    "BATIMENT", "APPARTEMENT", "NIVEAU", "TYPE", "LEGENDE",
    "DATE", "IND", "PLAN", "ECHELLE", "SCCV", "VENTE", "TOTAL",
    "SURF. LOT", "SURF.LOT", "N° LOT", "N°LOT", "LOT:",
]
UNIQUE_ROOM_TYPES = ["SEJOUR", "CUISINE", "SEJOUR/CUISINE", "ENTREE", "RECEPTION", "JARDIN", "CELLIER"]
NUMBERED_ROOM_TYPES = ["CHAMBRE", "SDB", "SDE", "WC", "BALCON", "GARAGE", "BUANDERIE", "PLACARD"]
```

##### `extract_from_pages(pages_data: List[Dict], reference_hint: Optional[str] = None) -> Dict`
Main method. Returns:
```python
{
    "table_rows": [(room_name, surface_str), ...],
    "living_space": float or None,
    "annex_space": float or None,
    "metadata_lines": [str, ...],
    "source": "spatial",
}
```

#### Key Methods:

##### `_analyze_page(page_data: Dict, reference_hint: Optional[str]) -> Dict`
Analyzes a single page:
1. Extracts text lines from blocks (PyMuPDF) or lines (OCR)
2. Filters to "high zone" (y < 90% of page height)
3. Finds lines with surfaces or room keywords
4. Merges close vertical lines (15px tolerance)
5. Applies 5 regex patterns for room/surface matching:
   - Standard: `"Chambre 12.50 m²"`
   - Collé: `"ENTREE/DGT 9,85m²"`
   - Ultra-collé: `"CELLIER1,78m²"`
   - Inverted: `"12.50 m² CHAMBRE"`
   - Integer: `"CHAMBRE 397 m?"` (OCR error - missing decimal)

##### `_strip_line_noise(text: str) -> str`
Removes OCR noise prefixes:
- `"O Q G 228 Séjour / Cuisine"` → `"Séjour / Cuisine"`
- `"D / } =+ Séjour / Cuisine"` → `"Séjour / Cuisine"`

##### `_fix_missing_decimal(surface_str: str) -> str`
Fixes OCR errors dropping decimal points:
- `"397"` → `"3.97"` (3 digits)
- `"1226"` → `"12.26"` (4 digits)

##### `_deduplicate_rows(rows: List[Tuple[str, str]]) -> List[Tuple[str, str]]`
Smart deduplication:
- For unique rooms (SEJOUR, ENTREE): keep largest surface
- For numbered rooms (CHAMBRE 1, CHAMBRE 2): keep all if surfaces differ
- Remove exact duplicates

##### `_merge_close_lines(lines: List[Dict], y_tol: float = 15.0, x_tol: float = 30.0) -> List[Dict]`
Merges lines that are close together (same row in table).

---

### 4. `room_normalizer.py` (344 lines)
**Purpose**: Normalize room names to standard format and classify room types.

#### Dependencies:
```python
import re
import logging
from typing import Tuple, Optional
from .models import RoomType
```

#### Class: `RoomNormalizer`

##### `ROOM_ALIASES` (List of tuples)
500+ patterns for room name normalization. Format:
```python
(pattern_regex, name_template, RoomType, is_exterior)
```

Examples:
```python
# Composites (checked first)
(r"^(SEJOUR|SÉJOUR)\s*/?\s*CUISINE", "sejour_cuisine", RoomType.LIVING_KITCHEN, False)

# Bathrooms
(r"^(SDB\s*/\s*WC|SDB\s*WC)$", "salle_de_bain", RoomType.BATHROOM, False)

# Bedrooms with number
(r"^CHAMBRE\s*(\d+)$", "chambre_{n}", RoomType.BEDROOM, False)

# Exterior
(r"^JARDIN$", "jardin", RoomType.GARDEN, True)
(r"^GARAGE$", "garage", RoomType.PARKING, True)
```

##### `normalize(name_raw: str) -> Tuple[Optional[str], Optional[RoomType], Optional[int], bool, float]`
Main method. Returns:
```python
(name_normalized, room_type, room_number, is_exterior, confidence)
# Example:
# ("Chambre 1 + Pl.", "chambre_1", RoomType.BEDROOM, False, 0.95)
```

##### `_deduplicate(name: str) -> str`
If "sejour" already exists, returns "sejour_2". Handles numbered rooms.

---

### 5. `metadata_extractor.py` (406 lines)
**Purpose**: Extract metadata from text (reference, floor, building, promoter, address).

#### Dependencies:
```python
import re
import logging
from typing import Dict, Optional, List, Any
```

#### Class: `MetadataExtractor`

##### `REF_PATTERNS` (List)
Patterns for lot reference extraction (priority ordered):
```python
r"Logement\s*[:\s]*([A-Z]\s*\d{3,4})"  # "Logement: A101"
r"Appartement\s+([A-Z]\d{2,4})"          # "Appartement A008"
r"LOT\s*[:\s]*([A-Z]?\d{2,4})"           # "LOT: A008"
r"\b([A-Z]\d{3,4})\b"                    # Standalone "A008"
```

##### `REF_BLACKLIST` (Set)
False positives to reject:
```python
{"R1", "R2", "R3", "T1", "T2", "T3", "T4", "T5",  # Too short
 "DATE", "TYPE", "PLAN", "NOTA", "IND",             # Not references
 "L261", "R261", "L111", "R111",                    # Law articles
 "H180", "H214", "H250"}                            # Height codes
```

##### `FLOOR_PATTERNS` (List)
Floor extraction patterns:
```python
(r"\bRDC\b", "RDC")
(r"\bR\+1\b", "R+1")
(r"\bR\+(\d+)\b", lambda m: f"R+{m.group(1)}")
(r"NIV\s*(\d{2,3})\b", lambda m: f"R+{int(m.group(1))-1}")
(r"REZ\s*DE\s*CHAUSSEE", "RDC")
```

##### `PROMOTER_SIGNATURES` (Dict)
Promoter detection patterns:
```python
{"nexity|NEXITY": "Nexity",
 "bouygues|BOUYGUES": "Bouygues Immobilier",
 "vinci|VINCI": "Vinci Immobilier",
 "kaufman|KAUFMAN": "Kaufman & Broad",
 ...
}
```

##### `extract(text: str, reference_hint: Optional[str] = None, spatial_metadata: Optional[List[str]] = None) -> Dict`
Main method. Returns:
```python
{
    "reference": "A008",
    "floor": "RDC",
    "building": "A",
    "promoter": "Nexity",
    "living_space": 45.50,
    "annex_space": 5.20,
    "address": "77100 MEAUX",
    "typology_hint": "T2",
    "program": "Résidence du Canton",
    "surface_propriete": 250.0,
    "surface_espaces_verts": 50.0,
}
```

---

### 6. `plan_validator.py` (84 lines)
**Purpose**: Validate extraction results mathematically.

#### Dependencies:
```python
import logging
from .models import RoomType, SURFACE_RANGES
```

#### Class: `PlanValidator`

##### Constants:
```python
SUM_TOLERANCE_ERROR = 0.12    # 12% - above = error
SUM_TOLERANCE_WARNING = 0.08  # 8% - above = warning
```

##### `validate(result: ExtractionResult) -> None`
Runs all validations (modifies result in place):

1. **`_validate_surface_sum`**: Checks if calculated habitable surface matches declared
2. **`_validate_composites`**: Verifies composite rooms (Séjour+Cuisine) sums
3. **`_validate_typology`**: Checks T1/T2/T3 matches bedroom count
4. **`_validate_ranges`**: Verifies room surfaces are plausible
5. **`_validate_basic`**: Ensures at least one room detected

---

### 7. `composite_resolver.py` (68 lines)
**Purpose**: Detect and resolve composite rooms (Réception = Séjour + Cuisine).

#### Dependencies:
```python
import logging
from typing import List, Dict, Tuple
```

#### Class: `CompositeResolver`

##### `COMPOSITE_RULES` (List)
```python
COMPOSITE_RULES = [
    ("reception", ["sejour", "cuisine"], "Réception = Séjour + Cuisine"),
    ("reception", ["sejour_cuisine"], "Réception = Séjour/Cuisine"),
    ("sejour_cuisine", ["sejour", "cuisine"], "Séjour/Cuisine = Séjour + Cuisine"),
]
```

##### `resolve(rooms: List[ExtractedRoom]) -> Tuple[List, Dict[str, List[str]]]`
Returns:
- Updated rooms list (with `is_composite=True` and `children` populated)
- Composites dictionary: `{"sejour_cuisine": ["sejour", "cuisine"]}`

---

### 8. `room_inference.py` (158 lines)
**Purpose**: Infer missing rooms from surface gaps when OCR fails to detect them.

#### Dependencies:
```python
import logging
from typing import List
from .models import RoomType, ExtractedRoom, ExtractionResult
```

#### Class: `RoomInference`

##### `infer_missing_living_room(result: ExtractionResult) -> ExtractionResult`
Rules:
- Declared living_space > 0
- Gap in [12, 60] m² (living room range)
- No living room already present
- At least one bedroom exists
- Gap doesn't match any existing surface

##### `infer_missing_bedroom(result: ExtractionResult) -> ExtractionResult`
Rules:
- Gap in [5, 40] m² (bedroom range)
- At least one bedroom already exists
- Gap doesn't match existing surfaces

##### `detect_typology(rooms: List[ExtractedRoom]) -> str`
Returns "T1", "T2", "T3", etc. based on bedroom count + living room.

##### `detect_property_type(rooms: List[ExtractedRoom], floor: str = "") -> str`
Returns "house" or "appartment" based on:
- Has garden, cellar, or parking
- Multi-floor (floor contains "+")

---

### 9. `deduplication.py` (241 lines)
**Purpose**: Remove duplicate rooms from multi-source extraction.

#### Dependencies:
```python
import logging
from typing import List, Optional
from itertools import combinations
from math import comb
from .models import RoomType, ExtractedRoom, ExtractionResult
```

#### Class: `DeduplicationUtils`

##### `final_dedup(rooms: List[ExtractedRoom]) -> List[ExtractedRoom]`
Two-pass deduplication:
1. By exact name_normalized (keep highest confidence)
2. By (type, number, surface) tuple

##### `filter_by_reference(rooms: List[ExtractedRoom], reference: str, living_space: float) -> List[ExtractedRoom]`
Filters rooms when calculated surface >> declared:
- Uses subset sum algorithm to find best match
- Prioritizes spatial source rooms
- Keeps essential rooms (WC, bathroom, entry)

##### `find_best_subset(rooms, target: float) -> Optional[List[ExtractedRoom]]`
Finds subset of rooms whose surfaces sum ≈ target.

---

### 10. `floor_utils.py` (186 lines)
**Purpose**: Handle floor normalization and multi-floor splitting for duplexes/houses.

#### Dependencies:
```python
import re
import copy
from typing import Dict, List, Any
```

#### Class: `FloorUtils`

##### `normalize_floor_label(floor: str) -> str`
Converts floor codes:
- `"001"` → `"R+1"`
- `"002"` → `"R+2"`
- `"RDC"` → `"RDC"`

##### `build_floor_split(ref: str, page_results: list) -> dict`
For multi-page lots (duplex):
- Groups pages by floor
- Returns `{"A18_R+1": result1, "A18_R+2": result2}`
- Or `{}` if single floor (caller combines)

##### `_detect_typology(rooms) -> str`
Detects T1/T2/T3/etc. from room list.

---

### 11. `room_parsers.py` (64 lines)
**Purpose**: Pattern matching utilities for room parsing.

#### Dependencies:
```python
import re
import logging
from typing import List, Tuple
from .models import RoomType, ExtractedRoom
```

#### Class: `RoomParsers`

##### `SURFACE_PATTERNS` (List)
Regex patterns for surface extraction:
```python
r"([A-Za-zÀ-ÿ]...)\s+(\d+[\.,]\d+)\s*m[²2]"   # Standard
r"([A-Za-zÀ-ÿ]...)\s+(\d+[\.,]\d+)m[²2]"      # Collé
r"([A-Za-zÀ-ÿ]...)\s*:\s*(\d+[\.,]\d+)\s*m[²2]"  # With colon
```

##### `rooms_from_table(rows, source) -> List[ExtractedRoom]`
Converts table rows to ExtractedRoom objects using RoomNormalizer.

---

### 12. `super_extractor.py` (Main - ~104K lines)
**Purpose**: Main orchestrator that coordinates all modules.

#### Dependencies (from modules):
```python
from .models import RoomType, ExtractedRoom, ExtractionResult, EXTERIOR_ROOM_TYPES
from .text_extractor import TextExtractor
from .spatial_extractor import SpatialExtractor
from .room_normalizer import RoomNormalizer
from .composite_resolver import CompositeResolver
from .metadata_extractor import MetadataExtractor
from .plan_validator import PlanValidator
from .floor_utils import FloorUtils
from .room_parsers import RoomParsers
from .room_inference import RoomInference
from .deduplication import DeduplicationUtils
```

#### Class: `SuperExtractor`

##### `__init__(use_ocr: bool = True, tesseract_path: Optional[str] = None)`
Initializes all component modules:
```python
self.text_extractor = TextExtractor(use_ocr=use_ocr, tesseract_path=tesseract_path)
self.spatial_extractor = SpatialExtractor()
self.normalizer = RoomNormalizer()
self.composite_resolver = CompositeResolver()
self.metadata_extractor = MetadataExtractor()
self.validator = PlanValidator()
self.floor_utils = FloorUtils(self.normalizer)
self.parsers = RoomParsers(self.normalizer)
self.inference = RoomInference()
self.dedup = DeduplicationUtils()
```

##### `extract(pdf_path: str, reference_hint: Optional[str] = None) -> ExtractionResult`
Main entry point:
1. Checks if PDF has multiple pages
2. If multi-page: calls `_extract_multipage()`
3. If single page: calls `_extract_single_page()`
4. Returns ExtractionResult

##### `_extract_single_page(pdf_path: str, reference_hint: Optional[str] = None, page_num: int = 0, is_multipage_context: bool = False) -> ExtractionResult`
Single page extraction pipeline:
1. **TextExtractor** → raw text + pages_data
2. **SpatialExtractor** → table rows + surfaces
3. **RoomParsers** → ExtractedRoom objects
4. **RoomNormalizer** → normalize names
5. **CompositeResolver** → detect composites
6. **MetadataExtractor** → reference, floor, promoter
7. **RoomInference** → infer missing rooms
8. **Deduplication** → remove duplicates
9. **PlanValidator** → validate results

##### `extract_all_pages(pdf_path: str, reference_hint: Optional[str] = None) -> Dict[str, Any]`
Multi-page extraction:
1. Scans all pages sequentially
2. Groups pages by lot reference (consecutive runs)
3. For single-page lots: returns result directly
4. For multi-page lots: combines or splits by floor
5. Returns dict: `{"A008": result, "A009": result, "M001": {"M001_RDC": r1, "M001_R+1": r2}}`

---

### 13. `__init__.py` (36 lines)
**Purpose**: Package initialization and exports.

#### Functions:
```python
setup_super_extractor_logging()  # Disables default logging (sets to WARNING level)
```

#### Exports:
```python
from .super_extractor import SuperExtractor, extract_plan_data, extract_plan_data_legacy, batch_extract, extract_all_plans
from .models import RoomType, ExtractedRoom, ExtractionResult

__all__ = [
    "SuperExtractor", "extract_plan_data", "extract_plan_data_legacy",
    "batch_extract", "extract_all_plans", "RoomType", "ExtractedRoom", "ExtractionResult",
]
```

---

## Usage Examples

### Basic Single Page Extraction
```python
from src.extractors.super_extractor import SuperExtractor

extractor = SuperExtractor(use_ocr=True)
result = extractor.extract("plan.pdf", "A008")

# Access results
print(f"Reference: {result.reference}")
print(f"Typology: {result.typology}")
print(f"Floor: {result.floor}")
print(f"Living space: {result.living_space}")
print(f"Rooms: {len(result.rooms)}")

# Convert to legacy dict format
data = result.to_legacy_format()
print(data)
```

### Multi-Page Extraction
```python
from src.extractors.super_extractor import SuperExtractor

extractor = SuperExtractor()
all_results = extractor.extract_all_pages("multipage.pdf")

# Iterate over all lots
for ref, result in all_results.items():
    if isinstance(result, dict):
        # Multi-floor (duplex/house)
        for floor_ref, floor_result in result.items():
            print(f"{floor_ref}: {floor_result.living_space}m²")
    else:
        # Single floor
        print(f"{ref}: {result.living_space}m²")
```

### Batch Extraction
```python
from src.extractors.super_extractor import batch_extract

results = batch_extract(["plan1.pdf", "plan2.pdf", "plan3.pdf"])
for ref, data in results.items():
    print(f"{ref}: {data[ref]['living_space']}m²")
```

---

## External Dependencies

### Required
```python
PyMuPDF (fitz)     # PDF text extraction
pytesseract        # OCR
Pillow (PIL)       # Image processing for OCR
```

### Optional (for full functionality)
```python
Tesseract OCR      # Must be installed separately
                   # Windows: https://github.com/UB-Mannheim/tesseract/wiki
```

---

## File Structure

```
src/extractors/super_extractor/
├── __init__.py           # Package initialization & exports
├── models.py             # Data classes (RoomType, ExtractedRoom, ExtractionResult)
├── text_extractor.py     # PyMuPDF + OCR text extraction
├── spatial_extractor.py  # Summary table extraction by position
├── room_normalizer.py    # Room name normalization (500+ patterns)
├── metadata_extractor.py  # Reference, floor, building, promoter
├── plan_validator.py     # Mathematical validation
├── composite_resolver.py # Composite room detection
├── floor_utils.py        # Floor normalization & multi-floor splitting
├── room_inference.py     # Missing room inference
├── deduplication.py      # Room deduplication
├── room_parsers.py       # Surface pattern matching
└── super_extractor.py    # Main orchestrator
```

---

## Version History

- **v3.0** (2026-02-27): Modular architecture with 13 separate files
- **v2.x**: Monolithic super_extractor.py (now split)
- **v1.x**: Basic extraction (deprecated)

---

## Notes

- All modules use Python's built-in `logging` module
- Default logging level is WARNING (configured in `__init__.py`)
- French language support (room names, surface formats)
- Handles OCR errors (missing decimals, noise characters)
- Supports both apartments and houses (with garden, garage, multiple floors)
