# ArchiScan — Complete Improvement Plan
> Last updated: March 2026  
> Goal: Handle all French real estate PDF formats with correct extraction and minimal manual correction

---

## Current Status

| Component | Status | Notes |
|---|---|---|
| Two-block format (CS104) | ✅ Working | Standard format |
| Forward-names format (INK/Groupe Duval) | ✅ Working | Recently fixed |
| Inverted-pairs format | ✅ Working | Drawing labels |
| Alternating name/surface | ✅ Working | Section-based |
| Multi-page PDF (44 pages) | ✅ Working | Per-lot navigation |
| PDF viewer with page jump | ✅ Working | PDF.js integration |
| Reference detection | ✅ Working | PP83/PP93 fixed |
| Web UI verification workflow | ✅ Working | Split-view + confirm |
| Ground truth test suite | ❌ Missing | Highest risk |
| Spatial-aware parsing | ❌ Missing | Biggest impact |
| Format auto-detection | ❌ Missing | Medium impact |
| Confidence scoring | ❌ Missing | UI quality |

---

## STEP 1 — Build a Ground Truth Test Suite
> **Time:** 1-2 days  
> **Impact:** Prevents regressions, saves hours of debugging  
> **Do this before any other change**

### Why it's critical
Every fix risks breaking something else. Today: fix INK A002 → CS104 loses balcon. Without tests, you're flying blind.

### File structure
```
tests/
  ground_truth.json
  test_extraction.py
  test_pdfs/
    CS104.pdf
    INK_A002.pdf
    DEDC_A18.pdf
    moroccan_01.pdf
    ...
```

### `tests/ground_truth.json`
```json
[
  {
    "pdf": "test_pdfs/CS104.pdf",
    "expected": {
      "reference": "CS104",
      "typology": "T4",
      "floor": "1",
      "living_space": 99.53,
      "annex_space": 27.74,
      "rooms": {
        "SURFACE CHAMBRE_1": 12.54,
        "SURFACE CHAMBRE_2": 10.54,
        "SURFACE CHAMBRE_3": 13.02,
        "SURFACE SEJOUR_CUISINE": 40.54,
        "SURFACE ENTREE": 7.82,
        "SURFACE SALLE_DE_BAIN": 4.58,
        "SURFACE SALLE_EAU": 4.17,
        "SURFACE WC": 2.26,
        "SURFACE BALCON": 27.74
      },
      "options": {
        "balcony": true
      }
    }
  },
  {
    "pdf": "test_pdfs/INK_A002.pdf",
    "expected": {
      "reference": "A002",
      "typology": "T4",
      "floor": "0",
      "living_space": 107.6,
      "annex_space": 14.9,
      "rooms": {
        "SURFACE SEJOUR_CUISINE": 35.0,
        "SURFACE CHAMBRE_1": 13.4,
        "SURFACE CHAMBRE_3": 12.0,
        "SURFACE CHAMBRE_2": 9.2,
        "SURFACE TERRASSE": 14.9
      },
      "options": {
        "terrace": true
      }
    }
  }
]
```

### `tests/test_extraction.py`
```python
import json
import pytest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.extractors.super_extractor import SuperExtractor

GROUND_TRUTH = json.loads(Path("tests/ground_truth.json").read_text())
extractor = SuperExtractor()

@pytest.mark.parametrize("case", GROUND_TRUTH, ids=[c["pdf"] for c in GROUND_TRUTH])
def test_extraction(case):
    result = extractor.extract(f"tests/{case['pdf']}")
    legacy = result.to_legacy_format()
    ref = case["expected"]["reference"]
    
    assert ref in legacy, f"Reference '{ref}' not found in output"
    data = legacy[ref]
    
    # Check living space
    if "living_space" in case["expected"]:
        actual = float(data.get("living_space", 0))
        expected = case["expected"]["living_space"]
        assert abs(actual - expected) < 0.5, \
            f"living_space: got {actual}, expected {expected}"
    
    # Check typology
    if "typology" in case["expected"]:
        assert data.get("typology") == case["expected"]["typology"], \
            f"typology: got {data.get('typology')}, expected {case['expected']['typology']}"
    
    # Check rooms
    surface_detail = data.get("surfaceDetail", {})
    for room_key, expected_val in case["expected"].get("rooms", {}).items():
        actual_val = surface_detail.get(room_key, 0)
        assert abs(actual_val - expected_val) < 0.1, \
            f"{room_key}: got {actual_val}, expected {expected_val}"
    
    # Check options
    options = data.get("option", {})
    for opt_key, expected_val in case["expected"].get("options", {}).items():
        assert options.get(opt_key) == expected_val, \
            f"option.{opt_key}: got {options.get(opt_key)}, expected {expected_val}"

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
```

