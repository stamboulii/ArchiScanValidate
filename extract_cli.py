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


def setup_logging():
    """Disable all logging to output clean JSON only."""
    # Configure root logger to suppress everything BEFORE importing modules
    logging.basicConfig(
        level=logging.CRITICAL,
        handlers=[
            logging.NullHandler()
        ]
    )
    
    # Disable all existing loggers
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.CRITICAL)
    
    # Remove any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    # Add null handler
    root_logger.addHandler(logging.NullHandler())
    
    # Disable all module loggers
    for name in logging.Logger.manager.loggerDict.copy():
        logger = logging.getLogger(name)
        logger.setLevel(logging.CRITICAL)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        logger.addHandler(logging.NullHandler())


# Setup logging before importing extractor
setup_logging()

from src.extractors.super_extractor import SuperExtractor


def _reindex_keys(output: dict) -> dict:
    """Reindex keys from format like 'F37' to 'F501' (Building + Floor + Unit)."""
    if not output:
        return output
    
    # First, group by building and floor to assign sequential apartment numbers
    # Key format: building + floor + sequential_number
    groups = {}  # (building, floor, prefix) -> list of (original_key, value)
    
    for key, value in list(output.items()):
        floor = value.get('floor', '0') if isinstance(value, dict) else '0'
        
        # Handle MAGASIN keys - convert to A5M01 format (replace original)
        if '_' in key and key.startswith('MAGASIN_'):
            num = key.split('_')[1] if '_' in key else '1'
            new_key = f"A{floor}M{int(num):02d}"
            # Replace the original key with the new key
            if new_key != key:
                output[new_key] = value
                del output[key]
            continue
        
        # Handle format like F37 -> F501 (Building + Floor + sequential number starting at 01)
        # But only reindex if the key doesn't already look like F701 format
        if len(key) >= 2 and key[0].isalpha() and key[0].isupper():
            building = key[0]
            remaining = key[1:]
            # Only reindex if remaining is digits but NOT already in correct format
            # (e.g., don't reindex C71 to C701)
            if remaining.isdigit():
                # Check if already looks like building+floor+2digit (e.g., C71 = C+7+01)
                if len(remaining) >= 2 and remaining[-2:].isdigit():
                    # Already looks correct, keep original
                    continue
                group_key = (building, floor, 'apt')
                if group_key not in groups:
                    groups[group_key] = []
                groups[group_key].append((key, value))
        else:
            # Keep original key for unknown formats
            pass
    
    # Assign sequential numbers within each group
    # And REMOVE the original keys from output
    for (building, floor, prefix), items in groups.items():
        # Sort by original key (F37, F38, etc.)
        items_sorted = sorted(items, key=lambda x: x[0])
        for seq_num, (orig_key, value) in enumerate(items_sorted, start=1):
            new_key = f"{building}{floor}{seq_num:02d}"
            # Add new key and REMOVE original
            output[new_key] = value
            if orig_key in output:
                del output[orig_key]
    
    # After reindexing, deduplicate keys that represent the same unit
    # e.g., "A001" and "A1" both refer to apartment A1 - keep shorter version
    normalized_to_original = {}
    keys_to_remove = set()
    
    for key in list(output.keys()):
        # Normalize key: remove leading zeros from numeric part
        # e.g., "A001" -> "A1", "A002" -> "A2"
        if len(key) >= 2 and key[0].isalpha() and key[0].isupper():
            building = key[0]
            remaining = key[1:]
            # Handle A001 format (building + floor + 2-digit unit)
            # or A0M01 format (building + floor + M + unit)
            if remaining.isdigit():
                # It's A001 format - extract floor and unit
                if len(remaining) >= 3:
                    # Last 2 digits are unit number, rest is floor
                    floor_part = remaining[:-2]
                    unit_part = remaining[-2:]
                    normalized_num = str(int(unit_part))  # Remove leading zeros
                    normalized_key = building + floor_part + normalized_num
                else:
                    normalized_key = key
            elif 'M' in remaining:
                # Keep MAGASIN format as-is
                normalized_key = key
            else:
                normalized_key = key
        else:
            normalized_key = key
        
        if normalized_key in normalized_to_original:
            # Duplicate found - mark for removal
            # Keep the shorter version (e.g., "A1" over "A001")
            original = normalized_to_original[normalized_key]
            if len(key) < len(original):
                keys_to_remove.add(original)
                normalized_to_original[normalized_key] = key
            else:
                keys_to_remove.add(key)
        else:
            normalized_to_original[normalized_key] = key
    
    # Remove duplicate keys
    for key in keys_to_remove:
        if key in output:
            del output[key]

    return output


