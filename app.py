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
from flask import Flask, render_template, request, jsonify, send_file, session
from werkzeug.utils import secure_filename
import logging

# Add project to path
import sys
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
        
        # Flatten results - extract_all_pages returns {lot_ref: data}
        if isinstance(all_results, dict):
            # Filter out non-lot keys
            lot_results = {}
            for key, value in all_results.items():
                # Skip internal keys
                if not key.startswith('_') and isinstance(value, dict):
                    lot_results[key] = value
            
            if lot_results:
                return lot_results
        
        # Fallback to single extraction
        result = extractor.extract(file_path, reference_hint)
        return result.to_legacy_format()
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
                    # Multiple lots - create separate entry for each
                    for lot_key in lot_keys:
                        files.append({
                            'file_id': f"{file_id}_{lot_key}",
                            'filename': f"{file_data['filename']} - {lot_key}",
                            'parent_file_id': file_id,
                            'lot_key': lot_key,
                            'data': {lot_key: data[lot_key]}
                        })
                else:
                    # Single lot
                    lot_key = lot_keys[0] if lot_keys else 'unknown'
                    files.append({
                        'file_id': file_id,
                        'filename': file_data['filename'],
                        'parent_file_id': None,
                        'lot_key': lot_key,
                        'data': data
                    })
    
    return jsonify({
        'success': True,
        'files': files
    })


@app.route('/api/upload', methods=['POST'])
def upload_files():
    """Handle file upload - single PDF, multi-page PDF, or folder."""
    global extraction_cache
    
    files = request.files.getlist('files')
    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'No files selected'}), 400
    
    results = []
    
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            # Check if file already exists in cache (for re-import after delete)
            existing_file_id = None
            for fid, fdata in extraction_cache.items():
                if fdata['filename'] == filename:
                    existing_file_id = fid
                    break
            
            # Extract data
            extraction_data = extract_pdf(filepath)
            
            if existing_file_id:
                # Update existing entry (re-import)
                file_id = existing_file_id
                extraction_cache[file_id] = {
                    'filename': filename,
                    'filepath': filepath,
                    'data': extraction_data,
                    'corrected': None,
                    'deleted': False
                }
            else:
                # Create new entry
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
    corrected_data = data.get('correctedData')
    
    if file_id in extraction_cache:
        extraction_cache[file_id]['corrected'] = corrected_data
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
        
        if file_data['corrected']:
            # Use the corrected data - use parcelLabel as the key
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


@app.route('/api/file/<file_id>')
def get_file(file_id):
    """Get a file for display in viewer."""
    if file_id in extraction_cache:
        filepath = extraction_cache[file_id]['filepath']
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
