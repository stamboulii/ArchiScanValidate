# SuperExtractor - Step-by-Step Extraction Process

The SuperExtractor is an architectural plan extraction system that extracts structured data from French real estate floor plans (PDF format). Here's the complete pipeline explained step by step:

## Overall Pipeline (9 Steps)

```
SuperExtractor
  ├── 1. TextExtractor     → Raw text (PyMuPDF + OCR)
  ├── 2. SpatialExtractor  → Summary table by spatial position
  ├── 3. RoomNormalizer    → Normalize room names (500+ patterns)
  ├── 4. CompositeResolver → Detect "Reception = Séjour + Cuisine"
  ├── 5. MetadataExtractor → Reference, floor, building, promoter
  ├── 6. PlanValidator     → Mathematical validation
  ├── 7. RoomInference     → Infer missing rooms from surface gaps
  └── 8. Deduplication     → Remove duplicate rooms
```

---

## Step-by-Step Extraction for Each Value

### 1. Lot Reference (e.g., "A008")

- **Module**: `MetadataExtractor.extract()` (metadata_extractor.py:415)
- **Process**: Uses regex patterns in priority order:
  - `r"Logement\s*[:\s]*([A-Z]\s*\d{3,4})"` → "Logement: A101"
  - `r"Appartement\s+([A-Z]\d{2,4})"` → "Appartement A008"
  - `r"LOT\s*[:\s]*([A-Z]?\d{2,4})"` → "LOT: A008"
  - `r"\b([A-Z]\d{3,4})\b"` → Standalone "A008"
- **Blacklist filtering**: Rejects false positives like "R1", "R2", "T1", "T2", "DATE", "TYPE", etc.

---

### 2. Property Type (apartment/house)

- **Module**: `RoomInference.detect_property_type()` (room_inference.py:519)
- **Process**: Returns "house" or "appartment" based on:
  - Has garden, cellar, or parking → "house"
  - Multi-floor (floor contains "+") → "house"
  - Otherwise → "appartment"

---

### 3. Typology (T1, T2, T3, etc.)

- **Module**: `RoomInference.detect_typology()` (room_inference.py:516)
- **Process**: Counts bedrooms + living room:
  - 0 bedrooms → T1
  - 1 bedroom → T2
  - 2 bedrooms → T3
  - 3 bedrooms → T4
  - etc.

---

### 4. Floor Level (RDC, R+1, R+2, etc.)

- **Module**: `MetadataExtractor.extract()` (metadata_extractor.py:394-402) + `FloorUtils.normalize_floor_label()` (floor_utils.py:568)
- **Process**: Regex patterns:
  - `r"\bRDC\b"` → "RDC"
  - `r"\bR\+1\b"` → "R+1"
  - `r"\bR\+(\d+)\b"` → captures number
  - `r"NIV\s*(\d{2,3})\b"` → converts "NIV 01" to "RDC"
  - `r"REZ\s*DE\s*CHAUSSEE"` → "RDC"

---

### 5. Living Space (habitable surface in m²)

- **Module**: `SpatialExtractor.extract_from_pages()` (spatial_extractor.py:268)
- **Process**:
  1. Searches for keywords: "TOTAL SURFACE HABITABLE", "SURFACE HABITABLE", "TOTAL SH"
  2. Extracts the numeric value following these keywords
  3. Converts French comma to dot: `24,50` → `24.50`

---

### 6. Annex Space (exterior surfaces in m²)

- **Module**: `SpatialExtractor.extract_from_pages()` (spatial_extractor.py:255-258)
- **Process**:
  1. Searches for keywords: "TOTAL SURFACE ANNEXE", "SURFACE ANNEXE", "TOTAL ANNEXE", "TOTAL EXTERIEURS"
  2. Extracts the numeric value

---

### 7. Room Inventory (bedrooms, bathrooms, kitchen, etc.)

- **Module**: `SpatialExtractor` (spatial_extractor.py:238) + `RoomNormalizer` (room_normalizer.py:316)
- **Process** (5 sub-steps):

#### 7a. Raw Room Extraction (SpatialExtractor)

- **5 regex patterns** for room/surface matching:
  - **Standard**: `"Chambre 12.50 m²"`
  - **Collé**: `"ENTREE/DGT 9,85m²"` (no space before m²)
  - **Ultra-collé**: `"CELLIER1,78m²"` (no space at all)
  - **Inverted**: `"12.50 m² CHAMBRE"`
  - **Integer**: `"CHAMBRE 397 m?"` (OCR error - adds decimal)

#### 7b. OCR Error Fixing

- **Module**: `SpatialExtractor._fix_missing_decimal()` (spatial_extractor.py:300-303)
- Fixes OCR errors dropping decimal points:
  - `"397"` → `"3.97"` (3 digits)
  - `"1226"` → `"12.26"` (4 digits)