def extract_to_json(pdf_path: str, reference: str = None, extract_all: bool = False, show_progress: bool = False) -> dict:
    """
    Extract data from PDF and return clean JSON in parcel format.
    
    Args:
        pdf_path: Path to PDF file
        reference: Reference hint (optional)
        extract_all: If True, extract ALL lots from multi-page PDF
        show_progress: If True, show progress bar during extraction
    
    Returns:
        Single dict for single lot, or dict with all lots for multi-page PDF
    """
    from tqdm import tqdm
    
    extractor = SuperExtractor()
    
    # Progress callback for tqdm
    pbar = None
    def progress_callback(current: int, total: int, message: str):
        if pbar is not None:
            pbar.set_description(f"Processing page {current}/{total}")
            pbar.update(1)
            pbar.refresh()
    
    if extract_all:
        # Extract all pages/lots
        if show_progress:
            import fitz
            doc = fitz.open(pdf_path)
            page_count = len(doc)
            doc.close()
            print(f"Starting extraction of {page_count} pages...")
            with tqdm(total=page_count, desc="Extracting pages", unit="page", leave=True) as pbar:
                all_results = extractor.extract_all_pages(pdf_path, reference, progress_callback)
        else:
            all_results = extractor.extract_all_pages(pdf_path, reference)
        
        if not all_results:
            return {"error": "No plans found in PDF"}
        
        # Convert all results to JSON format
        output = {}
        # If a specific reference was requested, try to find matching key by normalizing
        ref_normalized = reference.replace('-', '').replace(' ', '').upper() if reference else None
        
        # Find matching key in all_results (normalized comparison)
        refs_to_process = []
        if ref_normalized:
            for key in all_results.keys():
                if key.replace('-', '').replace(' ', '').upper() == ref_normalized:
                    refs_to_process = [key]
                    break
        if not refs_to_process:
            # No match found, use all keys
            refs_to_process = list(all_results.keys())
        
        for ref in refs_to_process:
            if ref not in all_results:
                continue
            result = all_results[ref]
            # Handle nested floor results (duplex/maison)
            if isinstance(result, dict):
                # This is a dict of floor results: {"A18_R+1": result1, "A18_R+2": result2}
                for floor_ref, floor_result in result.items():
                    if hasattr(floor_result, 'to_legacy_format'):
                        # to_legacy_format returns {ref: {...}}, extract inner dict
                        result_dict = floor_result.to_legacy_format()
                        # Get the inner dict using the reference as key
                        inner_key = floor_result.reference if floor_result.reference else floor_ref
                        if inner_key in result_dict:
                            parcel_data = _build_parcel_data(result_dict[inner_key], inner_key)
                        else:
                            # Get first key if reference not found
                            first_key = list(result_dict.keys())[0]
                            parcel_data = _build_parcel_data(result_dict[first_key], first_key)
                    else:
                        # Already a dict
                        parcel_data = _build_parcel_data(floor_result, floor_ref)
                    parcel_data["floor"] = floor_ref
                    output[floor_ref] = parcel_data
            elif hasattr(result, 'to_legacy_format'):
                # to_legacy_format returns {ref: {...}}, extract inner dict
                result_dict = result.to_legacy_format()
                # Always use the reference_hint (ref) as the key when provided
                # This ensures consistent output keys like "C71" instead of "C701"
                data_ref = ref  # Default to using ref
                if ref in result_dict:
                    parcel_data = _build_parcel_data(result_dict[ref], ref)
                else:
                    # Try to find a matching key by normalizing
                    ref_normalized = ref.replace('-', '').replace(' ', '')
                    matched = False
                    for key in result_dict.keys():
                        if key.replace('-', '').replace(' ', '') == ref_normalized:
                            # Use the matched key's data but keep original ref as output key
                            parcel_data = _build_parcel_data(result_dict[key], ref)
                            matched = True
                            break
                    if not matched:
                        # Use first key as fallback - but still use ref as output key
                        first_key = list(result_dict.keys())[0]
                        parcel_data = _build_parcel_data(result_dict[first_key], ref)
                output[ref] = parcel_data
            else:
                # Already a dict
                parcel_data = _build_parcel_data(result, ref)
                output[ref] = parcel_data
        
        return _reindex_keys(output)
    else:
        # Single extraction (default behavior)
        result = extractor.extract(pdf_path, reference)
        
        if hasattr(result, 'to_legacy_format'):
            result_dict = result.to_legacy_format()
        else:
            result_dict = result
        
        # Try to get reference from result_dict keys
        key = reference
        if not key and isinstance(result_dict, dict) and result_dict:
            key = list(result_dict.keys())[0] if result_dict else None
        
        # If reference doesn't match any key in result_dict, try to find a close match
        if key and isinstance(result_dict, dict) and key not in result_dict:
            # Try normalized versions (with/without hyphen, etc.)
            key_normalized = key.replace('-', '').replace(' ', '')
            for k in result_dict.keys():
                k_normalized = k.replace('-', '').replace(' ', '')
                if k_normalized == key_normalized:
                    key = k
                    break
        
        result = _build_parcel_data(result_dict, key)
        # For single extraction, wrap in dict and reindex
        return _reindex_keys({key: result}) if key else result


