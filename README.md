# ArchiScan - PDF Extraction & Verification Tool

A modern web application for extracting and verifying data from French real estate floor plans (PDF format).

## Features

- **PDF Upload**: Upload single PDFs, multi-page PDFs, or entire folders
- **Multi-Lot Support**: Handle PDFs where each page contains a different lot/parcel
- **Interactive Gallery**: View all imported files with status indicators
- **Split View**: PDF viewer on the left, editable form on the right
- **Data Verification**: Edit extracted values, add/remove rooms, modify options
- **Options Management**: Checkboxes for balcony, terrace, garden, loggia, parking, garage
- **Custom Fields**: Add custom label/value pairs
- **Confirm Workflow**: Confirm each lot after verification
- **JSON Export**: Download verified data as JSON

## Installation

```bash
# Install dependencies
pip install flask

# Run the application
python app.py
```

Open http://127.0.0.1:5000 in your browser.

## Usage

### 1. Upload Files
- Click "Choisir Fichiers" to select PDF files
- Click "Choisir Dossier" to select an entire folder
- After first upload, use "Ajouter des fichiers" button to add more

### 2. Verify & Edit
- Click on a file in the gallery to view it
- Edit fields:
  - Reference (Référence Lot)
  - Property Type (Type de Bien): Appartement, Maison, Magasin
  - Typology (Typology): T1, T2, T3, etc.
  - Floor (Étage): RDC, 1, 2, etc.
  - Living Space (Surface Habitable)
  - Rooms (Pièces): Add/remove rooms with surfaces
  - Options: Check exterior options and enter values

### 3. Confirm
- Click "Confirmer" to save verified data
- Confirmed files show a green checkmark

### 4. Download
- Click "Download JSON" to export confirmed data
- Only confirmed (non-deleted) files are included

## JSON Output Format

```json
{
  "CS001": {
    "parcelLabel": "CS001",
    "parcelTypeId": "appartment",
    "parcelTypeLabel": "Appartement",
    "orientation": "",
    "typology": "T3",
    "floor": "2",
    "price": "N.C",
    "living space": "72.80",
    "option": {
      "balcony": true,
      "terrace": false,
      "garden": false,
      "loggia": false,
      "duplex": false,
      "parking": false,
      "garage": false,
      "winter garden": false
    },
    "surfaceDetail": {
      "SEJOUR": 26.72,
      "CUISINE": 8.95,
      "CHAMBRE_1": 12.11,
      "SALLE_DE_BAIN": 4.86,
      "WC": 2.65,
      "TOTAL_HABITABLE": 72.80,
      "BALCON": 33.68,
      "TOTAL_ANNEXE": 33.68
    },
    "tva": "",
    "pinel": false,
    "state": "available",
    "customData": null
  }
}
```

## File Management

- **Delete Individual Files**: Hover over a file card and click the red X
- **Clear All**: Click "Effacer" button to reset everything
- **Re-import**: Add more files after initial upload

## Technology Stack

- **Backend**: Flask (Python)
- **Frontend**: HTML5, CSS3, JavaScript
- **PDF Extraction**: SuperExtractor (PyMuPDF + OCR)
- **Design**: Custom dark theme with responsive layout

## Project Structure

```
.
├── app.py                      # Flask backend
├── templates/
│   └── index.html             # Frontend UI
├── src/
│   └── extractors/
│       └── super_extractor/    # PDF extraction module
├── SUPER_EXTRACTOR_DOCUMENTATION.md
└── EXTRACTION_PROCESS_EXPLAINED.md
```

## Requirements

- Python 3.7+
- Flask
- PyMuPDF (fitz)
- pytesseract (for OCR)
- Pillow (for image processing)
- Tesseract OCR (optional, for scanned PDFs)

## License

Proprietary - Internal Use Only