### Run before every change
```bash
pytest tests/test_extraction.py -v
```

---

## STEP 2 — Simplify `extract_pdf()` in `app.py`
> **Time:** 2 hours  
> **Impact:** Removes 80 lines of fragile defensive code

### Problem
Current function is 100+ lines of nested `isinstance` checks working around inconsistent `to_legacy_format()` output. Any change to the extractor breaks the app silently.

### Solution
Replace the entire `extract_pdf()` function with:

```python
def extract_pdf(file_path, reference_hint=None):
    """Extract data from a PDF file using SuperExtractor."""
    try:
        all_results = extractor.extract_all_pages(file_path, reference_hint)
        output = {}
        for ref, result in all_results.items():
            legacy = result.to_legacy_format()
            # to_legacy_format returns {ref: lot_data}
            lot_data = legacy.get(ref) or list(legacy.values())[0]
            lot_data['page_number'] = getattr(result, 'page_number', 1)
            output[ref] = lot_data
        return output
    except Exception as e:
        logger.error(f"Extraction error for {file_path}: {e}")
        import traceback
        traceback.print_exc()
        return {"EXTRACTION_ERROR": {"error": str(e)}}
```

### What to fix in `to_legacy_format()` first
For this simplification to work, `to_legacy_format()` must always return `{reference: lot_data}` with a flat structure. Check `models.py` and ensure:
- No nested `floor_key` entries in the flat case
- `page_number` is included in the output dict
- `parcelLabel` matches the reference key

---

## STEP 3 — Spatial-Aware Parsing
> **Time:** 3-5 days  
> **Impact:** Solves most remaining extraction problems permanently

### Why this is the most important change
Currently `get_text()` collapses all spatial information into a flat string. This forces complex heuristics (gap counting, noise detection, forward/backward walks, `NOISE_RE` patterns) to reconstruct what the PDF layout already knows.

With `get_text("dict")`, you get x/y coordinates for every text span — separating legend from table from floor plan becomes trivial.

### What PyMuPDF dict format provides
```python
page = doc[page_num]
data = page.get_text("dict")

# Each block has:
# {
#   "bbox": [x0, y0, x1, y1],   ← position on page
#   "lines": [{
#     "spans": [{
#       "text": "Pièce de vie",
#       "size": 10.0,             ← font size
#       "flags": 20,              ← bold=1, italic=2
#       "bbox": [x0, y0, x1, y1]
#     }]
#   }]
# }
```

### Font size rules (observed across PDFs)
| Font size | Content type |
|---|---|
| 14pt+ | Section headers, apartment reference (A002) |
| 10-12pt | Room names in summary table |
| 8-9pt | Legend codes (PP83, PF, VR), footnotes |
| 6-7pt | Legal text, noise |

### Column rules (observed across PDFs)
| X position | Content type |
|---|---|
| 0-30% of page width | Legend (left panel) |
| 30-65% of page width | Floor plan drawing |
| 65-100% of page width | Summary table (names + surfaces) |

### Implementation plan

**Phase 3a — New text extraction method**

Add to `TextExtractor`:
```python
def extract_spatial_blocks(self, pdf_path, page_num=None):
    """
    Extract text blocks with position and font info.
    Returns list of {text, x, y, width, height, font_size, is_bold}
    """
    import fitz
    doc = fitz.open(pdf_path)
    pages = [doc[page_num]] if page_num is not None else doc
    
    all_blocks = []
    for page in pages:
        page_width = page.rect.width
        data = page.get_text("dict")
        
        for block in data.get("blocks", []):
            if block.get("type") != 0:  # 0 = text block
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    if not text:
                        continue
                    x0, y0, x1, y1 = span["bbox"]
                    all_blocks.append({
                        "text": text,
                        "x": x0,
                        "y": y0,
                        "x_pct": x0 / page_width,  # 0.0 to 1.0
                        "font_size": span.get("size", 10),
                        "is_bold": bool(span.get("flags", 0) & 1),
                        "page_width": page_width,
                    })
    
    doc.close()
    return sorted(all_blocks, key=lambda b: (round(b["y"]/5)*5, b["x"]))
```