def _build_parcel_data(result, key=None) -> dict:
    """Build parcel data dict from extraction result (object or dict)."""
    # Handle nested dict format: {'C01': {data}} where key is passed separately
    inner_result = result
    if key and key in result and isinstance(result[key], dict):
        inner_result = result[key]
        
        # Check if inner_result is a multi-floor nested structure (e.g., {'C01_RDC': {...}, 'C01_R+1': {...}})
        # In this case, we need to pick one floor to use for parcel data
        if isinstance(inner_result, dict):
            # Look for floor keys (containing 'RDC', 'R+', 'REZ', etc.)
            floor_keys = [k for k in inner_result.keys() if isinstance(k, str) and (
                'RDC' in k.upper() or 'R+' in k.upper() or 'REZ' in k.upper() or 
                any(x in k.upper() for x in ['MEZZ', 'ETAGE', 'GROUND'])
            )]
            if floor_keys:
                # Prefer ground floor (RDC/REZ) over upper floors
                ground_floor_keys = [k for k in floor_keys if 'RDC' in k.upper() or 'REZ' in k.upper()]
                if ground_floor_keys:
                    selected_floor = ground_floor_keys[0]
                else:
                    # Sort to get R+1 before R+2, etc.
                    sorted_floors = sorted(floor_keys, key=lambda x: int(x.split('R+')[-1]) if 'R+' in x.upper() and x.split('R+')[-1].isdigit() else 0)
                    selected_floor = sorted_floors[0] if sorted_floors else floor_keys[0]
                inner_result = inner_result[selected_floor]
    
    # Handle both dict and object results
    if hasattr(result, 'reference'):
        # It's an ExtractionResult object
        ref = result.reference
        typology = result.typology
        floor = result.floor
        living_space = result.living_space
        rooms = result.rooms
        validation_errors = result.validation_errors
        validation_warnings = result.validation_warnings
    else:
        # It's a dict - check for different key formats
        # Handle nested dict format: {'C01': {data}} where key is passed separately
        inner_result = result
        if key and key in result and isinstance(result[key], dict):
            inner_result = result[key]
        
        # Check if inner_result is a multi-floor nested structure (e.g., {'C01_RDC': {...}, 'C01_R+1': {...}})
        # This can happen in two cases:
        # 1. key was found and result[key] was the nested structure
        # 2. key was NOT found but result itself is the nested structure (e.g., to_legacy_format returns {'C03': {'C03_RDC': {...}, 'C03_R+1': {...}}})
        if isinstance(inner_result, dict):
            # Look for floor keys (containing 'RDC', 'R+', 'REZ', etc.)
            floor_keys = [k for k in inner_result.keys() if isinstance(k, str) and (
                'RDC' in k.upper() or 'R+' in k.upper() or 'REZ' in k.upper() or 
                any(x in k.upper() for x in ['MEZZ', 'ETAGE', 'GROUND'])
            )]
            if floor_keys:
                # Prefer ground floor (RDC/REZ) over upper floors
                ground_floor_keys = [k for k in floor_keys if 'RDC' in k.upper() or 'REZ' in k.upper()]
                if ground_floor_keys:
                    selected_floor = ground_floor_keys[0]
                else:
                    # Sort to get R+1 before R+2, etc.
                    sorted_floors = sorted(floor_keys, key=lambda x: int(x.split('R+')[-1]) if 'R+' in x.upper() and x.split('R+')[-1].isdigit() else 0)
                    selected_floor = sorted_floors[0] if sorted_floors else floor_keys[0]
                inner_result = inner_result[selected_floor]
        
        ref = inner_result.get('reference', '')
        typology = inner_result.get('typology', '')
        floor = inner_result.get('floor', '')
        
        # Handle living_space - can be string or float
        living_space = inner_result.get('living_space', 0)
        if isinstance(living_space, str):
            try:
                living_space = float(living_space)
            except (ValueError, TypeError):
                living_space = 0
        
        # Handle rooms - can be in 'rooms' or 'surfaceDetail' (which is a dict, not list)
        rooms = inner_result.get('rooms', [])
        
        if not rooms and 'surfaceDetail' in inner_result:
            # surfaceDetail is a dict {name: surface}, convert to list format
            surface_detail_dict = inner_result.get('surfaceDetail', {})
            rooms = []
            for name, surface in surface_detail_dict.items():
                rooms.append({
                    'name_normalized': name,
                    'surface': surface,
                    'room_type': 'UNKNOWN',
                    'is_exterior': False
                })
        
        validation_errors = inner_result.get('validation_errors', inner_result.get('_validation', {}).get('errors', []))
        validation_warnings = inner_result.get('validation_warnings', inner_result.get('_validation', {}).get('warnings', []))
    
    # Use key as parcelLabel if ref is empty
    parcel_label = ref or key or ""
    
    # Convert floor to numeric: R+5 -> 5, RDC -> 0, etc.
    floor_numeric = floor
    if floor:
        import re
        floor_upper = floor.upper()
        
        # Check for ground floor first (RDC, REZ-DE-CHAUSSEE, or RDC+MEZ)
        # If the parcel is on ground floor, use 0 regardless of other floors mentioned
        if 'RDC' in floor_upper or 'REZ' in floor_upper:
            # Ground floor (with or without mezzanine) = floor 0
            floor_numeric = '0'
        else:
            # No ground floor - check for R+ number
            floor_match = re.search(r'R\+(\d+)', floor_upper)
            if floor_match:
                floor_numeric = floor_match.group(1)
    
    # Build clean JSON structure
    # Prefer outer result for parcelTypeId and parcelTypeLabel, fallback to inner
    parcel_type_id = None
    parcel_type_label = None
    if result and hasattr(result, 'get'):
        parcel_type_id = result.get('parcelTypeId', result.get('property_type'))
        parcel_type_label = result.get('parcelTypeLabel', result.get('property_type'))
    elif result and hasattr(result, 'parcelTypeId'):
        parcel_type_id = result.parcelTypeId or result.property_type
        parcel_type_label = result.parcelTypeLabel or result._property_label() if hasattr(result, '_property_label') else None
    
    # Fallback to inner_result if not found in outer result
    if not parcel_type_id:
        if inner_result and hasattr(inner_result, 'get'):
            parcel_type_id = inner_result.get('parcelTypeId', inner_result.get('property_type', 'appartment'))
            parcel_type_label = inner_result.get('parcelTypeLabel', inner_result.get('property_type', 'appartement'))
        elif inner_result and hasattr(inner_result, 'parcelTypeId'):
            parcel_type_id = inner_result.parcelTypeId or inner_result.property_type or 'appartment'
            parcel_type_label = inner_result.parcelTypeLabel or 'appartement'
    
    parcel_type_id = parcel_type_id or 'appartment'
    parcel_type_label = parcel_type_label or 'appartement'
    
    parcel_data = {
        "parcelLabel": parcel_label,
        "parcelTypeId": parcel_type_id,
        "parcelTypeLabel": parcel_type_label,
        "orientation": result.get('orientation', '') if hasattr(result, 'get') else '',
        "typology": typology or "",
        "floor": floor_numeric or "",
        "price": "N.C",
        "living space": str(living_space) if living_space else "0",
        "surfaceDetail": _build_surface_detail(rooms, result),
        "option": inner_result.get('option', _build_options(rooms, floor)),
        "tva": "",
    }
    
    return parcel_data


