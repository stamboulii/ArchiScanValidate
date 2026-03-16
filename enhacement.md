# ArchiScan Enhancement Roadmap

> **Objective:** Build a PDF plan extractor that handles all architectural plan formats and extracts room surfaces correctly for any French real estate PDF.

---

## Current State

| Component | Status |
|---|---|
| Two-block format (CS104, standard) | ✅ Working |
| Forward-names format (INK/Groupe Duval) | ✅ Working (new) |
| Inverted-pairs format (drawing labels) | ✅ Working |
| Alternating name/surface sections | ✅ Working |
| Multi-page PDFs (distinct refs per page) | ✅ Working |
| Multi-page PDFs (shared legend, distinct apartments) | ❌ Broken (44-page INK) |
| Balcon/exterior detection | ✅ Working |
| Reference detection (PP83/PP93 pollution) | ✅ Fixed |
| Spatial/OCR fallback | ✅ Working |

---

## Priority 1 — Immediate Fixes (Quick Wins)

### 1.1 Room Normalizer Gaps
**File:** `src/extractors/super_extractor/room_normalizer.py`

| Raw name | Current output | Expected output |
|---|---|---|
| `Cellier` | `placard` | `storage` / `cave` |
| `Dressing` | `placard` | `dressing` |
| `Pièce de vie` | `sejour_cuisine` | `sejour` (context-dependent) |
| `Rangement` | `circulation` | `storage` |
| `Dgt` / `Dégagement` | `circulation` | `degagement` |

**Fix:** Add/update alias mappings in the normalizer's pattern list.

---

### 1.2 Rangement Loss After `all_rooms.clear()`
**File:** `src/extractors/super_extractor/super_extractor.py`  
**Method:** `_rooms_from_two_block_text`

**Problem:** When the forward-fallback triggers and clears rooms, small single-surface runs (Rangement = 0.9m²) get filtered out because their `run_total_val` (0.9) doesn't match any validation threshold.

**Fix:** In the sub-table filter, always keep single-room runs with small surfaces:
```python
keep = (
    not is_floor_plan_noise
    and (
        is_single_room
        or rt == 0.0
        or rt < 5.0  # ← ADD: always keep small rooms (rangement, wc, etc.)
        or abs(rt - filter_living_space) < 1.0
        ...
    )
)
```

---

### 1.3 `SURFACE TOTAL` Value
**File:** `extract_cli.py`  
**Method:** `_build_surface_detail_from_rooms`

**Problem:** `SURFACE TOTAL` shows the sum of extracted rooms instead of the declared `living_space`. When Rangement is missing, the total is off.

**Fix:** Use `living_space` directly:
```python
surface_detail["SURFACE TOTAL"] = result.living_space  # not sum(interior)
```

---

### 1.4 Duplex False Positive on INK A002
**File:** `src/extractors/super_extractor/super_extractor.py`  
**Method:** `_detect_property_type` / `_build_options`

**Problem:** INK A002 outputs `duplex: true` incorrectly. The floor string `"0"` (Rdc) is being misinterpreted.

**Fix:** Review duplex detection logic — only set `duplex: true` when floor string explicitly contains `DUPLEX` or multiple distinct floor levels.

---

## Priority 2 — Architecture Improvements (Medium Term)

### 2.1 Spatial/Position-Aware Parsing ⭐ Highest Impact
**File:** `src/extractors/super_extractor/super_extractor.py`

**Problem:** Currently uses PyMuPDF `get_text()` which collapses all spatial information into a flat string. This forces complex heuristics (gap counting, noise detection, forward/backward walks) to reconstruct what the PDF layout already knows.

**Solution:** Switch to `get_text("dict")` or `get_text("blocks")` which returns x/y coordinates per text span.

```python
page = doc[page_num]
blocks = page.get_text("dict")["blocks"]

# Separate left column (names) from right column (surfaces) by x-coordinate
page_width = page.rect.width
left_blocks  = [b for b in blocks if b["bbox"][0] < page_width * 0.6]
right_blocks = [b for b in blocks if b["bbox"][0] >= page_width * 0.6]

# Summary table is typically in the right 40% of the page
# Floor plan drawing labels are in the center/left
# Legend is in the far left
```

