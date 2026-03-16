#!/usr/bin/env python
"""
Web UI for ArchiScan - PDF Extraction Verification & Correction
Flask backend for PDF extraction with frontend for verification.
"""
import os
import json
import tempfile
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, render_template, request, jsonify, send_file, session
from werkzeug.utils import secure_filename
import logging

# Add project to path
import sys
import copy
sys.path.insert(0, str(Path(__file__).parent))

# Disable logging for clean extraction
logging.basicConfig(level=logging.CRITICAL)
import logging as ext_logging
ext_logging.disable(ext_logging.CRITICAL)

from src.extractors.super_extractor import SuperExtractor

app = Flask(__name__)
app.secret_key = 'archiscan_secret_key_2026'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max
app.config['UPLOAD_FOLDER'] = tempfile.mkdtemp()
app.config['TEMPLATES_AUTO_RELOAD'] = True

# Allowed extensions
ALLOWED_EXTENSIONS = {'pdf'}

# Initialize extractor
extractor = SuperExtractor(use_ocr=True)

# Store extraction results in memory
extraction_cache = {}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_pdf(file_path, reference_hint=None):
    """Extract data from a PDF file using SuperExtractor.
    Returns a dict with lot keys. For multi-page PDFs, can return multiple lots."""
    try:
        # Use extract_all_pages for multi-page handling
        all_results = extractor.extract_all_pages(file_path, reference_hint)
        
        # Process results - convert ExtractionResult objects to dicts
        if isinstance(all_results, dict):
            # Filter out non-lot keys and convert values to dicts
            lot_results = {}
            for key, value in all_results.items():
                # Skip internal keys
                if not key.startswith('_'):
                    # Convert ExtractionResult to dict if needed
                    if hasattr(value, 'to_legacy_format'):
                        # Get the legacy format data
                        legacy_data = value.to_legacy_format()
                        
                        # to_legacy_format returns {reference: lot_data_or_nested_structure}
                        # We need to flatten this to get just the lot data for the UI
                        if isinstance(legacy_data, dict) and len(legacy_data) == 1:
                            reference_key = list(legacy_data.keys())[0]
                            lot_data = legacy_data[reference_key]
                            
                            # ── Inject page_number from ExtractionResult ──
                            page_number = getattr(value, 'page_number', 1)
                            
                            # Check if lot_data has the nested structure from floor_results
                            if isinstance(lot_data, dict) and 'floor_key' in lot_data:
                                lot_data_clean = {k: v for k, v in lot_data.items() if k != 'floor_key'}
                                lot_data_clean['page_number'] = page_number
                                lot_results[reference_key] = lot_data_clean
                            else:
                                lot_data['page_number'] = page_number
                                lot_results[reference_key] = lot_data
                        else:
                            # Unexpected format, but try to use what we have
                            lot_results[key] = legacy_data if isinstance(legacy_data, dict) else {"error": "Invalid format"}
                    else:
                        lot_results[key] = value  # Assume it's already a dict
            
            if lot_results:
                return lot_results
        
        # Fallback to single extraction
        result = extractor.extract(file_path, reference_hint)
        if hasattr(result, 'to_legacy_format'):
            # Get the legacy format data
            legacy_data = result.to_legacy_format()
            
            # to_legacy_format returns {reference: lot_data_or_nested_structure}
            # We need to flatten this to get just the lot data for the UI
            if isinstance(legacy_data, dict) and len(legacy_data) == 1:
                reference_key = list(legacy_data.keys())[0]
                lot_data = legacy_data[reference_key]
                
                # Check if lot_data has the nested structure from floor_results
                if isinstance(lot_data, dict) and 'floor_key' in lot_data:
                    # This is the nested format from multi-floor results
                    # Extract the actual lot data by removing the floor_key metadata
                    lot_data_clean = {k: v for k, v in lot_data.items() if k != 'floor_key'}
                    return {reference_key: lot_data_clean}
                else:
                    # This is already the flat format we want
                    return {reference_key: lot_data}
            else:
                # Unexpected format, but try to create a valid structure
                if isinstance(legacy_data, dict):
                    # Use the first key as reference, or fallback to filename
                    ref = reference_hint if reference_hint and not (reference_hint.startswith('PAGE_') or reference_hint == 'UNKNOWN') else list(legacy_data.keys())[0] if legacy_data else extract_lot_reference(filepath)
                    if ref.startswith('PAGE_') or ref == 'UNKNOWN':
                        ref = extract_lot_reference(filepath)
                        if ref.startswith('PAGE_') or ref == 'UNKNOWN':
                            ref = "UNKNOWN_LOT"
                    # If the data looks like it's already lot data (not nested), use it directly
                    # Otherwise, wrap it
                    first_value = list(legacy_data.values())[0] if legacy_data else {}
                    if isinstance(first_value, dict) and 'floor_key' not in first_value:
                        return {ref: first_value}
                    else:
                        return {ref: {"error": "Could not parse extraction result", "raw_data": str(legacy_data)}}
                else:
                    # If it's not a dict, create an error structure
                    ref = reference_hint if reference_hint and not (reference_hint.startswith('PAGE_') or reference_hint == 'UNKNOWN') else extract_lot_reference(filepath)
                    if ref.startswith('PAGE_') or ref == 'UNKNOWN':
                        ref = "UNKNOWN_LOT"
                    return {ref: {"error": "Could not parse extraction result", "raw_data": str(legacy_data)}}
        else:
            # If result is already a dict, return it
            # But ensure it's in the expected format {reference: data}
            if isinstance(result, dict):
                # Check if it's already in the format we want
                # If it has a single key that doesn't start with _, assume it's already formatted
                non_internal_keys = [k for k in result.keys() if not k.startswith('_')]
                if len(non_internal_keys) == 1:
                    return result
                else:
                    # Try to determine a good reference key
                    ref = reference_hint if reference_hint and not (reference_hint.startswith('PAGE_') or reference_hint == 'UNKNOWN') else extract_lot_reference(filepath)
                    if ref.startswith('PAGE_') or ref == 'UNKNOWN':
                        ref = "UNKNOWN_LOT"
                    # Wrap the entire dict under this reference
                    return {ref: result}
            else:
                # If result is not a dict, create an error structure
                ref = reference_hint if reference_hint and not (reference_hint.startswith('PAGE_') or reference_hint == 'UNKNOWN') else extract_lot_reference(filepath)
                if ref.startswith('PAGE_') or ref == 'UNKNOWN':
                    ref = "UNKNOWN_LOT"
                return {ref: {"error": "Unexpected result type", "raw_data": str(result)}}
    except Exception as e:
        print(f"Error extracting {file_path}: {e}")
        return {"error": str(e)}