def _build_surface_detail(rooms: list, result=None) -> dict:
    """Build surface detail dictionary from rooms list."""
    surfaces = {}
    
    # Add multi-floor surfaces (RDC, MEZZANINE, etc.) if available
    multi_floor_surfaces = {}
    if result is not None:
        if hasattr(result, 'get'):
            multi_floor_surfaces = result.get('multi_floor_surfaces', {})
        else:
            multi_floor_surfaces = getattr(result, 'multi_floor_surfaces', {})
    
    # If we have multi_floor_surfaces with data, use those instead of room surfaces
    if multi_floor_surfaces and any(v > 0 for v in multi_floor_surfaces.values()):
        floor_labels = {'rdc': 'SURFACE RDC', 'mezz': 'SURFACE MEZZANINE', 'r+1': 'SURFACE R+1', 'r+2': 'SURFACE R+2', 'r+3': 'SURFACE R+3', 'r+4': 'SURFACE R+4', 'r+5': 'SURFACE R+5'}
        for floor_key, surface in multi_floor_surfaces.items():
            # Skip 'total' key - we'll add it ourselves
            if floor_key.lower() == 'total':
                continue
            if surface and surface > 0:
                label = floor_labels.get(floor_key.lower(), f'SURFACE {floor_key}')
                surfaces[label] = float(surface)
        
        # Add SURFACE TOTAL only once if there's more than one surface
        if len(surfaces) > 1:
            total = sum(surfaces.values())
            surfaces["SURFACE TOTAL"] = total
        return surfaces
    
    # Otherwise, use room-based surfaces
    # Room type to display name mapping
    TYPE_TO_NAME = {
        'LIVING_ROOM': 'SEJOUR',
        'RECEPTION': 'SEJOUR', 
        'KITCHEN': 'CUISINE',
        'LIVING_KITCHEN': 'SEJOUR_CUISINE',
        'ENTRY': 'ENTREE',
        'BEDROOM': 'CHAMBRE',
        'BATHROOM': 'SALLE_DE_BAIN',
        'SHOWER_ROOM': 'SDE',
        'WC': 'WC',
        'CIRCULATION': 'CIRCULATION',
        'STORAGE': 'PLACARD',
        'DRESSING': 'DRESSING',
        'BALCONY': 'BALCON',
        'TERRACE': 'TERRASSE',
        'GARDEN': 'JARDIN',
        'LOGGIA': 'LOGGIA',
        'PATIO': 'PATIO',
        'PARKING': 'PARKING',
        'CELLAR': 'CAVE',
    }
    
    for room in rooms:
        # Handle both dict and object rooms
        if isinstance(room, dict):
            name = room.get('name_normalized', room.get('name', ''))
            surface = room.get('surface', 0)
            room_type = room.get('room_type', '')
            if hasattr(room_type, 'name'):
                room_type = room_type.name
            is_exterior = room.get('is_exterior', False)
        else:
            name = room.name_normalized
            surface = room.surface
            room_type = room.room_type.name if hasattr(room.room_type, 'name') else str(room.room_type)
            is_exterior = room.is_exterior
        
        # Map room type to display name
        display_name = TYPE_TO_NAME.get(room_type, name.upper())
        
        # Handle numbered rooms (e.g., CHAMBRE_1, CHAMBRE_2)
        if hasattr(room, 'room_number') and room.room_number and room.room_number > 0:
            display_name = f"{display_name}_{room.room_number}"
        elif '_' in name and name.split('_')[-1].isdigit():
            # Try to extract number from name like "chambre_1"
            parts = name.rsplit('_', 1)
            if parts[0].upper() in [v for k, v in TYPE_TO_NAME.items()]:
                display_name = f"{parts[0].upper()}_{parts[1]}"
        
        if not is_exterior and surface and surface > 0:
            # Don't add SURFACE prefix for TOTAL_HABITABLE/TOTAL_ANNEXE keys
            if display_name in ('TOTAL_HABITABLE', 'TOTAL_ANNEXE'):
                surfaces[display_name] = float(surface)
            else:
                key = f"SURFACE {display_name}"
                # Deduplication: skip if more specific version exists
                base_name = display_name.split('_')[0] if '_' in display_name else display_name
                
                # Skip if we already have a numbered version (e.g., skip CHAMBRE if CHAMBRE_1 exists)
                has_numbered = any(k.startswith(f"SURFACE {base_name}_") for k in surfaces.keys())
                if has_numbered and not display_name.endswith(tuple('_' + str(i) for i in range(1, 20))):
                    continue
                surfaces[key] = float(surface)
    
    # Add total of interior surfaces only (exclude exterior: terrasse, balcon, etc.)
    _EXTERIOR_KEYS = {'TERRASSE', 'BALCON', 'JARDIN', 'LOGGIA', 'PATIO',
                      'TERRACE', 'BALCONY', 'GARDEN', 'PARKING', 'GARAGE', 'CAVE', 'CELLAR'}
    if len(surfaces) > 1:
        interior_total = sum(
            v for k, v in surfaces.items()
            if not any(ext in k.upper() for ext in _EXTERIOR_KEYS)
        )
        if interior_total > 0:
            surfaces["SURFACE TOTAL"] = round(interior_total, 2)

    return surfaces