**Benefits:**
- Eliminates forward/backward name-walk heuristics
- Reliable column separation (names vs surfaces)
- Legend detection (left side = legend codes like PP83)
- Floor plan label detection (center = drawing annotations)
- Would replace most of `_rooms_from_two_block_text` complexity

**Effort:** High. Requires rewriting the core parsing pipeline but eliminates most technical debt.

---

### 2.2 PDF Format Detection
**File:** `src/extractors/super_extractor/super_extractor.py`

**Problem:** The code tries all parsers sequentially and uses complex dedup/validation to reconcile conflicting results. This causes regressions when a new format is added.

**Solution:** Detect the PDF format early and route to the appropriate parser:

```python
def _detect_pdf_format(self, lines: list) -> str:
    """
    Returns: 'two_block' | 'forward_names' | 'inverted_pairs' | 
             'alternating' | 'spatial_table'
    """
    surface_indices = [i for i, l in enumerate(lines) if is_surface(l)]
    if not surface_indices:
        return 'unknown'
    
    # Check for large consecutive surface runs (two-block or forward-names)
    runs = _build_runs(surface_indices)
    large_runs = [r for r in runs if len(r) > 3]
    
    if large_runs:
        # Check if names come before or after the surface block
        first_large_run = large_runs[0]
        names_before = sum(1 for i in range(0, first_large_run[0])
                          if is_valid_name(lines[i]))
        names_after = sum(1 for i in range(first_large_run[-1]+1, 
                          min(first_large_run[-1]+20, len(lines)))
                         if is_valid_name(lines[i]))
        if names_after > names_before:
            return 'forward_names'  # INK/Groupe Duval
        return 'two_block'          # Standard (CS104)
    
    # Check for alternating surf/name pattern (inverted pairs)
    alternating = sum(1 for i in range(len(lines)-1)
                     if is_surface(lines[i]) and is_valid_name(lines[i+1]))
    if alternating > 3:
        return 'inverted_pairs'
    
    return 'alternating'
```

**Benefits:** Cleaner code, faster extraction, no cross-parser interference.

---

### 2.3 Per-Page Reference Detection
**File:** `src/extractors/super_extractor/super_extractor.py`  
**Method:** `extract_all_pages`

**Problem:** For the 44-page INK PDF, every page has the same legend (`PP83 PP93`) so all pages get grouped into 2 runs. Each page is actually a distinct apartment (`A001`, `A002`... `A044`).

**Root cause:** `_is_plan_page` only detects *if* a page is a plan, not *which* apartment it belongs to. Reference detection happens after grouping, too late.

**Solution:** Make `_is_plan_page` also extract the primary reference, using position awareness:

```python
def _detect_page_reference(self, page) -> str:
    """
    Use spatial position to find the apartment reference.
    In INK PDFs: reference appears in top-right metadata block
    near 'Logement' keyword, NOT in the left legend column.
    """
    blocks = page.get_text("dict")["blocks"]
    page_width = page.rect.width
    
    # Look for reference in right 30% of page (metadata panel)
    right_blocks = [b for b in blocks if b["bbox"][0] > page_width * 0.7]
    
    for block in right_blocks:
        text = " ".join(span["text"] for line in block.get("lines", [])
                       for span in line.get("spans", []))
        # Match "A 002", "A002", "Logement A 002"
        m = re.search(r'Logement\s*[:\s]*([A-Z]\s*\d{3,4})', text)
        if m:
            return re.sub(r'\s+', '', m.group(1))
    
    return None
```

---

### 2.4 Cross-Validation with Parser Retry
**File:** `src/extractors/super_extractor/super_extractor.py`