**Phase 3b — Column-based room/surface separator**

```python
def extract_summary_table(self, blocks, page_width):
    """
    Extract room names and surfaces from the right-side summary table.
    Ignores legend codes (left side) and drawing labels (center).
    """
    # Filter to right column only (summary table)
    table_blocks = [b for b in blocks if b["x_pct"] > 0.60]
    
    # Filter out legend/noise by font size
    table_blocks = [b for b in table_blocks if b["font_size"] >= 8.0]
    
    # Separate names from surfaces by x position within the table
    # Names are typically left-aligned, surfaces right-aligned
    table_x_values = [b["x_pct"] for b in table_blocks]
    if not table_x_values:
        return [], []
    
    mid_x = (min(table_x_values) + max(table_x_values)) / 2
    name_blocks = [b for b in table_blocks if b["x_pct"] < mid_x + 0.05]
    surf_blocks  = [b for b in table_blocks if b["x_pct"] >= mid_x - 0.05]
    
    # Sort by y position (top to bottom)
    names = [b["text"] for b in sorted(name_blocks, key=lambda b: b["y"])]
    surfs = [b["text"] for b in sorted(surf_blocks,  key=lambda b: b["y"])]
    
    return names, surfs
```

**Phase 3c — Reference detection by font size**

```python
def detect_reference_spatial(self, blocks):
    """
    Find apartment reference using font size and position.
    Large font in top-right = apartment reference (A002, CS104).
    Small font anywhere = legend codes (PP83) — ignore.
    """
    # Look for large text in right panel
    candidates = [
        b for b in blocks
        if b["x_pct"] > 0.60          # right panel
        and b["font_size"] >= 12.0     # large font
        and re.match(r'^[A-Z]\d{2,4}$', b["text"])  # reference pattern
    ]
    
    if candidates:
        # Return the one closest to top of page
        return min(candidates, key=lambda b: b["y"])["text"]
    
    return None
```

### What this eliminates
- `NOISE_RE` — no longer needed, filter by position instead
- `all_rooms.clear()` hack — forward vs backward becomes trivial
- `PP83`/`PP93` blacklist — they're in left column, we ignore left column
- Most of the gap-counting backward walk logic
- The `_used_forward_fallback` flag and all related complexity

---

## STEP 4 — PDF Format Auto-Detection
> **Time:** 1 day  
> **Impact:** Eliminates cross-parser interference

### Problem
Currently all parsers run and results are merged/deduped. This causes conflicts when two parsers return different values for the same room.

### Solution
Detect format first, run only the right parser:

```python
def detect_pdf_format(self, lines: list) -> str:
    """
    Detect PDF format from line patterns.
    Returns: 'two_block' | 'forward_names' | 'inverted_pairs' | 'alternating'
    """
    SURFACE_RE = re.compile(r'^\d+[,\.]\d+\s*(?:m[²2]?)?\s*$')
    
    def is_surf(l): return bool(SURFACE_RE.match(l))
    def is_name(l): return bool(re.search(r'[A-Za-zÀ-ÿ]{3,}', l)) and not is_surf(l)
    
    # Count pattern occurrences in first 200 lines
    sample = lines[:200]
    
    # Forward-names: surface then name
    fwd_pairs = sum(1 for i in range(len(sample)-1)
                    if is_surf(sample[i]) and is_name(sample[i+1]))
    
    # Standard: name then surface  
    std_pairs = sum(1 for i in range(len(sample)-1)
                    if is_name(sample[i]) and is_surf(sample[i+1]))
    
    # Two-block: large consecutive surface run (>3 surfaces in a row)
    surf_indices = [i for i, l in enumerate(sample) if is_surf(l)]
    runs = []
    if surf_indices:
        cur = [surf_indices[0]]
        for idx in surf_indices[1:]:
            if idx == cur[-1] + 1:
                cur.append(idx)
            else:
                runs.append(cur); cur = [idx]
        runs.append(cur)
    large_runs = [r for r in runs if len(r) > 3]
    
    if large_runs:
        if fwd_pairs > std_pairs:
            return 'forward_names'    # INK/Groupe Duval
        return 'two_block'            # CS104, standard
    
    if std_pairs > 5:
        return 'alternating'          # name/surface alternating
    
    if fwd_pairs > 5:
        return 'inverted_pairs'       # surface/name inverted
    
    return 'unknown'

def _extract_single_page(self, ...):
    ...
    lines = [l.strip() for l in raw_pymupdf.split('\n') if l.strip()]
    pdf_format = self.detect_pdf_format(lines)
    logger.info(f"  📋 Detected format: {pdf_format}")
    
    # Route to appropriate parser only
    if pdf_format == 'two_block':
        rooms_tb, tb_living, tb_annex = self._rooms_from_two_block_text(...)
    elif pdf_format == 'forward_names':
        rooms_tb, tb_living, tb_annex = self._rooms_from_forward_names(...)
    elif pdf_format == 'inverted_pairs':
        rooms_tb, tb_living, tb_annex = self._rooms_from_inverted_pairs(...)
    elif pdf_format == 'alternating':
        rooms_tb, tb_living, tb_annex = self._rooms_from_alternating(...)
    ...
```