def _build_options(rooms: list, floor: str = None) -> dict:
    """Build options from extracted data."""
    # Check for exterior spaces - check both room_type and room name
    has_garden = False
    has_terrace = False
    has_balcony = False
    has_loggia = False
    has_garage = False
    has_parking = False
    
    for room in rooms:
        # Get room name and type
        if isinstance(room, dict):
            room_name = room.get('name_normalized', '').lower()
            room_type = room.get('room_type', '')
            if hasattr(room_type, 'name'):
                room_type = room_type.name
        else:
            room_name = getattr(room, 'name_normalized', '').lower()
            room_type = room.room_type.name if hasattr(room.room_type, 'name') else str(room.room_type)
        
        # Check room_type first
        if room_type == "GARDEN":
            has_garden = True
        elif room_type in ["TERRACE", "PORCHE"]:
            has_terrace = True
        elif room_type == "BALCONY":
            has_balcony = True
        elif room_type == "LOGGIA":
            has_loggia = True
        elif room_type == "GARAGE":
            has_garage = True
        elif room_type == "PARKING":
            has_parking = True
        
        # Also check room name for common exterior terms
        if not has_garden and ('jardin' in room_name or 'garden' in room_name):
            has_garden = True
        if not has_terrace and ('terrasse' in room_name or 'terrace' in room_name):
            has_terrace = True
        if not has_balcony and ('balcon' in room_name or 'balcony' in room_name):
            has_balcony = True
        if not has_loggia and ('loggia' in room_name):
            has_loggia = True
        if not has_garage and ('garage' in room_name):
            has_garage = True
        if not has_parking and ('parking' in room_name or 'place' in room_name):
            has_parking = True
    
    # Check if duplex (multiple floors)
    is_duplex = False
    if floor:
        floor_upper = floor.upper()
        is_duplex = ("RDC" in floor_upper and "+1" in floor_upper) or \
                    ("," in floor) or ("/" in floor)
    
    return {
        "balcony": has_balcony,
        "terrace": has_terrace,
        "garden": has_garden,
        "parking": has_parking,
        "winter garden": False,
        "garage": has_garage,
        "loggia": has_loggia,
        "duplex": is_duplex
    }