**Problem:** When a parser returns wrong results (surface sum doesn't match declared living_space), the system currently returns bad data.

**Solution:** Add a validation-retry loop:

```python
def _extract_with_validation(self, raw_text, declared_living):
    parsers = [
        ('two_block', self._rooms_from_two_block_text),
        ('inverted',  self._rooms_from_inverted_pairs),
        ('multiline', self._rooms_from_multiline_text),
        ('regex',     self._rooms_from_regex),
    ]
    
    for name, parser in parsers:
        rooms, living, annex = parser(raw_text, name, declared_living)
        if not rooms:
            continue
        
        interior_sum = sum(r.surface for r in rooms if not r.is_exterior)
        if declared_living > 0:
            diff_pct = abs(interior_sum - declared_living) / declared_living
            if diff_pct < 0.05:  # Within 5% → accept
                logger.info(f"  ✅ Parser '{name}' validated: {diff_pct:.1%} diff")
                return rooms, living, annex
            else:
                logger.info(f"  ⚠️ Parser '{name}' rejected: {diff_pct:.1%} diff, trying next")
        else:
            return rooms, living, annex  # No declared living, accept first result
    
    return [], 0.0, 0.0
```

---

## Priority 3 — Robustness (Medium Term)

### 3.1 Known Promoter Profiles
**File:** `src/extractors/super_extractor/metadata_extractor.py`

Different promoters use different PDF formats consistently. Build a profile system to skip incompatible parsers:

```python
PROMOTER_PROFILES = {
    "Groupe Duval": {
        "format": "forward_names",
        "ref_pattern": r"Logement\s*[:\s]*([A-Z]\s*\d{3,4})",
        "ref_blacklist": [r"^PP\d+$"],
        "legend_position": "left",
    },
    "Faubourg Immobilier": {
        "format": "two_block_standard",
        "ref_pattern": r"\b([A-Z]\d{2,4})\b",
    },
    "Nexity": {
        "format": "alternating",
    },
}
```

Once `_detect_promoter()` identifies the promoter, the appropriate format profile is loaded before parsing begins.

---

### 3.2 Confidence Scoring
**File:** `src/extractors/super_extractor/models.py`

Track extraction confidence per result:

```python
@dataclass
class ExtractionResult:
    ...
    extraction_confidence: float = 0.0  # 0.0 to 1.0
    extraction_source: str = ""         # which parser succeeded
    validation_passed: bool = False     # sum ≈ declared_living
```

Confidence levels:
| Source | Confidence |
|---|---|
| Spatial table with exact match | 0.98 |
| Two-block with total validation | 0.92 |
| Forward-names with validation | 0.88 |
| Inverted-pairs with validation | 0.80 |
| Regex fallback | 0.55 |
| OCR | 0.50 |
| Inferred rooms | 0.65 |

---

### 3.3 Regression Test Suite
**New file:** `tests/test_extraction.py`

Build a ground-truth dataset and run it automatically:

```python
GROUND_TRUTH = [
    {
        "pdf": "test_pdfs/CS104.pdf",
        "expected": {
            "reference": "CS104",
            "living_space": 99.53,
            "rooms": {
                "SURFACE CHAMBRE_1": 12.54,
                "SURFACE CHAMBRE_2": 10.54,
                "SURFACE CHAMBRE_3": 13.02,
                "SURFACE BALCON": 27.74,
                ...
            }
        }
    },
    {
        "pdf": "test_pdfs/INK_A002.pdf",
        "expected": {
            "reference": "A002",
            "living_space": 107.6,
            "rooms": {
                "SURFACE SEJOUR_CUISINE": 35.0,
                "SURFACE CHAMBRE_1": 13.4,
                "SURFACE TERRASSE": 14.9,
                ...
            }
        }
    },
]

def test_extraction_accuracy():
    extractor = SuperExtractor()
    for case in GROUND_TRUTH:
        result = extractor.extract(case["pdf"])
        data = result.to_legacy_format()[result.reference]["surfaceDetail"]
        for key, expected_val in case["expected"]["rooms"].items():
            assert abs(data.get(key, 0) - expected_val) < 0.1, \
                f"{case['pdf']}: {key} = {data.get(key)} ≠ {expected_val}"
```

Run before every commit:
```bash
pytest tests/test_extraction.py -v
```

---

## Priority 4 — Strategic / Long Term

### 4.1 LLM-Assisted Fallback
**File:** `src/extractors/super_extractor/super_extractor.py`

For PDFs that fail validation after all parsers, use the Claude API as a last resort:

```python
async def _extract_with_llm(self, raw_text: str, declared_living: float) -> list:
    """
    Send raw PDF text to Claude API and ask it to extract room surfaces.
    Only used when all other parsers fail validation.
    """
    prompt = f"""
Extract room names and surfaces from this French architectural plan text.
Declared living space: {declared_living} m²

Text:
{raw_text[:3000]}

Return JSON only:
{{"rooms": [{{"name": "Séjour", "surface": 25.4}}, ...]}}
"""
    response = anthropic.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    # Parse and normalize the response
    ...
```

**When to use:** Only when `extraction_confidence < 0.6` and `validation_passed = False`.

---

### 4.2 Multi-Page Strategy Rethink

**Current strategy:** Group consecutive pages with the same reference into runs.

**Problem:** Fails when every page has the same legend codes (INK 44-page PDF).

**Proposed strategy:**
1. Extract reference from each page using position-aware detection
2. Group pages by extracted reference (not legend codes)
3. If all pages have the same reference, treat each page as independent
4. Merge pages only when their living_space values are complementary

```python
# Instead of:
if ref == current_ref or self._refs_are_same(ref, current_ref):
    current_pages.append(result)

# Use:
page_ref = self._detect_page_reference(page)  # position-aware
if page_ref and page_ref != current_ref:
    # New apartment
    runs.append((current_ref, current_pages))
    current_ref = page_ref
    current_pages = [result]
```

---

## Implementation Order

```
Phase 1 (1-2 days):
  ✅ 1.1 Normalizer gaps (Cellier, Dressing)
  ✅ 1.2 Rangement loss fix
  ✅ 1.3 SURFACE TOTAL value
  ✅ 1.4 Duplex false positive

Phase 2 (1 week):
  ⬜ 2.4 Cross-validation with parser retry
  ⬜ 3.3 Regression test suite (build as you fix)
  ⬜ 3.1 Promoter profiles (start with Groupe Duval)

Phase 3 (2-3 weeks):
  ⬜ 2.1 Spatial/position-aware parsing (biggest impact)
  ⬜ 2.2 PDF format detection
  ⬜ 2.3 Per-page reference detection (fixes 44-page INK)

Phase 4 (ongoing):
  ⬜ 3.2 Confidence scoring
  ⬜ 4.1 LLM-assisted fallback
  ⬜ 4.2 Multi-page strategy rethink
```

---

## Key Technical Debt to Address

| Issue | Location | Impact |
|---|---|---|
| `get_text()` loses spatial info | `text_extractor.py` | High |
| 500+ line `_rooms_from_two_block_text` | `super_extractor.py` | High |
| No format detection before parsing | `super_extractor.py` | High |
| `_parse_section_alternating` early returns | `super_extractor.py` | Medium |
| Parser results merged without format context | `_extract_single_page` | Medium |
| No automated tests | — | High |
| `NOISE_RE` growing unboundedly | `super_extractor.py` | Low |

---

## Notes on `get_text("dict")` Migration

PyMuPDF's dict format returns:
```python
{
  "blocks": [
    {
      "bbox": [x0, y0, x1, y1],  # position on page
      "lines": [
        {
          "spans": [
            {
              "text": "Pièce de vie",
              "size": 10.0,       # font size (large = headers)
              "flags": 0,         # bold, italic flags
              "bbox": [x0,y0,x1,y1]
            }
          ]
        }
      ]
    }
  ]
}
```

Font size is particularly useful:
- Large font (14pt+) → section headers, apartment reference
- Medium font (10-12pt) → room names in summary table  
- Small font (8pt) → legend codes, footnotes
- Very small (6pt) → noise, legal text

This alone would allow reliable separation of legend codes (small font, left column) from apartment references (large font, right panel) without any regex heuristics.