@app.route('/')
def index():
    """Render the main page."""
    return render_template('index.html')


@app.route('/api/files', methods=['GET'])
def get_files():
    """Get all files from cache."""
    global extraction_cache
    
    files = []
    for file_id, file_data in extraction_cache.items():
        if not file_data.get('deleted', False):
            data = file_data['data']
            
            # Check if data has multiple lots
            if isinstance(data, dict):
                # Check if it's a multi-lot result (multiple keys)
                lot_keys = [k for k in data.keys() if not k.startswith('file_')]
                
                if len(lot_keys) > 1:
                    for lot_key in lot_keys:
                        lot_data = data[lot_key]
                        files.append({
                            'file_id': f"{file_id}_{lot_key}",
                            'filename': f"{file_data['filename']} - {lot_key}",
                            'parent_file_id': file_id,
                            'lot_key': lot_key,
                            'page_number': lot_data.get('page_number', 1),
                            'data': {lot_key: data[lot_key]}
                        })
                else:
                    lot_key = lot_keys[0] if lot_keys else 'unknown'
                    files.append({
                        'file_id': file_id,
                        'filename': file_data['filename'],
                        'parent_file_id': None,
                        'lot_key': lot_key,
                        'page_number': data.get(lot_key, {}).get('page_number', 1),
                        'data': data
                    })
    
    return jsonify({
        'success': True,
        'files': files
    })

from flask import send_from_directory

@app.route('/pdfjs/<path:filename>')
def pdfjs(filename):
    return send_from_directory('static/pdfjs', filename)