---

## STEP 5 — Promoter Profile System
> **Time:** 0.5 days  
> **Impact:** Skips irrelevant parsers, faster + more reliable

```python
# In metadata_extractor.py or a new promoter_profiles.py

PROMOTER_PROFILES = {
    "Groupe Duval": {
        "format": "forward_names",
        "ref_pattern": r"Logement\s*[:\s]*([A-Z]\s*\d{3,4})",
        "ref_blacklist_re": r"^PP\d+$",
        "legend_position": "left",
        "summary_position": "right",
    },
    "Faubourg Immobilier": {
        "format": "two_block",
        "ref_pattern": r"\b([A-Z]\d{2,4})\b",
    },
    "Nexity": {
        "format": "alternating",
    },
    "Bouygues Immobilier": {
        "format": "two_block",
    },
    "Pitch Promotion": {
        "format": "two_block",
    },
}

def get_promoter_profile(text: str) -> dict:
    """Detect promoter and return their PDF format profile."""
    for promoter, profile in PROMOTER_PROFILES.items():
        if re.search(re.escape(promoter), text, re.IGNORECASE):
            logger.info(f"  🏢 Promoter detected: {promoter} → format: {profile['format']}")
            return profile
    return {}
```

Usage in `_extract_single_page`:
```python
profile = get_promoter_profile(primary_text)
format_hint = profile.get("format")  # use this to skip other parsers
```

---

## STEP 6 — Room Normalizer Fixes
> **Time:** 1 hour  
> **Impact:** Correct room labels in output

### Current mapping errors

| Raw name | Current | Expected |
|---|---|---|
| `Cellier` | `placard` | `storage` |
| `Dressing` | `placard` | `dressing` |
| `Rangement` | `circulation` | `storage` |
| `Dgt` | `circulation` | `degagement` |

### Fix in `room_normalizer.py`

Find the `DRESSING` pattern and change:
```python
# FROM:
(r"^(DRESSING|ARMOIRE)$", "placard", RoomType.STORAGE, False),

# TO:
(r"^(DRESSING|ARMOIRE|PENDERIE)$", "dressing", RoomType.DRESSING, False),
```

Find or add `CELLIER`:
```python
# ADD:
(r"^(CELLIER|CAVE|CAVEAU)$", "cave", RoomType.CELLAR, False),
```

Find `RANGEMENT`:
```python
# FROM: maps to circulation
# TO:
(r"^(RANGEMENT|RGT|RNG)$", "storage", RoomType.STORAGE, False),
```

---

## STEP 7 — UI Improvements
> **Time:** 1 day  
> **Impact:** Faster verification workflow

### 7.1 Keyboard Shortcuts

Add to `index.html` script section:

```javascript
document.addEventListener('keydown', (e) => {
    // Don't trigger when typing in inputs
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;
    
    if (e.key === 'ArrowRight' && currentIndex < files.length - 1) {
        loadFile(currentIndex + 1);
    }
    if (e.key === 'ArrowLeft' && currentIndex > 0) {
        loadFile(currentIndex - 1);
    }
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        confirmBtn.click();
    }
    if (e.key === 'Delete' && e.shiftKey) {
        deleteFile(new Event('click'), currentIndex);
    }
});
```

Show shortcut hints in UI:
```html
<div style="font-size:0.75rem; color:var(--text-secondary); margin-top:0.5rem;">
    ← → Navigate &nbsp;|&nbsp; Ctrl+Enter Confirm &nbsp;|&nbsp; Shift+Del Delete
</div>
```