def _handle_batch_mode(args):
    """Handle batch processing of multiple PDF files."""
    from tqdm import tqdm
    import os
    
    input_path = Path(args.pdf_path)
    
    # Get list of PDF files
    if input_path.is_dir():
        # Use set to deduplicate (Windows filesystem is case-insensitive)
        pdf_files = list(set(list(input_path.glob("*.pdf")) + list(input_path.glob("*.PDF"))))
        base_dir = input_path
    elif input_path.is_file() and input_path.suffix.lower() == '.pdf':
        # Multiple files passed as arguments
        pdf_files = [input_path]
        base_dir = input_path.parent
    else:
        print(f"Error: Invalid path: {input_path}", file=sys.stderr)
        sys.exit(1)
    
    if not pdf_files:
        print(f"Error: No PDF files found in {input_path}", file=sys.stderr)
        sys.exit(1)
    
    # Output directory
    output_dir = Path(args.output_dir) if args.output_dir else base_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Check if merge mode
    merge_mode = getattr(args, 'merge', False)
    
    print(f"Batch mode: Found {len(pdf_files)} PDF file(s)")
    print(f"Output directory: {output_dir}")
    if merge_mode:
        print(f"Merge mode: ON - all results will be combined into one file")
    
    # Process each file
    results = {}
    merged_data = {}  # For merge mode
    
    # Load existing merged data if merge mode and file exists (for incremental updates)
    if merge_mode:
        existing_merge_file = output_dir / "all_parcels_merged.json"
        if existing_merge_file.exists():
            try:
                existing_data = json.loads(existing_merge_file.read_text(encoding='utf-8'))
                merged_data = existing_data
                print(f"Loaded existing merge file with {len(merged_data)} parcels")
            except json.JSONDecodeError:
                print("Warning: Could not parse existing merge file, starting fresh")
    
    for pdf_file in tqdm(pdf_files, desc="Processing PDFs", unit="file"):
        try:
            print(f"\n--- Processing: {pdf_file.name} ---")
            
            # Check if this PDF might contain parcels already in merged_data
            if merge_mode:
                # Get expected parcel labels from PDF filename (e.g., IMAGINA_PV_A_011.pdf -> potential labels A011)
                # Also try to extract potential labels from filename
                potential_labels = []
                
                # Extract potential label from filename (e.g., B014 from IMAGINA_PV_B_014.pdf)
                filename_upper = pdf_file.stem.upper()
                # Match patterns like: A011, B014, C52, etc.
                label_match = re.search(r'[_\-\.\s]([A-Z]+\d+)[_\-\.\s]?$', filename_upper)
                if label_match:
                    potential_labels.append(label_match.group(1))
                # Also try simple pattern at end
                simple_match = re.search(r'([A-Z]+\d+)$', filename_upper)
                if simple_match:
                    potential_labels.append(simple_match.group(1))
                
                # Check if any of these labels already exist in merged_data
                skipped = False
                for label in potential_labels:
                    if label in merged_data:
                        print(f"-> SKIP: Parcel '{label}' already exists in merged file")
                        results[pdf_file.name] = {"status": "skipped", "reason": f"parcel '{label}' already exists"}
                        skipped = True
                        break
                
                if skipped:
                    continue
            
            # Extract data
            parcel_data = extract_to_json(
                str(pdf_file), 
                args.reference, 
                args.all, 
                args.verbose
            )
            
            if merge_mode:
                # Use parcelLabel as key, skip if already exists
                for key, parcel in parcel_data.items():
                    parcel_label = parcel.get('parcelLabel', '')
                    
                    if parcel_label:
                        # Use parcelLabel as the key
                        if parcel_label in merged_data:
                            print(f"-> SKIP: Parcel '{parcel_label}' already exists in merged file")
                            continue
                        
                        # Add source info
                        parcel["_source_file"] = pdf_file.name
                        merged_data[parcel_label] = parcel
                    else:
                        # Fallback to key if no parcelLabel
                        if key in merged_data:
                            # Key collision - make unique by adding numeric suffix
                            base_key = key
                            counter = 1
                            while f"{base_key}_{counter}" in merged_data:
                                counter += 1
                            unique_key = f"{base_key}_{counter}"
                        else:
                            unique_key = key
                        parcel["_source_file"] = pdf_file.name
                        merged_data[unique_key] = parcel
                
                results[pdf_file.name] = {
                    "status": "success",
                    "parcels": len(parcel_data) if isinstance(parcel_data, dict) else 0
                }
                print(f"-> Added {len(parcel_data)} parcels to merged file")
            else:
                # Generate output filename
                output_file = output_dir / f"{pdf_file.stem}_extracted.json"
                
                # Save to JSON
                indent = 4 if args.pretty else None
                json_output = json.dumps(parcel_data, indent=indent, ensure_ascii=False)
                output_file.write_text(json_output, encoding="utf-8")
                
                # Track results
                results[pdf_file.name] = {
                    "status": "success",
                    "output": str(output_file),
                    "parcels": len(parcel_data) if isinstance(parcel_data, dict) else 0
                }
                
                print(f"-> Saved to: {output_file}")
            
        except Exception as e:
            print(f"Error processing {pdf_file.name}: {e}")
            results[pdf_file.name] = {
                "status": "error",
                "error": str(e)
            }
    
    # Summary
    print(f"\n=== Batch Complete ===")
    successful = sum(1 for r in results.values() if r.get("status") == "success")
    failed = len(results) - successful
    print(f"Total: {len(results)} | Success: {successful} | Failed: {failed}")
    
    # Save merged file if merge mode is on
    if merge_mode and merged_data:
        merge_file = output_dir / "all_parcels_merged.json"
        indent = 4 if args.pretty else None
        json_output = json.dumps(merged_data, indent=indent, ensure_ascii=False)
        merge_file.write_text(json_output, encoding="utf-8")
        print(f"Merged file saved to: {merge_file}")
        print(f"Total parcels merged: {len(merged_data)}")
    
    # Save summary
    summary_file = output_dir / "batch_summary.json"
    summary_json = json.dumps(results, indent=2, ensure_ascii=False)
    summary_file.write_text(summary_json, encoding="utf-8")
    print(f"Summary saved to: {summary_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Extract architectural plan data to clean JSON"
    )
    parser.add_argument(
        "pdf_path",
        help="Path to the PDF file to extract"
    )
    parser.add_argument(
        "-r", "--reference",
        help="Reference/Lot number (optional)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file (optional, prints to stdout if not specified)"
    )
    parser.add_argument(
        "-p", "--pretty",
        action="store_true",
        help="Pretty print JSON output"
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress all logging output"
    )
    parser.add_argument(
        "-a", "--all",
        action="store_true",
        help="Extract ALL lots from multi-page PDF (returns dict with all lots)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show progress bar during extraction"
    )
    parser.add_argument(
        "-b", "--batch",
        action="store_true",
        help="Batch mode: process multiple PDF files or a directory"
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="In batch mode: merge all results into a single JSON file"
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory for batch mode (default: same as input)"
    )
    
    args = parser.parse_args()
    
    # Check if file exists
    if not Path(args.pdf_path).exists():
        print(f"Error: File not found: {args.pdf_path}", file=sys.stderr)
        sys.exit(1)
    
    # Handle batch mode
    if args.batch:
        _handle_batch_mode(args)
        return
    
    try:
        # Extract data
        parcel_data = extract_to_json(args.pdf_path, args.reference, args.all, args.verbose)
        
        # Output JSON
        indent = 4 if args.pretty else None
        json_output = json.dumps(parcel_data, indent=indent, ensure_ascii=False)
        
        if args.output:
            Path(args.output).write_text(json_output, encoding="utf-8")
            print(f"Output written to: {args.output}")
        else:
            print(json_output)
            
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