@app.route('/api/upload', methods=['POST'])
def upload_files():
    """Handle file upload - single PDF, multi-page PDF, or folder."""
    global extraction_cache
    
    files = request.files.getlist('files')
    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'No files selected'}), 400
    
    # First, save all files to disk
    saved_files = []
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            saved_files.append((filename, filepath))
    
    # Process extractions in parallel
    def process_file(args):
        filename, filepath = args
        # Check if file already exists in cache
        existing_file_id = None
        for fid, fdata in extraction_cache.items():
            if fdata['filename'] == filename:
                existing_file_id = fid
                break
        
        # Extract data
        extraction_data = extract_pdf(filepath)
        
        return filename, filepath, extraction_data, existing_file_id
    
    # Use ThreadPoolExecutor for parallel extraction
    results = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        extractions = list(executor.map(process_file, saved_files))
    
    # Update cache with results
    for filename, filepath, extraction_data, existing_file_id in extractions:
        if existing_file_id:
            file_id = existing_file_id
            extraction_cache[file_id] = {
                'filename': filename,
                'filepath': filepath,
                'data': extraction_data,
                'corrected': None,
                'deleted': False
            }
        else:
            file_id = f"file_{len(extraction_cache)}"
            extraction_cache[file_id] = {
                'filename': filename,
                'filepath': filepath,
                'data': extraction_data,
                'corrected': None,
                'deleted': False
            }
        
        results.append({
            'file_id': file_id,
            'filename': filename,
            'data': extraction_data
        })
    
    return jsonify({
        'success': True,
        'files': results,
        'total': len(extraction_cache),
        'all_files': [{'file_id': k, 'filename': v['filename']} for k, v in extraction_cache.items() if not v.get('deleted', False)]
    })


