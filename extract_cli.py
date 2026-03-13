#!/usr/bin/env python
"""
CLI for extracting data from architectural plans and outputting clean JSON.
Uses SuperExtractor for extraction.

Supports:
- Single page PDFs
- Multi-page PDFs (returns first lot by default)
- Multi-lot PDFs with --all flag (returns all lots)
"""
import json
import sys
import argparse
import logging
import re
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))


# ---------------------------------------------------------------------------
# Logging setup — must run before any module imports
# ---------------------------------------------------------------------------

def setup_logging():
    """Disable all logging to output clean JSON only."""
    logging.basicConfig(level=logging.CRITICAL, handlers=[logging.NullHandler()])
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.CRITICAL)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    root_logger.addHandler(logging.NullHandler())
    for name in logging.Logger.manager.loggerDict.copy():
        logger = logging.getLogger(name)
        logger.setLevel(logging.CRITICAL)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        logger.addHandler(logging.NullHandler())


setup_logging()

from src.extractors.super_extractor import SuperExtractor

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def _safe_float(value) -> float:
    """Safely convert value to float, returning 0.0 on failure."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _unpack_room(room) -> tuple:
    """
    Unpack a room (dict or object) into (name, surface, room_type, is_exterior).
    """
    if isinstance(room, dict):
        room_type = room.get('room_type', '')
        if hasattr(room_type, 'name'):
            room_type = room_type.name
        return (
            room.get('name_normalized', room.get('name', '')),
            room.get('surface', 0),
            room_type,
            room.get('is_exterior', False),
        )
    room_type = (
        room.room_type.name
        if hasattr(room.room_type, 'name')
        else str(room.room_type)
    )
    return room.name_normalized, room.surface, room_type, room.is_exterior


def _apply_room_number(display_name: str, raw_name: str, room) -> str:
    """Append room number suffix if available (e.g. CHAMBRE -> CHAMBRE_1)."""
    if hasattr(room, 'room_number') and room.room_number and room.room_number > 0:
        return f"{display_name}_{room.room_number}"
    if '_' in raw_name:
        parts = raw_name.rsplit('_', 1)
        if parts[-1].isdigit():
            return f"{parts[0].upper()}_{parts[1]}"
    return display_name


# ---------------------------------------------------------------------------
# Floor helpers
# ---------------------------------------------------------------------------

def _resolve_inner_result(data: dict) -> dict:
    """
    If *data* contains floor-level keys (RDC, R+1, …) return the preferred
    floor's sub-dict; otherwise return *data* unchanged.

    Priority: ground floor (RDC/REZ) > R+1 > R+2 …
    """
    floor_keys = [
        k for k in data.keys()
        if isinstance(k, str) and any(
            x in k.upper()
            for x in ['RDC', 'R+', 'REZ', 'MEZZ', 'ETAGE', 'GROUND']
        )
    ]
    if not floor_keys:
        return data

    ground = [k for k in floor_keys if 'RDC' in k.upper() or 'REZ' in k.upper()]
    if ground:
        return data[ground[0]]

    sorted_floors = sorted(
        floor_keys,
        key=lambda x: (
            int(x.split('R+')[-1])
            if 'R+' in x.upper() and x.split('R+')[-1].isdigit()
            else 0
        ),
    )
    return data[sorted_floors[0]]


def _parse_floor_numeric(floor: str) -> str:
    """
    Convert a floor label to a numeric string.
      'RDC'  -> '0'
      'R+3'  -> '3'
      ''     -> ''
    """
    if not floor:
        return ''
    upper = floor.upper()
    if 'RDC' in upper or 'REZ' in upper:
        return '0'
    match = re.search(r'R\+(\d+)', upper)
    return match.group(1) if match else floor


def _detect_duplex(floor: str) -> bool:
    """
    Return True when the floor string indicates a multi-level unit (duplex).
    Handles: 'RDC+R+1', 'R+1/R+2', 'R+1,R+2', consecutive R+ levels.
    """
    if not floor:
        return False
    upper = floor.upper()
    return (
        ('RDC' in upper and 'R+1' in upper)
        or bool(re.search(r'R\+\d+.*R\+\d+', upper))
        or ',' in floor
        or '/' in floor
    )


# ---------------------------------------------------------------------------
# Type resolution
# ---------------------------------------------------------------------------

def _resolve_parcel_type(result, inner_result) -> tuple:
    """
    Return (parcelTypeId, parcelTypeLabel) by inspecting *result* then
    *inner_result*.  Falls back to ('appartment', 'appartement').
    """
    for src in [result, inner_result]:
        if src is None:
            continue
        if hasattr(src, 'get'):
            type_id = src.get('parcelTypeId') or src.get('property_type')
            type_label = src.get('parcelTypeLabel') or src.get('property_type')
        elif hasattr(src, 'parcelTypeId'):
            type_id = getattr(src, 'parcelTypeId', None) or getattr(src, 'property_type', None)
            type_label = getattr(src, 'parcelTypeLabel', None)
        else:
            continue
        if type_id:
            return type_id, (type_label or type_id)
    return 'appartment', 'appartement'


# ---------------------------------------------------------------------------
# Room / surface helpers
# ---------------------------------------------------------------------------

# Canonical room-type → French display name
_TYPE_TO_NAME = {
    'LIVING_ROOM':    'SEJOUR',
    'RECEPTION':      'SEJOUR',
    'KITCHEN':        'CUISINE',
    'LIVING_KITCHEN': 'SEJOUR_CUISINE',
    'ENTRY':          'ENTREE',
    'BEDROOM':        'CHAMBRE',
    'BATHROOM':       'SALLE_DE_BAIN',
    'SHOWER_ROOM':    'SALLE_EAU',
    'WC':             'WC',
    'CIRCULATION':    'DEGAGEMENT',
    'STORAGE':        'PLACARD',
    'DRESSING':       'DRESSING',
    'BALCONY':        'BALCON',
    'TERRACE':        'TERRASSE',
    'GARDEN':         'JARDIN',
    'LOGGIA':         'LOGGIA',
    'PATIO':          'PATIO',
    'PARKING':        'PARKING',
    'CELLAR':         'CAVE',
}

_EXTERIOR_KEYS = {
    'TERRASSE', 'BALCON', 'JARDIN', 'LOGGIA', 'PATIO',
    'TERRACE', 'BALCONY', 'GARDEN', 'PARKING', 'GARAGE', 'CAVE', 'CELLAR',
}

_FLOOR_LABELS = {
    'rdc':  'SURFACE RDC',
    'mezz': 'SURFACE MEZZANINE',
    'r+1':  'SURFACE R+1',
    'r+2':  'SURFACE R+2',
    'r+3':  'SURFACE R+3',
    'r+4':  'SURFACE R+4',
    'r+5':  'SURFACE R+5',
}


def _extract_rooms_from_result(inner_result: dict) -> list:
    """
    Return a rooms list from *inner_result*.
    Falls back to converting 'surfaceDetail' dict into pseudo-room entries
    when no explicit rooms list is present.
    is_exterior is inferred from the key name using _EXTERIOR_KEYS.
    """
    rooms = inner_result.get('rooms', [])
    if not rooms and 'surfaceDetail' in inner_result:
        pseudo = []
        for name, surface in inner_result['surfaceDetail'].items():
            key_bare = re.sub(r'^SURFACE\s+', '', name).upper()
            key_base = key_bare.split('_')[0]
            is_ext   = key_base in _EXTERIOR_KEYS
            pseudo.append({
                'name_normalized': name,
                'surface':         surface,
                'room_type':       'UNKNOWN',
                'is_exterior':     is_ext,
            })
        return pseudo
    return rooms


def _build_surface_detail_from_floors(multi_floor_surfaces: dict) -> dict:
    """
    Build a surface dict from multi-floor data (duplex / maison).
    Keys like 'rdc', 'r+1', 'mezz' are mapped to their French labels.
    A SURFACE TOTAL is appended when more than one floor is present.
    """
    surfaces = {
        _FLOOR_LABELS.get(k.lower(), f'SURFACE {k.upper()}'): float(v)
        for k, v in multi_floor_surfaces.items()
        if k.lower() != 'total' and v and v > 0
    }
    if len(surfaces) > 1:
        surfaces['SURFACE TOTAL'] = round(sum(surfaces.values()), 2)
    return surfaces


def _build_surface_detail_from_rooms(rooms: list) -> dict:
    """
    Build a surface dict by iterating over individual rooms.
    Interior surfaces are summed into SURFACE TOTAL.
    """
    surfaces = {}

    # Max surface per room_type (for ExtractedRoom objects with known type)
    _TYPE_MAX = {
        'BALCONY': 70.0, 'TERRACE': 200.0, 'GARDEN': 5000.0,
        'LOGGIA': 30.0,  'PATIO': 100.0,   'PARKING': 40.0,
        'CELLAR': 50.0,  'GARAGE': 50.0,
        'BEDROOM': 40.0, 'BATHROOM': 20.0, 'SHOWER_ROOM': 15.0,
        'WC': 10.0,      'STORAGE': 15.0,  'DRESSING': 20.0,
        'ENTRY': 25.0,   'CIRCULATION': 20.0,
    }
    # Max surface per display name BASE (for pseudo-rooms rebuilt from surfaceDetail
    # dict where room_type is UNKNOWN — keyed on first word before underscore)
    _NAME_MAX = {
        'BALCON': 70.0,    'TERRASSE': 200.0, 'JARDIN': 5000.0,
        'LOGGIA': 30.0,    'PATIO': 100.0,    'PARKING': 40.0,
        'CAVE': 50.0,      'GARAGE': 50.0,
        'CHAMBRE': 40.0,   'SALLE': 20.0,     'WC': 10.0,
        'PLACARD': 15.0,   'DRESSING': 20.0,  'ENTREE': 25.0,
        'DEGAGEMENT': 20.0,
    }

    def _max_for(room_type, display_name):
        """Return surface ceiling for this room, checking both type and name."""
        m = _TYPE_MAX.get(room_type)
        if m:
            return m
        base = display_name.split('_')[0] if '_' in display_name else display_name
        return _NAME_MAX.get(base.upper())

    for room in rooms:
        name, surface, room_type, is_exterior = _unpack_room(room)
        if is_exterior or not surface or surface <= 0:
            continue

        display_name = _TYPE_TO_NAME.get(room_type, name.upper())
        display_name = _apply_room_number(display_name, name, room)

        # Special aggregate keys — no prefix
        if display_name in ('TOTAL_HABITABLE', 'TOTAL_ANNEXE'):
            surfaces[display_name] = float(surface)
            continue

        # Reject values outside known bounds (catches living_space leaking into rooms)
        ceiling = _max_for(room_type, display_name)
        if ceiling and surface > ceiling:
            continue

        key = f'SURFACE {display_name}'
        base = display_name.split('_')[0] if '_' in display_name else display_name

        # Skip generic name if a numbered variant already exists
        if (
            '_' not in display_name
            and any(k.startswith(f'SURFACE {base}_') for k in surfaces)
        ):
            continue

        surfaces[key] = float(surface)

    # --- Exterior rooms (balcony, terrace, etc.) ---
    # Included in output but NOT counted in SURFACE TOTAL
    for room in rooms:
        name, surface, room_type, is_exterior = _unpack_room(room)
        if not is_exterior or not surface or surface <= 0:
            continue

        display_name = _TYPE_TO_NAME.get(room_type, name.upper())
        display_name = _apply_room_number(display_name, name, room)

        # Same ceiling check — critical for balcony mis-paired with living_space total
        ceiling = _max_for(room_type, display_name)
        if ceiling and surface > ceiling:
            continue

        key  = f'SURFACE {display_name}'
        base = display_name.split('_')[0] if '_' in display_name else display_name

        # Skip generic if numbered variant exists
        if (
            '_' not in display_name
            and any(k.startswith(f'SURFACE {base}_') for k in surfaces)
        ):
            continue

        if key not in surfaces or float(surface) > surfaces[key]:
            surfaces[key] = float(surface)

    if len(surfaces) > 1:
        interior_total = sum(
            v for k, v in surfaces.items()
            if not any(ext in k.upper() for ext in _EXTERIOR_KEYS)
            and 'TOTAL' not in k.upper()
        )
        if interior_total > 0:
            surfaces['SURFACE TOTAL'] = round(interior_total, 2)

    return surfaces


def _build_surface_detail(rooms: list, result=None) -> dict:
    """
    Dispatcher: use multi-floor surface data when available,
    otherwise fall back to per-room surface building.
    """
    multi_floor_surfaces = {}
    if result is not None:
        multi_floor_surfaces = (
            result.get('multi_floor_surfaces', {})
            if hasattr(result, 'get')
            else getattr(result, 'multi_floor_surfaces', {})
        )

    if multi_floor_surfaces and any(v > 0 for v in multi_floor_surfaces.values()):
        return _build_surface_detail_from_floors(multi_floor_surfaces)

    return _build_surface_detail_from_rooms(rooms)


# ---------------------------------------------------------------------------
# Options builder
# ---------------------------------------------------------------------------

def _build_options(rooms: list, floor: str = None) -> dict:
    """Derive boolean option flags from room list and floor label."""
    flags = {
        'GARDEN':  False,
        'TERRACE': False,
        'BALCONY': False,
        'LOGGIA':  False,
        'GARAGE':  False,
        'PARKING': False,
    }
    name_keywords = {
        'GARDEN':  ('jardin', 'garden'),
        'TERRACE': ('terrasse', 'terrace'),
        'BALCONY': ('balcon', 'balcony'),
        'LOGGIA':  ('loggia',),
        'GARAGE':  ('garage',),
        'PARKING': ('parking', 'place'),
    }
    # PORCHE counts as terrace
    type_map = {
        'GARDEN':  {'GARDEN'},
        'TERRACE': {'TERRACE', 'PORCHE'},
        'BALCONY': {'BALCONY'},
        'LOGGIA':  {'LOGGIA'},
        'GARAGE':  {'GARAGE'},
        'PARKING': {'PARKING'},
    }

    for room in rooms:
        name, _, room_type, _ = _unpack_room(room)
        room_name_lower = name.lower()

        for flag, types in type_map.items():
            if not flags[flag] and room_type in types:
                flags[flag] = True
        for flag, keywords in name_keywords.items():
            if not flags[flag] and any(kw in room_name_lower for kw in keywords):
                flags[flag] = True

    return {
        'balcony':      flags['BALCONY'],
        'terrace':      flags['TERRACE'],
        'garden':       flags['GARDEN'],
        'parking':      flags['PARKING'],
        'winter garden': False,
        'garage':       flags['GARAGE'],
        'loggia':       flags['LOGGIA'],
        'duplex':       _detect_duplex(floor),
    }


# ---------------------------------------------------------------------------
# Parcel builder
# ---------------------------------------------------------------------------

def _build_parcel_data(result, key=None) -> dict:
    """
    Build a clean parcel dict from an extraction result (object or dict).

    Steps:
      1. Resolve the inner result (handle nested floor dicts).
      2. Extract scalar fields (ref, typology, floor, living_space, rooms).
      3. Resolve parcel type.
      4. Build surfaceDetail and option sub-dicts.
    """
    # --- 1. Resolve inner result ---
    inner_result = result
    if key and isinstance(result, dict) and key in result:
        inner_result = _resolve_inner_result(result[key])
    elif isinstance(result, dict):
        inner_result = _resolve_inner_result(result)

    # --- 2. Extract scalar fields ---
    if hasattr(result, 'reference'):
        # ExtractionResult object
        ref          = result.reference
        typology     = result.typology
        floor        = result.floor
        living_space = _safe_float(result.living_space)
        rooms        = result.rooms
    else:
        ref          = inner_result.get('reference', '')
        typology     = inner_result.get('typology', '')
        floor        = inner_result.get('floor', '')
        living_space = _safe_float(inner_result.get('living_space', 0))
        rooms        = _extract_rooms_from_result(inner_result)

    # --- 3. Resolve parcel type ---
    parcel_type_id, parcel_type_label = _resolve_parcel_type(result, inner_result)

    # --- 4. Build and return parcel dict ---
    # Always recalculate option from the actual rooms list.
    # Never reuse existing_option from inner_result — it may come from a stale
    # single-page extraction path and won't reflect the full combined room set
    # (e.g. balcony rooms added later from inverted-pairs would be missed).
    return {
        'parcelLabel':     ref or key or '',
        'parcelTypeId':    parcel_type_id,
        'parcelTypeLabel': parcel_type_label,
        'orientation':     result.get('orientation', '') if hasattr(result, 'get') else '',
        'typology':        typology or '',
        'floor':           _parse_floor_numeric(floor),
        'price':           'N.C',
        'living_space':    str(living_space) if living_space else '0',
        'surfaceDetail':   _build_surface_detail(rooms, result),
        'option':          _build_options(rooms, floor),
        'tva':             '',
    }


# ---------------------------------------------------------------------------
# Key reindexing / deduplication
# ---------------------------------------------------------------------------

def _deduplicate_keys(output: dict) -> dict:
    """
    Remove duplicate keys that represent the same unit after reindexing.
    e.g. 'A001' and 'A1' both normalise to 'A1' — keep the shorter one.
    """
    normalized_to_key = {}   # normalised -> canonical key
    to_remove = set()

    for key in list(output.keys()):
        if len(key) >= 2 and key[0].isalpha() and key[0].isupper():
            building  = key[0]
            remaining = key[1:]
            if remaining.isdigit() and len(remaining) >= 3:
                floor_part = remaining[:-2]
                unit_part  = remaining[-2:]
                norm_key   = building + floor_part + str(int(unit_part))
            elif 'M' in remaining:
                norm_key = key  # keep MAGASIN format as-is
            else:
                norm_key = key
        else:
            norm_key = key

        if norm_key in normalized_to_key:
            existing = normalized_to_key[norm_key]
            if len(key) < len(existing):
                to_remove.add(existing)
                normalized_to_key[norm_key] = key
            else:
                to_remove.add(key)
        else:
            normalized_to_key[norm_key] = key

    return {k: v for k, v in output.items() if k not in to_remove}


def _reindex_keys(output: dict) -> dict:
    """
    Reindex parcel keys so they follow the pattern:
      <Building><Floor><SequentialUnit>  e.g. A301, B502
    MAGASIN keys are converted to  A<Floor>M<nn>.
    Returns a NEW dict — the original is never mutated.
    """
    result  = {}
    groups  = {}   # (building, floor) -> [(orig_key, value), …]

    for key, value in output.items():
        floor = value.get('floor', '0') if isinstance(value, dict) else '0'

        # --- MAGASIN ---
        if key.startswith('MAGASIN_'):
            num     = key.split('_')[1] if '_' in key else '1'
            new_key = f"A{floor}M{int(num):02d}"
            result[new_key] = value
            continue

        # --- Standard alphabetic key ---
        if len(key) >= 2 and key[0].isalpha() and key[0].isupper():
            remaining = key[1:]
            if remaining.isdigit():
                # Already in correct format (floor + 2-digit unit)?
                if len(remaining) >= 2:
                    result[key] = value   # keep as-is
                else:
                    groups.setdefault((key[0], floor), []).append((key, value))
                continue

        # --- Anything else: keep unchanged ---
        result[key] = value

    # Assign sequential unit numbers within each (building, floor) group
    for (building, floor), items in groups.items():
        for seq, (_, value) in enumerate(sorted(items), start=1):
            result[f"{building}{floor}{seq:02d}"] = value

    return _deduplicate_keys(result)


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def _find_ref_key(result_dict: dict, ref: str) -> str:
    """
    Return the key in *result_dict* that matches *ref* (exact or normalised).
    Falls back to the first key when nothing matches.
    """
    if ref in result_dict:
        return ref
    ref_norm = ref.replace('-', '').replace(' ', '')
    for k in result_dict:
        if k.replace('-', '').replace(' ', '') == ref_norm:
            return k
    return next(iter(result_dict)) if result_dict else ref


def extract_to_json(
    pdf_path: str,
    reference: str = None,
    extract_all: bool = False,
    show_progress: bool = False,
) -> dict:
    """
    Extract data from a PDF and return a clean parcel JSON dict.

    Args:
        pdf_path:      Path to the PDF file.
        reference:     Optional reference / lot hint.
        extract_all:   When True, extract every lot in a multi-page PDF.
        show_progress: When True, display a tqdm progress bar.

    Returns:
        Dict keyed by parcel label.
    """
    extractor = SuperExtractor()

    # ------------------------------------------------------------------
    # Multi-lot extraction
    # ------------------------------------------------------------------
    if extract_all:
        pbar = None

        def _progress(current: int, total: int, message: str):
            if pbar is not None:
                pbar.set_description(f"Page {current}/{total}")
                pbar.update(1)
                pbar.refresh()

        if show_progress and fitz is not None and tqdm is not None:
            doc        = fitz.open(pdf_path)
            page_count = len(doc)
            doc.close()
            print(f"Starting extraction of {page_count} pages…")
            with tqdm(total=page_count, desc="Extracting pages", unit="page", leave=True) as pbar:
                all_results = extractor.extract_all_pages(pdf_path, reference, _progress)
        else:
            all_results = extractor.extract_all_pages(pdf_path, reference)

        if not all_results:
            return {'error': 'No plans found in PDF'}

        # Filter to requested reference when provided
        ref_norm = reference.replace('-', '').replace(' ', '').upper() if reference else None
        if ref_norm:
            refs_to_process = [
                k for k in all_results
                if k.replace('-', '').replace(' ', '').upper() == ref_norm
            ] or list(all_results.keys())
        else:
            refs_to_process = list(all_results.keys())

        output = {}
        for ref in refs_to_process:
            if ref not in all_results:
                continue
            result = all_results[ref]

            if isinstance(result, dict):
                # Nested floor results (duplex / maison)
                for floor_ref, floor_result in result.items():
                    if hasattr(floor_result, 'to_legacy_format'):
                        result_dict = floor_result.to_legacy_format()
                        inner_key   = getattr(floor_result, 'reference', None) or floor_ref
                        data_key    = inner_key if inner_key in result_dict else next(iter(result_dict))
                        parcel      = _build_parcel_data(result_dict[data_key], data_key)
                    else:
                        parcel = _build_parcel_data(floor_result, floor_ref)
                    parcel['floor']   = floor_ref
                    output[floor_ref] = parcel

            elif hasattr(result, 'to_legacy_format'):
                result_dict = result.to_legacy_format()
                data_key    = _find_ref_key(result_dict, ref)
                parcel      = _build_parcel_data(result_dict[data_key], ref)
                output[ref] = parcel

            else:
                output[ref] = _build_parcel_data(result, ref)

        return _reindex_keys(output)

    # ------------------------------------------------------------------
    # Single-lot extraction (default)
    # ------------------------------------------------------------------
    result = extractor.extract(pdf_path, reference)
    result_dict = result.to_legacy_format() if hasattr(result, 'to_legacy_format') else result

    key = reference
    if not key and isinstance(result_dict, dict) and result_dict:
        key = next(iter(result_dict))
    if key and isinstance(result_dict, dict):
        key = _find_ref_key(result_dict, key)

    parcel = _build_parcel_data(result_dict, key)
    return _reindex_keys({key: parcel}) if key else parcel


# ---------------------------------------------------------------------------
# Batch mode
# ---------------------------------------------------------------------------

def _handle_batch_mode(args):
    """Process multiple PDF files and optionally merge results."""
    import os

    input_path = Path(args.pdf_path)

    if input_path.is_dir():
        pdf_files = list({*input_path.glob('*.pdf'), *input_path.glob('*.PDF')})
        base_dir  = input_path
    elif input_path.is_file() and input_path.suffix.lower() == '.pdf':
        pdf_files = [input_path]
        base_dir  = input_path.parent
    else:
        print(f"Error: Invalid path: {input_path}", file=sys.stderr)
        sys.exit(1)

    if not pdf_files:
        print(f"Error: No PDF files found in {input_path}", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output_dir) if args.output_dir else base_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    merge_mode  = getattr(args, 'merge', False)
    merged_data = {}

    print(f"Batch mode: {len(pdf_files)} PDF file(s) found")
    print(f"Output directory: {output_dir}")
    if merge_mode:
        print("Merge mode: ON")

    # Load existing merged file for incremental updates
    if merge_mode:
        existing = output_dir / 'all_parcels_merged.json'
        if existing.exists():
            try:
                merged_data = json.loads(existing.read_text(encoding='utf-8'))
                print(f"Loaded existing merge file — {len(merged_data)} parcels")
            except json.JSONDecodeError:
                print("Warning: could not parse existing merge file, starting fresh")

    results = {}
    iterator = (
        tqdm(pdf_files, desc="Processing PDFs", unit="file")
        if tqdm is not None
        else pdf_files
    )

    for pdf_file in iterator:
        print(f"\n--- Processing: {pdf_file.name} ---")
        try:
            # Quick skip based on filename pattern (best-effort, not authoritative)
            if merge_mode:
                filename_upper = pdf_file.stem.upper()
                potential_labels = []
                for pattern in (
                    r'[_\-\.\s]([A-Z]+\d+)[_\-\.\s]?$',
                    r'([A-Z]+\d+)$',
                ):
                    m = re.search(pattern, filename_upper)
                    if m:
                        potential_labels.append(m.group(1))

                skipped = False
                for label in potential_labels:
                    if label in merged_data:
                        print(f"-> SKIP (filename match): '{label}' already in merged file")
                        results[pdf_file.name] = {
                            'status': 'skipped',
                            'reason': f"parcel '{label}' already exists",
                        }
                        skipped = True
                        break
                if skipped:
                    continue

            parcel_data = extract_to_json(
                str(pdf_file),
                args.reference,
                args.all,
                args.verbose,
            )

            if merge_mode:
                added = 0
                for p_key, parcel in parcel_data.items():
                    label = parcel.get('parcelLabel', '') or p_key
                    if label in merged_data:
                        print(f"-> SKIP: '{label}' already in merged file")
                        continue
                    parcel['_source_file'] = pdf_file.name
                    merged_data[label]     = parcel
                    added += 1

                results[pdf_file.name] = {'status': 'success', 'parcels_added': added}
                print(f"-> Added {added} parcel(s) to merged file")

            else:
                output_file = output_dir / f"{pdf_file.stem}_extracted.json"
                indent      = 4 if args.pretty else None
                output_file.write_text(
                    json.dumps(parcel_data, indent=indent, ensure_ascii=False),
                    encoding='utf-8',
                )
                results[pdf_file.name] = {
                    'status':  'success',
                    'output':  str(output_file),
                    'parcels': len(parcel_data) if isinstance(parcel_data, dict) else 0,
                }
                print(f"-> Saved: {output_file}")

        except Exception as exc:
            print(f"Error processing {pdf_file.name}: {exc}")
            results[pdf_file.name] = {'status': 'error', 'error': str(exc)}

    # Summary
    successful = sum(1 for r in results.values() if r.get('status') == 'success')
    print(f"\n=== Batch Complete: {len(results)} total | {successful} OK | "
          f"{len(results) - successful} failed ===")

    if merge_mode and merged_data:
        merge_file = output_dir / 'all_parcels_merged.json'
        indent     = 4 if args.pretty else None
        merge_file.write_text(
            json.dumps(merged_data, indent=indent, ensure_ascii=False),
            encoding='utf-8',
        )
        print(f"Merged file: {merge_file}  ({len(merged_data)} parcels)")

    summary_file = output_dir / 'batch_summary.json'
    summary_file.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"Summary: {summary_file}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Extract architectural plan data to clean JSON'
    )
    parser.add_argument('pdf_path', help='Path to the PDF file (or directory in batch mode)')
    parser.add_argument('-r', '--reference', help='Reference / lot number (optional)')
    parser.add_argument('-o', '--output',    help='Output JSON file (default: stdout)')
    parser.add_argument('-p', '--pretty',    action='store_true', help='Pretty-print JSON')
    parser.add_argument('-q', '--quiet',     action='store_true', help='Suppress log output')
    parser.add_argument('-a', '--all',       action='store_true', help='Extract all lots')
    parser.add_argument('-v', '--verbose',   action='store_true', help='Show progress bar')
    parser.add_argument('-b', '--batch',     action='store_true', help='Batch mode')
    parser.add_argument('--merge',           action='store_true', help='Merge batch results')
    parser.add_argument('--output-dir',      help='Output directory for batch mode')

    args = parser.parse_args()

    if not Path(args.pdf_path).exists():
        print(f"Error: File not found: {args.pdf_path}", file=sys.stderr)
        sys.exit(1)

    if args.batch:
        _handle_batch_mode(args)
        return

    try:
        parcel_data = extract_to_json(args.pdf_path, args.reference, args.all, args.verbose)
        indent      = 4 if args.pretty else None
        json_output = json.dumps(parcel_data, indent=indent, ensure_ascii=False)

        if args.output:
            Path(args.output).write_text(json_output, encoding='utf-8')
            print(f"Output written to: {args.output}")
        else:
            print(json_output)

    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()