#### 7c. Room Name Normalization (RoomNormalizer)

- **500+ patterns** normalize room names to standard format:
  - `r"^(SEJOUR|SÉJOUR)\s*/?\s*CUISINE"` → "sejour_cuisine"
  - `r"^CHAMBRE\s*(\d+)$"` → "chambre_1", "chambre_2", etc.
  - `r"^SDB\s*/\s*WC"` → "salle_de_bain"

#### 7d. Composite Room Detection (CompositeResolver)

- **Module**: `CompositeResolver.resolve()` (composite_resolver.py:483)
- Detects combined rooms:
  - "Séjour + Cuisine" → marked as "reception" composite
  - "Séjour/Cuisine" → kept as "sejour_cuisine"

#### 7e. Deduplication

- **Module**: `DeduplicationUtils.final_dedup()` (deduplication.py:540)
- Two-pass deduplication:
  1. By exact name_normalized (keeps highest confidence)
  2. By (type, number, surface) tuple

---

### 8. Exterior Spaces (balcony, garden, garage, etc.)

- **Module**: `RoomNormalizer.normalize()` (room_normalizer.py:351)
- **Process**: Detected via `is_exterior` flag from patterns:
  - `r"^JARDIN$"` → RoomType.GARDEN, is_exterior=True
  - `r"^GARAGE$"` → RoomType.PARKING, is_exterior=True
  - `r"^BALCON"` → RoomType.BALCONY, is_exterior=True

---

### 9. Missing Room Inference

- **Module**: `RoomInference` (room_inference.py:500)
- **Process**: Infers missing rooms from surface gaps:
  - **Living room**: If gap in [12, 60] m² and no living room exists
  - **Bedroom**: If gap in [5, 40] m² and at least one bedroom exists
  - Formula: `declared_surface - sum(rooms) = gap`

---

### 10. Validation

- **Module**: `PlanValidator.validate()` (plan_validator.py:452)
- **5 validation checks**:
  1. **Surface sum**: Checks if calculated habitable surface matches declared (±12% = error, ±8% = warning)
  2. **Composite sums**: Verifies composite rooms sum correctly
  3. **Typology**: Checks T1/T2/T3 matches bedroom count
  4. **Ranges**: Verifies room surfaces are plausible (per SURFACE_RANGES)
  5. **Basic**: Ensures at least one room detected

---

## Output Structure (ExtractionResult)

```python
@dataclass
class ExtractionResult:
    reference: str           # "A008"
    parcel_label: str        # "M011"
    property_type: str       # "appartment" or "house"
    typology: str            # "T2", "T3", etc.
    floor: str               # "RDC", "R+1", etc.
    living_space: float      # 45.50
    annex_space: float       # 5.20
    rooms: List[ExtractedRoom]
    # ...
```

The final output can be converted to legacy dict format via `result.to_legacy_format()` (models.py:139).

---

## Room Types (21 types)

| Enum Value | French Label |
|------------|--------------|
| ENTRY | entrée |
| LIVING_ROOM | séjour |
| KITCHEN | cuisine |
| LIVING_KITCHEN | séjour_cuisine |
| RECEPTION | réception |
| BEDROOM | chambre |
| BATHROOM | salle_de_bain |
| SHOWER_ROOM | salle_d_eau |
| WC | wc |
| CIRCULATION | circulation |
| STORAGE | storage |
| DRESSING | dressing |
| BALCONY | balcon |
| TERRACE | terrasse |
| GARDEN | jardin |
| LOGGIA | loggia |
| PATIO | patio |
| PARKING | parking |
| CELLAR | cave |
| UNKNOWN | unknown |

---

## Expected Surface Ranges (per room type)

| Room Type | Min (m²) | Max (m²) |
|-----------|----------|----------|
| ENTRY | 1.0 | 25.0 |
| LIVING_ROOM | 8.0 | 80.0 |
| BEDROOM | 5.0 | 40.0 |
| BATHROOM | 2.0 | 20.0 |
| GARDEN | 5.0 | 5000.0 |
| ... | ... | ... |

---

## File Structure

```
src/extractors/super_extractor/
├── __init__.py           # Package initialization & exports
├── models.py             # Data classes (RoomType, ExtractedRoom, ExtractionResult)
├── text_extractor.py     # PyMuPDF + OCR text extraction
├── spatial_extractor.py  # Summary table extraction by position
├── room_normalizer.py    # Room name normalization (500+ patterns)
├── metadata_extractor.py # Reference, floor, building, promoter
├── plan_validator.py     # Mathematical validation
├── composite_resolver.py # Composite room detection
├── floor_utils.py        # Floor normalization & multi-floor splitting
├── room_inference.py     # Missing room inference
├── deduplication.py      # Room deduplication
├── room_parsers.py       # Surface pattern matching
└── super_extractor.py    # Main orchestrator
```