### 7.2 Auto-Recalculate Totals

Add to form section — updates `TOTAL_HABITABLE` and `living_space` as user edits:

```javascript
function recalcTotals() {
    let habitableSum = 0;
    let annexeSum = 0;
    
    document.querySelectorAll('#interiorRoomsContainer .room-item').forEach(item => {
        const val = parseFloat(item.querySelectorAll('input')[1].value) || 0;
        habitableSum += val;
    });
    
    document.querySelectorAll('#exteriorRoomsContainer .room-item').forEach(item => {
        const val = parseFloat(item.querySelectorAll('input')[1].value) || 0;
        annexeSum += val;
    });
    
    const livingSpaceField = document.getElementById('field_living_space');
    if (livingSpaceField) {
        livingSpaceField.value = habitableSum.toFixed(2);
    }
}

// Attach to all room inputs (call after form renders)
function attachRecalcListeners() {
    document.addEventListener('input', (e) => {
        if (e.target.closest('.room-item') && e.target.type === 'number') {
            recalcTotals();
        }
    });
}
```

### 7.3 Progress Counter

In `updateGallery()`, update the counter:

```javascript
function updateGallery() {
    // ... existing code ...
    
    const confirmed = files.filter(f => 
        extractionData[f.file_id]?.corrected
    ).length;
    
    fileCounter.textContent = `${confirmed} / ${files.length} confirmés`;
    
    // Change color based on progress
    const pct = confirmed / files.length;
    fileCounter.style.color = pct === 1 ? 'var(--secondary)' : 
                               pct > 0.5 ? 'var(--warning)' : 
                               'var(--text-secondary)';
}
```

### 7.4 Bulk Confirm Button

Add to header actions:
```html
<button class="btn btn-secondary" id="confirmAllBtn">
    ✓ Confirmer tout
</button>
```

```javascript
document.getElementById('confirmAllBtn').addEventListener('click', async () => {
    if (!confirm(`Confirmer les ${files.length} fichiers avec les données extraites ?`)) return;
    
    for (let i = 0; i < files.length; i++) {
        currentIndex = i;
        loadFile(i);
        await new Promise(r => setTimeout(r, 100)); // let form render
        confirmBtn.click();
        await new Promise(r => setTimeout(r, 50));
    }
    
    showToast('success', `${files.length} fichiers confirmés`);
});
```

### 7.5 Extraction Confidence Badge

In `get_files()` backend, include confidence:
```python
lot_data['confidence'] = getattr(result, 'extraction_confidence', 0.8)
```

In gallery card:
```javascript
const confidence = lotData?.confidence || 0;
const confPct = Math.round(confidence * 100);
const confColor = confidence > 0.9 ? '#10B981' : confidence > 0.7 ? '#F59E0B' : '#EF4444';
card.innerHTML += `
    <div style="font-size:0.7rem; color:${confColor}; text-align:center; margin-top:4px;">
        ${confPct}% confiance
    </div>
`;
```

---

## STEP 8 — Persistent Cache
> **Time:** 1 hour  
> **Impact:** No data loss on server restart

### Problem
If Flask restarts mid-session, all extraction results and corrections are lost.

### Solution

In `app.py`:

```python
import json
from pathlib import Path

CACHE_FILE = Path(tempfile.gettempdir()) / 'archiscan_cache.json'

def load_cache():
    """Load cache from disk on startup."""
    global extraction_cache
    if CACHE_FILE.exists():
        try:
            extraction_cache = json.loads(CACHE_FILE.read_text(encoding='utf-8'))
            print(f"  ✅ Cache loaded: {len(extraction_cache)} files")
        except Exception as e:
            print(f"  ⚠️ Cache load failed: {e}")
            extraction_cache = {}

def save_cache():
    """Save cache to disk after each update."""
    try:
        # Don't serialize filepath objects that don't JSON-serialize
        serializable = {}
        for k, v in extraction_cache.items():
            entry = {key: val for key, val in v.items() if key != 'filepath'}
            entry['filepath'] = v.get('filepath', '')
            serializable[k] = entry
        CACHE_FILE.write_text(
            json.dumps(serializable, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
    except Exception as e:
        print(f"  ⚠️ Cache save failed: {e}")

# Call load_cache() at startup
if __name__ == '__main__':
    load_cache()
    app.run(debug=True, host='0.0.0.0', port=5000)

# Call save_cache() after every mutation
@app.route('/api/corrected', methods=['POST'])
def save_corrected():
    # ... existing code ...
    save_cache()
    return jsonify({'success': True})
```