@app.route('/api/extract-single', methods=['POST'])
def extract_single():
    """Extract data from a single PDF file."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file selected'}), 400
    
    file = request.files['file']
    if file.filename == '' or not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type'}), 400
    
    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    
    # Extract data
    extraction_data = extract_pdf(filepath)
    
    return jsonify({
        'success': True,
        'filename': filename,
        'data': extraction_data
    })


@app.route('/api/corrected', methods=['POST'])
def save_corrected():
    """Save corrected extraction data."""
    global extraction_cache
    
    data = request.json
    file_id = data.get('fileId')
    lot_key = data.get('lotKey')
    corrected_data = data.get('correctedData')
    print(f"[SAVE] file_id={file_id} lot_key={lot_key} corrected_data keys={list(corrected_data.keys()) if corrected_data else None}")

    
    # Find base file id (strip lot suffix if needed)
    base_id = file_id
    if base_id not in extraction_cache:
        parts = base_id.rsplit('_', 1)
        if len(parts) == 2 and parts[0] in extraction_cache:
            base_id = parts[0]
    
    if base_id in extraction_cache:
        if lot_key:
            if 'corrected_lots' not in extraction_cache[base_id]:
                extraction_cache[base_id]['corrected_lots'] = {}
            extraction_cache[base_id]['corrected_lots'][lot_key] = corrected_data
        else:
            extraction_cache[base_id]['corrected'] = corrected_data
        return jsonify({'success': True})
    
    return jsonify({'error': 'File not found'}), 404


@app.route('/api/delete', methods=['POST'])
def delete_file():
    """Mark a file as deleted (soft delete)."""
    global extraction_cache
    
    data = request.json
    file_id = data.get('fileId')
    
    if file_id in extraction_cache:
        extraction_cache[file_id]['deleted'] = True
        return jsonify({'success': True})
    
    return jsonify({'error': 'File not found'}), 404


@app.route('/api/download', methods=['POST'])
def download_json():
    """Download all corrected extraction data as JSON."""
    global extraction_cache
    
    data = request.json
    include_uncorrected = data.get('includeUncorrected', False)  # Default to False - only confirmed
    
    output = {}
    
    for file_id, file_data in extraction_cache.items():
        # Skip deleted files
        if file_data.get('deleted', False):
            continue
            
        filename = file_data['filename']
        
        # Check corrected_lots (multi-lot PDFs)
        if file_data.get('corrected_lots'):
            for lot_key, corrected in file_data['corrected_lots'].items():
                key = corrected.get('parcelLabel', lot_key)
                output[key] = corrected

        # Check corrected (single-lot PDFs)
        elif file_data.get('corrected'):
            corrected = file_data['corrected']
            key = corrected.get('parcelLabel', filename.replace('.pdf', ''))
            output[key] = corrected

        elif include_uncorrected:
            # Transform original data to desired format
            original_data = file_data['data']
            if isinstance(original_data, dict):
                # Get the first (and usually only) lot key
                lot_key = list(original_data.keys())[0] if original_data else filename.replace('.pdf', '')
                lot_data = original_data.get(lot_key, {})
                
                # Extract lot reference from filename
                key = extract_lot_reference(filename)
                
                # Transform to desired format
                transformed = transform_to_format(key, lot_data)
                output[key] = transformed
    
    # Create temporary JSON file
    json_path = os.path.join(app.config['UPLOAD_FOLDER'], 'extraction_results.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    
    return send_file(json_path, as_attachment=True, download_name='extraction_results.json')


def extract_lot_reference(filename):
    """Extract lot reference from filename."""
    import re
    # Try to find pattern like CS001, A101, etc. in filename
    matches = re.search(r'(?:PDV[-_]?|[_-])?([A-Z]+\d+[A-Z]?)(?:[-_]|\.pdf)', filename, re.IGNORECASE)
    if matches and matches.group(1):
        return matches.group(1).upper()
    # Fallback: remove .pdf and common prefixes
    return filename.replace('.pdf', '', 1).replace('pdf', '', 1).rstrip('_')


def transform_to_format(key, lot_data):
    """Transform extraction data to desired output format."""
    # Map property type
    parcel_type_id = lot_data.get('parcelTypeId', 'appartment')
    parcel_type_label = lot_data.get('parcelTypeLabel', 'appartement')
    
    # Get surface detail and convert to uppercase
    surface_detail_raw = lot_data.get('surfaceDetail', {})
    surface_detail = {}
    habitable_sum = 0.0
    annexe_sum = 0.0
    
    exterior_types = {'balcon', 'terrasse', 'jardin', 'loggia', 'parking', 'garage', 'cave', 'cellier'}
    
    for room_name, room_surface in surface_detail_raw.items():
        upper_name = room_name.upper()
        surface_detail[upper_name] = room_surface
        
        # Check if exterior
        room_lower = room_name.lower()
        is_exterior = any(ext in room_lower for ext in exterior_types)
        
        if is_exterior:
            annexe_sum += float(room_surface) if room_surface else 0
        else:
            habitable_sum += float(room_surface) if room_surface else 0
    
    # Add totals
    surface_detail['TOTAL_HABITABLE'] = round(habitable_sum, 2)
    surface_detail['TOTAL_ANNEXE'] = round(annexe_sum, 2)
    
    # Get options
    option_raw = lot_data.get('option', {})
    option = {
        'balcony': bool(option_raw.get('balcony', False)),
        'terrace': bool(option_raw.get('terrace', False)),
        'garden': bool(option_raw.get('garden', False)),
        'loggia': bool(option_raw.get('loggia', False)),
        'duplex': False,
        'parking': bool(option_raw.get('parking', False)),
        'garage': bool(option_raw.get('garage', False)),
        'winter garden': False
    }
    
    # Build the output format
    result = {
        'parcelLabel': key,
        'parcelTypeId': parcel_type_id,
        'parcelTypeLabel': parcel_type_label,
        'orientation': '',
        'typology': lot_data.get('typology', ''),
        'floor': lot_data.get('floor', ''),
        'price': 'N.C',
        'living space': str(lot_data.get('living_space', '')),
        'option': option,
        'surfaceDetail': surface_detail,
        'tva': '',
        'pinel': True,
        'state': 'available',
        'customData': None
    }
    
    return result


@app.route('/api/file/<path:file_id>')
def get_file(file_id):
    """Get a file for display in viewer."""
    base_id = file_id
    if base_id not in extraction_cache:
        parts = base_id.rsplit('_', 1)
        if len(parts) == 2 and parts[0] in extraction_cache:
            base_id = parts[0]
    
    if base_id in extraction_cache:
        filepath = extraction_cache[base_id]['filepath']
        if os.path.exists(filepath):
            return send_file(filepath, mimetype='application/pdf')
    
    return jsonify({'error': 'File not found'}), 404


@app.route('/api/cache/clear', methods=['POST'])
def clear_cache():
    """Clear the extraction cache - reset all files."""
    global extraction_cache
    
    # Clean up uploaded files
    for file_data in extraction_cache.values():
        if os.path.exists(file_data.get('filepath', '')):
            try:
                os.remove(file_data['filepath'])
            except:
                pass
    
    extraction_cache = {}
    return jsonify({'success': True})


if __name__ == '__main__':
    print("Starting ArchiScan Web UI...")
    print("Open http://127.0.0.1:5000 in your browser")
    app.run(debug=True, host='0.0.0.0', port=5000)
