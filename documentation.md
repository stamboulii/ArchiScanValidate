# ArchiScan — Project Documentation
> Complete technical reference for the PDF extraction and verification system  
> Last updated: March 2026

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Extraction Pipeline](#3-extraction-pipeline)
4. [PDF Formats Supported](#4-pdf-formats-supported)
5. [Components Reference](#5-components-reference)
6. [Web UI Features](#6-web-ui-features)
7. [Work Done — Session Log](#7-work-done--session-log)
8. [Known Issues](#8-known-issues)
9. [Configuration & Setup](#9-configuration--setup)

---

## 1. Project Overview

ArchiScan is a Python-based system for extracting structured data from French real estate architectural plan PDFs. It automatically identifies room names, surfaces, floor levels, and property references from complex multi-format PDFs, and provides a web interface for human verification and correction before export.

### Core Objective
Convert architectural plan PDFs into structured JSON data with room surfaces, property metadata, and options — ready for import into real estate management systems.

### Target Data Structure
```json
{
  "CS104": {
    "parcelLabel": "CS104",
    "parcelTypeId": "appartment",
    "parcelTypeLabel": "appartement",
    "typology": "T4",
    "floor": "1",
    "living_space": "99.53",
    "surfaceDetail": {
      "SURFACE CHAMBRE_1": 12.54,
      "SURFACE CHAMBRE_2": 10.54,
      "SURFACE CHAMBRE_3": 13.02,
      "SURFACE SEJOUR_CUISINE": 40.54,
      "SURFACE ENTREE": 7.82,
      "SURFACE SALLE_DE_BAIN": 4.58,
      "SURFACE SALLE_EAU": 4.17,
      "SURFACE WC": 2.26,
      "SURFACE BALCON": 27.74,
      "TOTAL_HABITABLE": 99.53,
      "TOTAL_ANNEXE": 27.74,
      "SURFACE TOTAL": 99.53
    },
    "option": {
      "balcony": true,
      "terrace": false,
      "garden": false,
      "parking": false,
      "duplex": false,
      "garage": false,
      "loggia": false,
      "winter garden": false
    },
    "price": "N.C",
    "tva": "",
    "orientation": ""
  }
}
```

---

## 2. Architecture

### Project Structure
```
archiscan/
├── app.py                          # Flask web server
├── extract_cli.py                  # Command-line extraction tool
├── templates/
│   └── index.html                  # Web UI (single-page app)
├── static/
│   └── pdfjs/                      # PDF.js viewer (Mozilla)
│       ├── web/viewer.html
│       └── build/
├── src/
│   └── extractors/
│       └── super_extractor/
│           ├── __init__.py
│           ├── super_extractor.py  # Main orchestrator (600+ lines)
│           ├── models.py           # Data models (RoomType, ExtractedRoom, ExtractionResult)
│           ├── room_normalizer.py  # Room name → normalized key mapping
│           ├── metadata_extractor.py # Reference, floor, promoter detection
│           ├── text_extractor.py   # PyMuPDF + OCR text extraction
│           ├── spatial_extractor.py # Positional table extraction
│           ├── composite_resolver.py # Séjour + Cuisine = Séjour/Cuisine
│           ├── plan_validator.py   # Mathematical validation
│           ├── floor_utils.py      # Floor level detection/normalization
│           ├── room_parsers.py     # Individual room parsing strategies
│           ├── room_inference.py   # Missing room inference
│           └── deduplication.py   # Room deduplication utilities
└── tests/
    └── (to be created — see IMPROVEMENT_PLAN.md)
```

### Technology Stack
| Component | Technology |
|---|---|
| Backend | Python 3.11, Flask |
| PDF Parsing | PyMuPDF (fitz) |
| OCR | Tesseract via pytesseract |
| PDF Viewer | PDF.js v4.0.379 |
| Frontend | Vanilla JavaScript, CSS3 |
| Data Format | JSON |

### Data Flow
```
PDF File
   │
   ▼
TextExtractor          ← PyMuPDF get_text() + OCR fallback
   │
   ▼
SpatialExtractor       ← Positional table detection
   │
   ▼
_rooms_from_two_block_text    ┐
_rooms_from_inverted_pairs    ├── Parser selection (all tried, best wins)
_rooms_from_multiline_text    ┘
   │
   ▼
RoomNormalizer         ← "Séjour/Cuisine" → "sejour_cuisine"
   │
   ▼
CompositeResolver      ← Détecte Séjour + Cuisine = Séjour_Cuisine
   │
   ▼
MetadataExtractor      ← Reference, floor, promoter, living_space
   │
   ▼
PlanValidator          ← Sum validation, typology check
   │
   ▼
ExtractionResult.to_legacy_format()
   │
   ▼
JSON Output / Web UI
```

---

## 3. Extraction Pipeline

### Step 1 — Text Extraction (`text_extractor.py`)
Extracts raw text from each PDF page using PyMuPDF. Falls back to Tesseract OCR when text extraction yields fewer than 20 characters (scanned PDFs).

```python
text_data = self.text_extractor.extract(pdf_path, page_num=page_num)
# Returns: {
#   "text_pymupdf": str,   # vector PDF text
#   "text_ocr": str,       # OCR text (if needed)
#   "raw_pymupdf": str,    # unprocessed PyMuPDF output
#   "primary_source": str  # which source was used
# }
```

### Step 2 — Spatial Extraction (`spatial_extractor.py`)
Analyzes text block positions to detect tabular data. Pairs room names with surfaces based on x/y proximity.

### Step 3 — Two-Block Parser (`_rooms_from_two_block_text`)
Main parser for vector PDFs. Detects consecutive surface number runs and matches them to adjacent name blocks.

**Key logic:**
- Builds "runs" of consecutive numeric lines
- For each run, walks backwards to find the matching name block
- Falls back to forward walk for PDFs where names come after surfaces
- Validates by comparing sum of rooms to declared living space

### Step 4 — Inverted Pairs Parser (`_rooms_from_inverted_pairs`)
Handles PDFs where surface appears BEFORE room name (floor plan drawing labels):
```
25.4
Séjour/Cuisine
12.3
Chambre 1
```

### Step 5 — Room Normalization (`room_normalizer.py`)
Maps raw room names to normalized keys using regex patterns:

| Raw input | Normalized key | Room type |
|---|---|---|
| `Séjour/Cuisine`, `Pièce de vie`, `Réception` | `sejour_cuisine` | LIVING_KITCHEN |
| `Séjour`, `Salon`, `Living` | `sejour` | LIVING_ROOM |
| `Cuisine` | `cuisine` | KITCHEN |
| `Chambre 1`, `Ch. 1` | `chambre_1` | BEDROOM |
| `Salle de bain`, `SdB` | `salle_de_bain` | BATHROOM |
| `Salle d'eau`, `SdE` | `salle_d_eau` | SHOWER_ROOM |
| `WC`, `W.C.` | `wc` | WC |
| `Entrée`, `Hall` | `entree` | ENTRY |
| `Dégagement`, `Dgt` | `degagement` | CIRCULATION |
| `Balcon` | `balcon` | BALCONY |
| `Terrasse` | `terrasse` | TERRACE |
| `Jardin` | `jardin` | GARDEN |
| `Parking` | `parking` | PARKING |
| `Garage` | `garage` | GARAGE |
| `Placard`, `Dressing` | `placard` | STORAGE |

### Step 6 — Metadata Extraction (`metadata_extractor.py`)
Detects:
- **Reference** — apartment/lot identifier (CS104, A002, MAGASIN_1)
- **Floor** — RDC, R+1, R+2, DUPLEX, etc.
- **Building** — B1, B2, etc.
- **Promoter** — detected from signatures (Nexity, Bouygues, etc.)
- **Living space** — declared total from LOGEMENT or SURFACE HABITABLE
- **Annex space** — declared total from SURFACE ANNEXE

### Step 7 — Validation (`plan_validator.py`)
- Calculates sum of interior rooms
- Compares to declared living_space (tolerance: 5%)
- Validates typology (T4 = 3 bedrooms + living room)
- Flags missing essential rooms (WC, living area)

### Step 8 — Multi-Page Handling (`extract_all_pages`)
For PDFs with multiple pages:
1. Scans each page with `_is_plan_page()` to detect architectural plans
2. Groups consecutive pages with same reference into "runs"
3. Each run → single lot (combined if multi-floor, split if distinct apartments)
4. Returns `Dict[reference, ExtractionResult]`

---

## 4. PDF Formats Supported

### Format 1: Two-Block Standard
**Description:** Names in one block, surfaces in adjacent block. Most common format.  
**Example PDFs:** CS104, standard French developers  
**Detection:** Large consecutive surface run with names immediately before it

```
Text layout:                    PyMuPDF output:
┌─────────────────┐            Séjour/Cuisine
│ Séjour/Cuisine  │ 40.54 m²   Chambre 1
│ Chambre 1       │ 12.54 m²   Chambre 2
│ Chambre 2       │ 10.54 m²   40,54
│                 │            12,54
│       40.54     │            10,54
│       12.54     │
│       10.54     │
└─────────────────┘
```

### Format 2: Forward-Names (INK/Groupe Duval)
**Description:** Surfaces column comes BEFORE names column in PDF layout. PyMuPDF reads surfaces first, then names.  
**Example PDFs:** INK-DUVAL_CROIX-JardinDAugustin  
**Detection:** Surface run with names appearing AFTER the block

```
Text layout:                    PyMuPDF output:
┌──────────────────────────┐   14.9 m²  ← annex total
│ SURFACES  │ PIÈCES       │   92.7 m²  ← habitable total  
│ 14.9 m²   │              │   35.0 m²
│ 92.7 m²   │              │   13.4 m²
│ 35.0 m²   │ Pièce de vie │   ...
│ 13.4 m²   │ Chambre 01   │   PIECES
│ ...       │ ...          │   Pièce de vie
└──────────────────────────┘   Chambre 01
```

**Special handling:**
- Strip 2 leading total values from surface list
- Clear floor-plan label runs before processing summary block
- `run_total_val` recomputed from trimmed surf_lines

### Format 3: Inverted Pairs
**Description:** Surface appears on line ABOVE room name (floor plan drawing labels).  
**Detection:** Recurring `SURFACE\nNAME` pattern with ≥5 pairs

```
PyMuPDF output:
25.4
Séjour/Cuisine
12.3
Chambre 1
3.5
Balcon
```

### Format 4: Alternating with Section Headers
**Description:** Sections like "Surfaces habitables" and "Surfaces des annexes" with alternating name/surface.  
**Detection:** Section header keywords present

```
Surfaces habitables
Séjour/Cuisine    40.54
Chambre 1         12.54
...
Surfaces des annexes
Balcon            27.74
```

### Format 5: Spatial Table (OCR/Scanned)
**Description:** Scanned PDFs where text position determines column membership.  
**Handled by:** `SpatialExtractor` + `_rooms_from_multiline_text`

---

## 5. Components Reference

### `SuperExtractor` (main class)

| Method | Description |
|---|---|
| `extract(pdf_path, hint)` | Single entry point — auto-detects single vs multi-page |
| `extract_all_pages(pdf_path, hint)` | Multi-page extraction, returns `Dict[ref, ExtractionResult]` |
| `_extract_single_page(...)` | Core extraction for one page |
| `_rooms_from_two_block_text(text, source, declared_living)` | Main parser for vector PDFs |
| `_rooms_from_inverted_pairs(text, source)` | Parser for surface-before-name format |
| `_rooms_from_multiline_text(text, source)` | Parser for name\nsurface alternating |
| `_rooms_from_regex(text, source)` | Regex fallback parser |
| `_is_plan_page(pdf_path, page_num)` | Detects if page contains architectural plan |
| `_detect_typology(rooms)` | T1/T2/T3/T4/T5 from bedroom count |
| `_detect_property_type(rooms, floor, ...)` | appartment / house / magasin |
| `_combine_multi_floor_results(results)` | Merges RDC+étage pages |

### `RoomNormalizer`

| Method | Description |
|---|---|
| `normalize(name_raw)` | Returns `(norm_key, RoomType, number, is_exterior, confidence)` |
| `reset()` | Resets numbering counters (call per extraction run) |

### `MetadataExtractor`

| Pattern list | Description |
|---|---|
| `REF_PATTERNS` | Regex patterns for apartment reference detection |
| `REF_BLACKLIST` | Known false-positive references to ignore |
| `FLOOR_PATTERNS` | Floor level detection patterns |
| `LIVING_SPACE_PATTERNS` | Surface habitable detection |
| `ANNEX_SPACE_PATTERNS` | Surface annexe detection |

### `ExtractionResult` (model)

| Field | Type | Description |
|---|---|---|
| `reference` | str | Apartment reference (CS104, A002) |
| `parcel_label` | str | Display label (may differ from reference) |
| `floor` | str | Floor level (RDC, R+1, 1, DUPLEX) |
| `typology` | str | T1/T2/T3/T4/T5/Studio/Commercial |
| `living_space` | float | Declared habitable surface in m² |
| `annex_space` | float | Declared annexe surface in m² |
| `rooms` | List[ExtractedRoom] | All detected rooms |
| `page_number` | int | PDF page number (1-based) |
| `validation_errors` | List[str] | Validation failures |
| `validation_warnings` | List[str] | Validation warnings |

---

## 6. Web UI Features

### Upload
- Drag & drop or click to select files
- Single PDF, multi-page PDF, or entire folder
- Parallel extraction with ThreadPoolExecutor (up to 4 workers)
- Automatic multi-lot detection for multi-page PDFs

### Gallery
- Thumbnail cards for each lot (one card per apartment, not per file)
- Status badge: En attente (orange) / Corrigé (green)
- Delete individual lots
- Click to open in split-view

### PDF Viewer (left panel)
- PDF.js embedded viewer with full zoom/navigation controls
- Opens directly on the correct page for each lot
- Previous/Next navigation buttons
- Page counter (X / total)

### Data Form (right panel)
- **Basic fields:** Reference, Type de bien, Orientation, Typologie, Étage, Surface Habitable, État
- **Interior rooms:** Editable name + surface pairs, add/remove rows
- **Exterior spaces:** Same as interior but for balcon, terrasse, jardin, etc.
- **Options:** Checkboxes for balcony, terrace, garden, loggia, parking, garage
- **Validation display:** Shows extraction validation status and errors
- **Include/Exclude toggles:** Check/uncheck entire interior or exterior section

### Actions
- **Confirmer:** Saves current form data and marks lot as corrected
- **Effacer:** Clears all files and resets the session
- **Download JSON:** Exports all confirmed lots as structured JSON
- **Ajouter Champ:** Add custom key-value fields to a lot

### API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `GET /` | GET | Serve main HTML page |
| `POST /api/upload` | POST | Upload and extract PDF files |
| `GET /api/files` | GET | Get all extracted lots from cache |
| `POST /api/corrected` | POST | Save corrected lot data |
| `POST /api/delete` | POST | Soft-delete a file from cache |
| `POST /api/download` | POST | Download confirmed data as JSON |
| `GET /api/file/<file_id>` | GET | Serve PDF file for viewer |
| `POST /api/cache/clear` | POST | Clear all cached data |
| `GET /pdfjs/<path>` | GET | Serve PDF.js static files |

---

## 7. Work Done — Session Log

### Session 1 — Core Extractor Refactor
**Files modified:** `super_extractor.py`, `models.py`, `extract_cli.py`

- Refactored extraction pipeline into modular components
- Added `ExtractionResult` model with `to_legacy_format()`
- Built `_rooms_from_two_block_text()` for vector PDF two-column format
- Added `_rooms_from_inverted_pairs()` for drawing-label PDFs
- Fixed `_build_parcel_data()` and `_build_surface_detail_from_rooms()` in `extract_cli.py`

---

### Session 2 — CS104 Balcon Fix
**File:** `super_extractor.py`  
**PDF:** `PDV-CS104-PLN_.pdf`

**Problem:** Balcon (27.74 m²) was not appearing in extraction output.

**Root cause:** `NOISE_RE` did not filter French metadata strings like `"1er étage"` and `"4 pièces"`. These leaked into the backwards name-walk for the balcon run, accumulating too many candidates → `count_diff > 2` → `_process_ordered()` never called.

**Fix:** Added patterns to `NOISE_RE`:
```python
r'|^\d+\s*(er|ème|e|eme)?\s*(étage|etage|ETAGE)\b'
r'|\b\d+\s*pi[eè]ces?\b'
r'|^(RDC|REZ[\s\-]DE[\s\-]CHAUSS[EÉ]E)$'
```

**Result:** `SURFACE BALCON: 27.74` ✅, `balcony: true` ✅

---

### Session 3 — PP83/PP93 Reference Pollution Fix
**File:** `metadata_extractor.py`  
**PDF:** `INK-DUVAL_CROIX-JardinDAugustin_PlansVente-20230505.pdf`

**Problem:** Every page had `PP83 PP93` in the legend. These matched `\b([A-Z]{1,2}\d{3,4})\b` and were extracted as apartment references instead of the real reference (`A001`, `A002`...).

**Fix:**
1. Added `PP\d+` check in `_extract_first()`:
```python
if re.match(r'^PP\d+$', ref, re.IGNORECASE):
    continue
```
2. Added `PP83`, `PP93` to `REF_BLACKLIST`
3. Added `r"Logement\s*[:\s]*([A-Z]\s*\d{3,4})"` to `REF_PATTERNS` (captures `A 002` → normalized to `A002`)

**Result:** All 44 lots correctly identified as `A001`–`A311` ✅

---

### Session 4 — Forward-Names Format (INK/Groupe Duval)
**File:** `super_extractor.py`  
**PDF:** `INK_page_3.pdf` (A002 apartment)

**Problem:** Rooms extracted with completely wrong surfaces. `CHAMBRE_3: 35.0` when it should be `SEJOUR_CUISINE: 35.0`.

**Root cause:** INK PDFs have a unique layout where the SURFACES column appears before the PIÈCES column. PyMuPDF reads all surfaces first, then all names. The backwards name-walk found floor-plan drawing labels (with wrong dimension values) and paired them with surfaces.

**Fixes applied (in order of discovery):**

1. **Forward fallback** — when backwards walk finds no valid names, scan AFTER the surface block:
```python
if not any(is_valid_name(l) and not is_total(l) for l in candidates):
    surf_end = run[-1] + 1
    forward_lines = lines[surf_end:next_run_start]
    # build fwd_block from forward_lines
```

2. **`surf_lines[2:]` strip** — remove leading total values (annex total + habitable total) before the room values:
```python
surf_lines = surf_lines[2:]
```

3. **`all_rooms.clear()` + `runs_data[:] = [(0.0, []) for _ in runs_data]`** — wipe floor-plan label runs when large summary block found:
```python
if len(surf_lines) > 3:
    all_rooms.clear()
    rooms_before_run = 0
    surf_lines = surf_lines[2:]
    runs_data[:] = [(0.0, []) for _ in runs_data]
```

4. **`run_total_val` recompute** from trimmed surf_lines:
```python
if _used_forward_fallback and surf_lines:
    run_total_val = sum(parse_value(sl) for sl in surf_lines)
```

5. **`count_diff==1` guard** — skip the total-removal handler when forward fallback used:
```python
if count_diff == 1 and not _used_forward_fallback:
```

6. **70% validation threshold** — validate runs whose sum is 70-100% of declared living space:
```python
or (rt > 0 and filter_living_space > 0
    and rt < filter_living_space
    and rt > filter_living_space * 0.7)
```

7. **Sub-table filter fix** — same 70% threshold in keep condition:
```python
or (rt > 0 and filter_living_space > 0
    and rt < filter_living_space
    and rt > filter_living_space * 0.7)
```

8. **`len(surf_lines) > 3` guard** — prevent single-surface runs (balcon) from triggering full reset:
```python
if len(surf_lines) > 3:
    all_rooms.clear()
    ...
```

**Result:** `SEJOUR_CUISINE: 35.0`, `CHAMBRE_1: 13.4`, `CHAMBRE_3: 12.0` ✅

**Regression verified:** CS104 balcon still correct after all fixes ✅

---

### Session 5 — Web UI Development
**Files:** `app.py`, `templates/index.html`

**Features built:**
- Flask backend with file upload, extraction cache, download endpoint
- Split-view layout: PDF viewer (left) + editable form (right)
- Gallery with per-lot cards and status badges
- Parallel extraction with ThreadPoolExecutor
- JSON export of confirmed lots only

---

### Session 6 — Multi-Lot PDF UI Fixes
**Files:** `app.py`, `templates/index.html`

**Problem 1: Multi-lot PDFs broken in gallery**  
When a PDF had multiple lots, sub-lot `file_id` was `"file_0_A002"` but `/api/file/file_0_A002` returned 404.

**Fix:** Updated `/api/file/<path:file_id>` to strip lot suffix and find parent:
```python
parts = base_id.rsplit('_', 1)
if len(parts) == 2 and parts[0] in extraction_cache:
    base_id = parts[0]
```

**Problem 2: Reference showing filename instead of lot reference**  
Form showed full filename instead of `A311`.

**Fix:** Use `parcelLabel` from extracted data:
```python
const key = lotData.parcelLabel || lotRef || extractLotReference(file.filename);
```

---

### Session 7 — PDF.js Page Navigation
**Files:** `app.py`, `templates/index.html`, `static/pdfjs/`

**Problem:** PDF viewer always opened page 1 regardless of which lot was selected.

**Fix steps:**
1. Downloaded PDF.js v4.0.379 to `static/pdfjs/`
2. Added Flask route to serve PDF.js files
3. Replaced `<embed>` with PDF.js iframe
4. Injected `page_number` from `ExtractionResult` into lot data
5. Used `setTimeout(50ms)` delay before setting iframe src to ensure PDF.js initializes before jumping to page

**Final implementation:**
```javascript
pdfContainer.innerHTML = '';
const iframe = document.createElement('iframe');
iframe.style.cssText = 'width:100%; height:100%; min-height:600px; border:none;';
pdfContainer.appendChild(iframe);
setTimeout(() => {
    iframe.src = `/pdfjs/web/viewer.html?file=${pdfUrl}#page=${pageNum}`;
}, 50);
```

**Result:** Each lot card opens the PDF directly on its correct page ✅

---

## 8. Known Issues

### Active Issues

| Issue | Severity | Description |
|---|---|---|
| `Cellier` → `placard` | Medium | Cellar rooms show as PLACARD instead of CAVE/STORAGE |
| `Dressing` → `placard` | Medium | Dressing rooms show as PLACARD |
| Rangement missing | Low | Last room in INK PDFs (0.9 m²) sometimes filtered out |
| `duplex: true` false positive | Low | INK A002 incorrectly flagged as duplex |
| 44-page INK extraction | Medium | Currently extracts all 44 lots correctly but reference detection relies on blacklist, not position |

### Fixed Issues (Historical)

| Issue | Session | Fix |
|---|---|---|
| Balcon missing from CS104 | Session 2 | Added floor/pièces patterns to NOISE_RE |
| PP83/PP93 reference pollution | Session 3 | Added to REF_BLACKLIST, added Logement pattern |
| Wrong rooms in INK forward-names PDFs | Session 4 | 8-step forward-fallback implementation |
| Multi-lot PDF viewer 404 | Session 6 | Strip lot suffix in /api/file endpoint |
| Reference showing filename | Session 6 | Use parcelLabel from extracted data |
| PDF viewer always page 1 | Session 7 | PDF.js + setTimeout + page_number injection |

---

## 9. Configuration & Setup

### Requirements
```
python >= 3.11
flask
pymupdf (fitz)
pytesseract
tesseract-ocr (system install)
pillow
werkzeug
```

### Installation
```bash
# Clone/navigate to project
cd C:\Users\MSI\Documents\archiscan

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate

# Install dependencies
pip install flask pymupdf pytesseract pillow werkzeug

# Install Tesseract (Windows)
# Download from: https://github.com/UB-Mannheim/tesseract/wiki
# Add to PATH

# PDF.js is already in static/pdfjs/
```

### Running the Web UI
```bash
python app.py
# Open http://127.0.0.1:5000
```

### Running CLI Extraction
```bash
# Single PDF
python extract_cli.py 'path/to/plan.pdf' -p -q

# Multi-page PDF
python extract_cli.py 'path/to/multipage.pdf' -p -q -a

# With reference hint
python extract_cli.py 'path/to/plan.pdf' -r CS104 -p -q
```

### CLI Flags
| Flag | Description |
|---|---|
| `-p` | Pretty-print JSON output |
| `-q` | Quiet mode (suppress logs) |
| `-a` | Extract all pages (multi-page mode) |
| `-r REF` | Reference hint for extraction |
| `-v` | Verbose logging |

### Environment Variables
None required. All configuration is in `app.py` constants:
```python
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max upload
app.config['UPLOAD_FOLDER'] = tempfile.mkdtemp()       # Temp folder for uploads
```

---

## Appendix — Debugging Tips

### Check what a PDF contains
```bash
python dump_text.py 'path/to/file.pdf' > dump_out.txt
```

### Test extraction with debug output
```bash
python extract_cli.py 'path/to/file.pdf' -p -v 2>&1
```

### Check which parser is winning
Add to `_extract_single_page` temporarily:
```python
print(f"rooms_tb={len(rooms_tb)}, rooms_inv={len(rooms_inv)}", file=sys.stderr)
```

### Verify page numbers in multi-page extraction
```bash
python -c "
from src.extractors.super_extractor import SuperExtractor
e = SuperExtractor()
results = e.extract_all_pages('path/to/file.pdf')
for ref, r in results.items():
    print(f'{ref}: page={r.page_number}, living={r.living_space}')
"
```

### Check Flask cache state
Visit: `http://127.0.0.1:5000/api/files` in browser to see all cached lots as JSON.