---

## STEP 9 — LLM-Assisted Fallback
> **Time:** 1 day  
> **Impact:** Handles truly unusual PDFs without code changes

### When to use
Only when all parsers fail validation (sum of rooms doesn't match declared living space).

### Implementation

```python
def _extract_with_llm_fallback(self, raw_text: str, declared_living: float) -> list:
    """
    Last resort: send raw text to Claude API for extraction.
    Only called when extraction_confidence < 0.6.
    """
    import anthropic
    
    client = anthropic.Anthropic()
    
    prompt = f"""Extract room names and surfaces from this French architectural plan.
Declared living space: {declared_living} m²

Text:
{raw_text[:4000]}

Return ONLY valid JSON, no explanation:
{{"rooms": [{{"name": "Séjour/Cuisine", "surface": 25.4, "is_exterior": false}}, ...]}}

Rules:
- Include all rooms with their exact surface in m²
- is_exterior=true for: balcon, terrasse, jardin, loggia, parking, garage
- is_exterior=false for all interior rooms
- Use French room names as found in the text"""

    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    try:
        data = json.loads(response.content[0].text)
        rooms = []
        for room_data in data.get("rooms", []):
            norm, rtype, num, ext, conf = self.normalizer.normalize(room_data["name"])
            if rtype:
                rooms.append(ExtractedRoom(
                    name_raw=room_data["name"],
                    name_normalized=norm,
                    surface=float(room_data["surface"]),
                    room_type=rtype,
                    is_exterior=room_data.get("is_exterior", ext),
                    room_number=num,
                    source="llm_fallback",
                    confidence=0.75,
                ))
        return rooms
    except (json.JSONDecodeError, KeyError):
        return []
```

Usage in `_extract_single_page`:
```python
# After all parsers tried:
if not rooms or extraction_confidence < 0.6:
    logger.info("  🤖 Trying LLM fallback...")
    rooms_llm = self._extract_with_llm_fallback(raw_pymupdf, declared_living)
    if rooms_llm:
        rooms = rooms_llm
        logger.info(f"  🤖 LLM fallback: {len(rooms_llm)} rooms extracted")
```

---

## Implementation Order

```
Week 1:
  ✅ Day 1-2: STEP 1 — Ground truth test suite
  ✅ Day 3:   STEP 6 — Room normalizer fixes (quick wins)
  ✅ Day 3:   STEP 2 — Simplify extract_pdf()
  ✅ Day 4-5: STEP 7 — UI improvements

Week 2:
  ✅ Day 1:   STEP 8 — Persistent cache
  ✅ Day 2:   STEP 4 — Format auto-detection
  ✅ Day 3:   STEP 5 — Promoter profiles
  ✅ Day 4-5: STEP 3 — Spatial-aware parsing (start)

Week 3:
  ✅ Day 1-3: STEP 3 — Spatial-aware parsing (complete)
  ✅ Day 4:   STEP 9 — LLM fallback
  ✅ Day 5:   Full regression test + bug fixes
```

---

## Success Criteria

| Metric | Current | Target |
|---|---|---|
| Formats supported | ~4 | 10+ |
| Extraction accuracy | ~75% | 95%+ |
| Manual corrections needed | ~40% of files | <10% of files |
| Regression test coverage | 0 PDFs | 15+ PDFs |
| Time to support new format | 2-4 hours debugging | <30 minutes |
| Server restart data loss | 100% | 0% |

---

## Key Files Reference

| File | Role | Priority changes |
|---|---|---|
| `src/extractors/super_extractor/super_extractor.py` | Core extraction engine | STEP 3, 4 |
| `src/extractors/super_extractor/room_normalizer.py` | Room name mapping | STEP 6 |
| `src/extractors/super_extractor/metadata_extractor.py` | Reference/floor/promoter | STEP 5 |
| `src/extractors/super_extractor/models.py` | Data models | STEP 3 |
| `src/extractors/text_extractor.py` | PDF text extraction | STEP 3 |
| `app.py` | Flask backend | STEP 2, 8 |
| `templates/index.html` | Web UI | STEP 7 |
| `tests/test_extraction.py` | Regression tests | STEP 1 |