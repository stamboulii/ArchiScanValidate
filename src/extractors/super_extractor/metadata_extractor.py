"""
Metadata Extractor - Référence, étage, bâtiment, promoteur, adresse
"""

import re
import logging
from typing import Dict, Optional, List, Any

logger = logging.getLogger(__name__)


class MetadataExtractor:

    REF_PATTERNS = [
        # Priorité 0: MAGASIN format with type prefix
        r"MAGASIN\s*N[°o]\s*[:\s]*(\d+)",
        r"MAGASIN\s*[:\s]*(\d+)",
        r"MAGASIN\s+N[°o]\s*[:\s]*(\d+)",
        # Priorité 0: Logement code "A 101", "B 203" (but NOT single letter + newline + number)
        r"Logement\s*[:\s]*([A-Z]\s*\d{3,4})",
        # Priorité 0: code avec tiret (A-53, C-53, etc.) - must come BEFORE standalone letter+number
        r"\b([A-Z]-\d{2,4})\b",
        # Priorité 1: pattern explicite avec contexte
        r"Appartement\s+([A-Z]{1,2}\d{2,4})",
        r"APPARTEMENT\s*[:\s]*([A-Z]\d{2,4})",
        r"APPARTEMENT\s*[:\s]*([A-Z]{1,2}\d{2,4})",
        # Moroccan format: "APPARTEMENT N° : 17" or "APPARTEMENT N° 17"
        r"APPARTEMENT\s*N[°o]\s*[:\s]*(\d+)",
        r"APPARTEMENT\s*N[°o]\s*(\d+)",
        r"APPARTEMENT\s*[:\s]*(\d+)",
        r"Appartement\s+N[°o]\s*[:\s]*(\d+)",
        # Moroccan MAGASIN format: "MAGASIN N° : 15"
        # (Already handled above)
        r"LOT\s*[:\s]*([A-Z]?\d{2,4})",
        r"R[ÉE]F[ÉE]RENCE\s*[:\s]*(\w+)",
        # Priorité 1b: format Bâtiment-Lot "B2-402", "B1-101"
        r"NUMERO\s*LOT[^\n]{0,40}?(\b[A-Z]\d{1,2}-\d{3,4}\b)",
        r"\b([A-Z]\d{1,2}-\d{3,4})\b",  # B2-402, B1-101
        # Priorité 2: code seul (A008, B13, C234)
        r"\b([A-Z]{1,2}\d{3,4})\b",   # CS104, A104, B13
        r"\b([A-Z]{1,2}\d{2})\b",      # CS10, A08   # ← REMETTRE mais avec blacklist
        # Priorité 3: Standalone letter + space + number (A 101, B 203) - lower priority
        r"\b([A-Z])\s+(\d{3,4})\b",
    ]

    REF_BLACKLIST = {"R1", "R2", "R3", "T1", "T2", "T3", "T4", "T5", "T6",
                 "A1", "A2", "A3", "B1", "B2", "B3",  # trop courts/génériques
                 "DATE", "TYPE", "PLAN", "NOTA", "IND",
                 # Articles de loi CCH (faux positifs)
                 "L261", "R261", "L111", "R111", "L123", "R123",
                 "L151", "R151", "L152", "R152", "L421", "R421",
                 # Height codes from architectural drawings (Hauteur XXX cm)
                 "H180", "H214", "H250", "H360", "H110", "H160", "H200",
                 "H220", "H240", "H270", "H300", "H320", "H350", "H400"}

    FLOOR_PATTERNS = [
        # Numeric floor codes: "Etage 001", "Etage 002" — keep as-is
        (r"\bEtage\b.{0,300}?\b(0\d{2})\b", lambda m: m.group(1)),  # "Etage ... 001"
        (r"\bEtage\s+(\d{3})\b", lambda m: m.group(1)),
        # Format ETAGE: "ETAGE 1", "ETAGE 2" - direct floor number
        (r"\bETAGE\s+(\d+)\b", lambda m: f"R+{m.group(1)}"),
        # Format NIV: "NIV 01", "NIV 02", etc. - common in French architectural plans
        (r"\bNIV\s*(\d{2,3})\b", lambda m: f"R+{int(m.group(1))-1}" if int(m.group(1)) > 0 else "RDC"),
        # Moroccan SITUATION format: "REZ-DE-CHAUSSEE_MEZZANINE" -> "RDC+MEZ"
        # Handle both "CHAUSSEE" and "CHAUSSÉE" and "CHAUSSÉÉ"
        (r"REZ[- ]?DE[- ]?CHAUSS?E+[_\s]+MEZZANINE", "RDC+MEZ"),
        # Format MEZZANINE (Moroccan)
        (r"\bMEZZANINE\b", "MEZZANINE"),
        # Format français avec °: "4°ETAGE", "4° ETAGE", "4e ETAGE"
        (r"\b(\d+)°?\s*ETAGE\b", lambda m: f"R+{m.group(1)}"),
        # Combinaison RDC + ETAGE (indique une maison avec plusieurs niveaux)
        (r"REZ\s*DE\s*CHAUSSEE\s*(ETAGE|\+|/)\s*ETAGE", "RDC+1"),
        (r"RDC\s*(ETAGE|\+|/)\s*ETAGE", "RDC+1"),
        (r"REZ[- ]?DE[- ]?CHAUSSEE\s+ETAGE", "RDC+1"),
        # Niveau unique RDC
        (r"NIVEAU\s*[:\s]*(Rez[- ]?de[- ]?chauss[éeè]e)", "RDC"),
        (r"\bRDC\b", "RDC"),
        (r"[Rr]ez[- ]?de[- ]?chauss[éeè]e\s+(?!ETAGE|ETG)", "RDC"),  # RDC seul, pas suivi de ETAGE
        # Patterns pour étage
        (r"NIVEAU\s*[:\s]*(1er\s*[ée]tage|Premier\s*[ée]tage|1re\s*[ée]tage)", "R+1"),
        (r"\bR\+1\b", "R+1"),
        (r"(1er\s*[ée]tage|Premier\s*[ée]tage|1re\s*[ée]tage)", "R+1"),
        (r"(\d+)\s*(?:er|e|[èe]me)\s*[ée]tage", lambda m: f"R+{m.group(1)}"),
        (r"NIVEAU\s*[:\s]*R\+(\d+)", lambda m: f"R+{m.group(1)}"),
        (r"\bR\+(\d+)\b", lambda m: f"R+{m.group(1)}"),
        # "duplex au 1er étage", "duplex au 2eme étage", "duplex au 2ème étage"
        (r"duplex\s+au\s+1er?\s*[ée]tage", "R+1"),
        (r"duplex\s+au\s+2[eè]me?\s*[ée]tage", "R+2"),
        (r"duplex\s+au\s+(\d+)[eè]me?\s*[ée]tage", lambda m: f"R+{m.group(1)}"),
        # "au 1er étage", "au 2ème étage" (standalone)
        (r"au\s+1er?\s*[ée]tage", "R+1"),
        (r"au\s+(\d+)[eè]me?\s*[ée]tage", lambda m: f"R+{m.group(1)}"),
    ]

    BUILDING_PATTERNS = [
        # Full IMMEUBLE description: "IMMEUBLE A ETAGE 1", "immeuble à rez de chaussée"
        # This must come FIRST to capture longer text before falling back to single letter
        r"IMMEUBLE\s+([A-Za-zÀ-ÿ\s]{1,30})",
        # Standalone building codes like "B2" in a list
        r"\b([A-Z]\d?)\s*-\d{3,4}\b",  # B2-403 -> extract B2
        r"BATIMENT\s*[:\s]*([A-Z]\d?)",
        r"B[ÂA]T\.?\s*[:\s]*([A-Z]\d?)",
        r"\bBATIMENT\s+([A-Z]\d?)\b",  # BATIMENT B2
        r"\b([A-Z]\d?)\b",  # Fallback: any single letter + digit (last resort)
    ]

    PROMOTER_SIGNATURES = {
        r"faubourg[\s\-]?immobilier|SCCV\s*FI": "Faubourg Immobilier",
        r"nexity|NEXITY": "Nexity",
        r"bouygues|BOUYGUES\s*IMMOBILIER": "Bouygues Immobilier",
        r"vinci|VINCI\s*IMMOBILIER": "Vinci Immobilier",
        r"kaufman|KAUFMAN": "Kaufman & Broad",
        r"eiffage|EIFFAGE": "Eiffage Immobilier",
        r"cogedim|COGEDIM": "Cogedim",
        r"icade|ICADE": "Icade",
        r"altarea|ALTAREA": "Altarea",
        r"promogim|PROMOGIM": "Promogim",
        r"groupe\s*duval|GROUPE\s*DUVAL|duval": "Groupe Duval",
        r"ink\s*architectes|INK": "Ink Architectes",
        r"pitch[\s\-]?promotion": "Pitch Promotion",
    }

    LIVING_SPACE_PATTERNS = [
        # LOGEMENT - primary keyword for total living space
        r"LOGEMENT\s*[:\s]*(\d+(?:[\.,]\d+)?)",
        r"LOGEMENT\s+(\d+(?:[\.,]\d+)?)",
        # Total surface patterns
        r"TOTAL\s*SURFACE\s*HABITABLE\s*[:\s]*(\d+(?:[\.,]\d+)?)",
        r"SURFACE\s*HABITABLE\s*[:\s]*(\d+(?:[\.,]\d+)?)",
        # Moroccan/Vertex format: "SURFACE : 48 m2"
        r"SURFACE\s*[:\s]*(\d+(?:[\.,]\d+)?)\s*m",
        r"SURFACE\s*[:\s]*(\d+(?:[\.,]\d+)?)\s*m2",
        # Moroccan format with floor: "SURFACE RDC : 26 m²", "SURFACE MEZZANINE : 16 m²"
        # Use non-capturing group for floor part so group(1) is always the surface
        r"SURFACE\s+(?:RDC|MEZ\w*)\s*[:\s]*(\d+(?:[\.,]\d+)?)\s*m?²?",
        # NEW: Moroccan format "SURFACE APPARTEMENT : 54 m2"
        r"SURFACE\s+APPARTEMENT\s*[:\s]*(\d+(?:[\.,]\d+)?)\s*m?²?",
        # Surface totale
        r"SURFACE\s+TOTALE\s*[:\s]*(\d+(?:[\.,]\d+)?)",
    ]
    
    # Multi-floor surface patterns - sum all floor surfaces
    MULTI_FLOOR_SPACE_PATTERNS = [
        # Sum all "SURFACE RDC" and "SURFACE MEZZANINE" patterns
        (r"SURFACE\s+RDC\s*[:\s]*(\d+(?:[\.,]\d+)?)", "rdc"),
        (r"SURFACE\s+MEZ\w*\s*[:\s]*(\d+(?:[\.,]\d+)?)", "mezz"),
    ]

    ANNEX_SPACE_PATTERNS = [
        r"TOTAL\s*SURFACE\s*ANNEXE\s*[:\s]*(\d+[\.,]\d+)",
        r"SURFACE\s*ANNEXE\s*[:\s]*(\d+[\.,]\d+)",
        r"TOTAL\s*EXT[ÉE]RIEURS?\s*[:\s]*(\d+[\.,]\d+)",
        # Vertex PDF format
        r"JARDIN\s+(\d+[\.,]\d+)",
        r"PORCHE\s+(\d+[\.,]\d+)",
        r"SURFACE\s*JARDIN\s*[:\s]*(\d+[\.,]\d+)",
        # NEW: Moroccan format "SURFACE TERRASSE : 53 m2"
        r"SURFACE\s*TERRASSE\s*[:\s]*(\d+(?:[\.,]\d+)?)\s*m?²?",
    ]
    
    # Nouvelles patterns pour surface propriété et espaces verts
    PROPERTY_SPACE_PATTERNS = [
        # Patterns simples (sans accents pour éviter les problèmes d'encodage)
        r"TOTAL\s+PROPRIETE\s+(\d+[\.,]\d+)",
        r"TOTAL\s+PROPRIETE\s+(\d+[\.,]\d+)\s*m",
        r"TOTAL\s*PROPRI[ÉE]T[ÉÈ]\s*[:\s]*(\d+[\.,]\d+)\s*m",
        r"TOTAL\s*PROPRI[ÉE]T[ÉÈ]\s*[:\s]*(\d+[\.,]\d+)",
        r"SURFACE\s*PROPRI[ÉE]T[ÉÈ]\s*[:\s]*(\d+[\.,]\d+)",
        r"SURFACE\s*DU\s*LOT\s*[:\s]*(\d+[\.,]\d+)",
        r"SURFACE\s*TERRAIN\s*[:\s]*(\d+[\.,]\d+)",
        r"SUPERFICIE\s*[:\s]*(\d+[\.,]\d+)",
    ]
    
    # Patterns pour gérer les newlines (texte sur plusieurs lignes)
    PROPERTY_SPACE_PATTERNS_MULTILINE = [
        r"TOTAL\s+PROPRIETE\s*\n?\s*(\d+[\.,]\d+)\s*m",
        r"TOTAL\s+PROPRIETE\s*\n?\s*(\d+[\.,]\d+)",
    ]
    
    GARDEN_SPACE_PATTERNS = [
        r"SURFACE\s*ESPACES\s*VERTS\s*[:\s]*(\d+[\.,]\d+)",
        r"SURFACE\s*JARDIN\s*[:\s]*(\d+[\.,]\d+)",
        r"JARDIN\s*PRIVATIF\s*[:\s]*(\d+[\.,]\d+)",
        r"ESPACES\s*VERTS\s*[:\s]*(\d+[\.,]\d+)",
        # Vertex PDF format - standalone values
        r"JARDIN\s+(\d+[\.,]\d+)",
        r"JARDIN$",
    ]
 

    def extract(self, text: str, reference_hint: Optional[str] = None,
                spatial_metadata: Optional[List[str]] = None) -> Dict[str, Any]:

        full_text = text
        if spatial_metadata:
            full_text += " " + " ".join(spatial_metadata)

        # Combiner les patterns normaux et multilignes pour surface_propriete
        property_patterns = self.PROPERTY_SPACE_PATTERNS + self.PROPERTY_SPACE_PATTERNS_MULTILINE
        
        # Extract program name
        program = self._extract_program(full_text)
        
        # Extract property type hint first (to use for reference prefix)
        property_type_hint = self._extract_property_type_hint(full_text)
        
        # Extract reference
        # First, try to use the reference_hint if provided
        reference = None
        if reference_hint and reference_hint not in ("UNKNOWN", ""):
            # Check if the hint exists in the text (with various normalizations)
            hint_normalized = reference_hint.upper().replace('-', '').replace(' ', '')
            text_normalized = full_text.upper().replace('-', '').replace(' ', '')
            
            # Try various formats: C22, C-22, C 22
            hint_variants = [
                reference_hint,
                reference_hint.replace('-', ''),
                reference_hint.replace('-', ' '),
                reference_hint.upper(),
            ]
            
            for variant in hint_variants:
                variant_normalized = variant.upper().replace('-', '').replace(' ', '')
                if variant_normalized in text_normalized:
                    reference = variant
                    break
            
            # If hint not found in text but looks valid (like "C71"), use it anyway
            # This prevents pattern matching from extracting wrong value like "701"
            if not reference and reference_hint:
                # Check if hint looks like a valid reference (letter + digits)
                hint_clean = reference_hint.replace('-', '').replace(' ', '')
                if len(hint_clean) >= 2 and hint_clean[0].isalpha() and hint_clean[1:].isdigit():
                    reference = reference_hint
        
        # If hint not found or not provided, use pattern matching
        if not reference:
            reference = self._extract_first(full_text, self.REF_PATTERNS, reference_hint or "UNKNOWN")
        
        # Add prefixes BEFORE validation so validation checks the final reference
        # Prepend type prefix to avoid conflicts (e.g., MAGASIN_1 vs APPARTEMENT_1)
        if property_type_hint == "magasin" and reference and reference.isdigit():
            reference = f"MAGASIN_{reference}"
        
        # Combine building with unit number to avoid conflicts
        building = self._extract_building(full_text)
        if building and reference and reference.isdigit():
            building_letter = building.strip()[0].upper() if building.strip() else ""
            if building_letter and building_letter.isalpha():
                reference = f"{building_letter}{reference}"
        
        # VALIDATION: Ensure reference actually exists in text (filter false positives like N5)
        # This prevents OCR errors from creating invalid parcel references
        reference_valid = False
        if reference and reference not in ("UNKNOWN", ""):
            # STRICT VALIDATION: Reject single-digit or single-letter references (OCR false positives)
            # Also reject if the reference is too short (e.g., just "5" or "N")
            ref_clean = reference.replace('-', '').replace(' ', '').replace('_', '')
            if len(ref_clean) <= 1:
                logger.warning(f"⚠️ Référence '{reference}' rejetée - trop courte (length={len(ref_clean)})")
                reference_valid = False
            elif ref_clean.isdigit() and len(ref_clean) < 3:
                # Reject short numeric references like "5", "35" (too generic)
                logger.warning(f"⚠️ Référence '{reference}' rejetée - numéro court trop générique")
                reference_valid = False
            else:
                # Normalize text for comparison
                text_normalized = full_text.upper().replace('-', '').replace(' ', '').replace('_', '')
                ref_normalized = ref_clean.upper()
                
                # Check if reference exists in text (exact match or as part of "N° lot: XXX")
                if ref_normalized in text_normalized:
                    reference_valid = True
                else:
                    # Also check with "N° lot:" prefix pattern
                    lot_pattern = f"N° lot:.*?{ref_normalized}"
                    if re.search(lot_pattern, full_text, re.IGNORECASE):
                        reference_valid = True
                    else:
                        # Also check with space instead of underscore (MAGASIN_1 vs MAGASIN 1)
                        ref_with_space = ref_normalized.replace('_', ' ')
                        if ref_with_space in text_normalized:
                            reference = ref_with_space.replace(' ', '_')  # Use original format
                            reference_valid = True
                        else:
                            # Check if reference_hint was provided and exists (even if reference was auto-detected)
                            if reference_hint:
                                hint_normalized = reference_hint.upper().replace('-', '').replace(' ', '').replace('_', '')
                                if hint_normalized in text_normalized:
                                    reference = reference_hint
                                    reference_valid = True
                            else:
                                # Only log warning for single-digit references (likely OCR errors)
                                # Allow multi-char references even if not found (may be valid for commercial)
                                if len(ref_normalized) <= 2 and ref_normalized.isdigit():
                                    # Reference not found in text - likely OCR false positive
                                    logger.warning(f"⚠️ Référence '{reference}' non trouvée dans le texte - possible faux positif OCR")
        
        # If reference is still not valid, log warning but continue with extraction
        # Only reject single-digit references as OCR errors, allow others
        if reference and not reference_valid:
            ref_clean = reference.replace('-', '').replace(' ', '').replace('_', '')
            if len(ref_clean) <= 1 or (ref_clean.isdigit() and len(ref_clean) < 3):
                logger.warning(f"⚠️ Référence '{reference}' non trouvée dans le texte - possible faux positif OCR")
            else:
                # For longer references (like MAGASIN_1, A1, A2), allow them even if not found in text
                # They may be valid for commercial PDFs or different formats
                logger.info(f"⚠️ Référence '{reference}' non trouvée dans le texte mais acceptée (format non standard)")
                reference_valid = True
        
        # Store the original reference before prefixing (for parcelLabel)
        original_reference = reference
        
        # Extract building for later use in return values
        building = self._extract_building(full_text)
        
        # Normalize reference: remove hyphens to ensure consistent keys (e.g., "C-03" -> "C03")
        # Do this AFTER building combination to avoid conflicts
        if reference:
            reference = reference.replace('-', '').replace(' ', '')
        if original_reference:
            original_reference = original_reference.replace('-', '').replace(' ', '')
        
        return {
            "reference": reference,
            "original_reference": original_reference,  # Keep original for parcelLabel
            "reference_valid": reference_valid,  # Validation: does reference exist in text?
            "floor": self._extract_floor(full_text),
            "building": building,
            "promoter": self._detect_promoter(full_text),
            "living_space": self._extract_surface(full_text, self.LIVING_SPACE_PATTERNS),
            "annex_space": self._extract_surface(full_text, self.ANNEX_SPACE_PATTERNS),
            "address": self._extract_address(full_text),
            "typology_hint": self._extract_typology_hint(full_text),
            "property_type_hint": property_type_hint,
            "program": program,
            "surface_propriete": self._extract_surface(full_text, property_patterns),
            "surface_espaces_verts": self._extract_surface(full_text, self.GARDEN_SPACE_PATTERNS),
            "multi_floor_surfaces": self._extract_multi_floor_surfaces(full_text),
        }

    def _extract_multi_floor_surfaces(self, text: str) -> Dict[str, float]:
        """Extract and sum surfaces from multiple floors (RDC + Mezzanine)."""
        surfaces = {}
        total = 0.0
        for pattern, floor_name in self.MULTI_FLOOR_SPACE_PATTERNS:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    val = float(match.replace(",", "."))
                    surfaces[floor_name] = val
                    total += val
                except ValueError:
                    pass
        surfaces["total"] = total
        return surfaces

    PROGRAM_PATTERNS = [
        # Direct patterns for common residence names - case insensitive
        r"(?:Square|Résidence|Domaine|Les| programme)\s+[A-Za-zÀ-ÿ'\-\s]{3,30}",
        r"(?:Résidence|Domaine|Les?|Le|La|Villa|Programme)\s+[A-Z][A-Za-zÀ-ÿ'\s\-]{3,40}",
        # Also try to find ensemble immobilier as fallback
        r"Ensemble\s+Immobilier\s+[A-Za-zÀ-ÿ'\-\s]{3,30}",
    ]

    def _extract_program(self, text: str) -> str:
        """Extract program/residence name from text."""
        # Normalize multiple spaces first
        text = re.sub(r'\s+', ' ', text)
        
        # PRIORITY 1: Look for ÎLOT/ILOT first (most specific for this type of PDF)
        # Note: Due to encoding issues, Î might appear as 'l' in the text
        if 'ÎLOT' in text.upper() or 'ILOT' in text.upper() or 'LOT' in text.upper():
            # Try to find the actual pattern - look for "lot" or "Lot" or "ILOT"
            idx = -1
            for pattern in ['ÎLOT', 'ILOT', 'LOT']:
                potential_idx = text.upper().find(pattern)
                if potential_idx >= 0:
                    idx = potential_idx
                    break
            if idx >= 0:
                # Get up to 22 chars after keyword (shorter to avoid extra text)
                segment = text[idx:idx+22]
                # Stop at common delimiters
                segment = re.split(
                    r'\s+(?:TYPE|NUMERO|BATIMENT|NOTA|SURFACES|LEGENDE|PLAN|ACCESSIBILITE|NIVEAU|PORTE|BOITE|CLOTURE)\b', 
                    segment, flags=re.IGNORECASE)[0].strip()
                if len(segment) > 3:
                    return segment
        
        # Try explicit known patterns first
        for p in self.PROGRAM_PATTERNS:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                name = m.group(0).strip()
                name = re.split(
                    r'\s+(?:Rue|Avenue|Boulevard|All[ée]e|Impasse|Place|Chemin|Route)\b',
                    name, flags=re.IGNORECASE)[0].strip()
                if len(name) > 5 and not any(w in name.upper() for w in
                        ['SURFACE', 'PLAN', 'ETAGE', 'TOTAL', 'TYPE', 'NOTA', 'NUMERO', 'BATIMENT']):
                    return name
        
        # Fallback: look for specific keywords in the text
        # Order matters - prioritize more specific terms
        keywords = ['SQUARE', 'RÉSIDENCE', 'DOMAINE', 'VILLA', 'ÎLOT', 'LOT', 'MAISON']
        for kw in keywords:
            if kw in text.upper():
                # Find the keyword and extract surrounding text
                idx = text.upper().find(kw)
                # Get up to 20 chars after keyword
                segment = text[idx:idx+20]
                # Stop at common delimiters
                segment = re.split(
                    r'\s+(?:TYPE|NUMERO|BATIMENT|NOTA|SURFACES|LEGENDE|PLAN|ACCESSIBILITE|NIVEAU)\b', 
                    segment, flags=re.IGNORECASE)[0].strip()
                if len(segment) > 5:
                    return segment
        
        # Try ENSEMBLE IMMOBILIER as last resort
        if 'ENSEMBLE IMMOBILIER' in text.upper():
            return 'Ensemble Immobilier'
        
        return ""

    def _extract_first(self, text, patterns, default):
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                # Handle patterns with 2 groups (e.g. r"\b([A-Z])\s(\d{3,4})\b")
                if m.lastindex and m.lastindex >= 2:
                    ref = "".join(g for g in m.groups() if g).strip()
                else:
                    ref = m.group(1).strip()
                ref = re.sub(r'\s+', '', ref)  # "A 101" → "A101"
                if ref.upper() in self.REF_BLACKLIST:
                    continue
                # Rejeter si la ref est dans "L261-15" (article de loi)
                if re.search(rf"\\b{re.escape(ref)}-\\d{{1,2}}\\b", text, re.IGNORECASE):
                    continue
                # Accept refs >= 3 chars OR pattern like A101 OR short numeric like 17, 1, 2 (for Moroccan floor plans)
                if len(ref) >= 3 or re.match(r"^[A-Z]\d{2,}$", ref) or re.match(r"^\d{1,3}$", ref):
                    return ref
        return default

    def _extract_building(self, text):
        """Extract building code without using blacklist (to allow B1, B2, etc.)"""
        for p in self.BUILDING_PATTERNS:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                ref = m.group(1).strip()
                if not ref:  # Handle optional group that didn't match
                    continue
                ref = re.sub(r'\s+', '', ref)
                # Building codes are typically single letter + optional digit (B2, A1, etc.)
                if re.match(r"^[A-Z]\d?$", ref):
                    return ref
                # Also accept longer IMMEUBLE descriptions like "A ETAGE 1"
                if re.match(r"^[A-Za-zÀ-ÿ].*", ref):
                    return ref
        return ""

    def _extract_floor(self, text):
        # First try to get all floors (for multi-floor PDFs like NIV 01 + NIV 02)
        all_floors = self._extract_all_floors(text)
        if len(all_floors) > 1:
            # Multiple floors found - return comma-separated for later processing
            return ",".join(all_floors)
        # Check for Moroccan multi-floor in SITUATION field
        # Pattern: "REZ-DE-CHAUSSEE_MEZZANINE" or similar
        # Handle both "CHAUSSEE" and "CHAUSSÉE" and "CHAUSSÉÉ"
        m = re.search(r"REZ[- ]?DE[- ]?CHAUSS?E+[_\s]+MEZZANINE", text, re.IGNORECASE)
        if m:
            return "RDC+MEZ"
        # Single floor or no floors - return the first match
        for pattern, val in self.FLOOR_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                return val(m) if callable(val) else val
        return ""

    def _extract_all_floors(self, text):
        """Extract all floor references from text (useful for multi-floor PDFs)."""
        floors = []
        seen = set()
        
        # Check for Moroccan multi-floor in SITUATION field first
        # Handle both "CHAUSSEE" and "CHAUSSÉE" and "CHAUSSÉÉ"
        m = re.search(r"REZ[- ]?DE[- ]?CHAUSS?E+[_\s]+MEZZANINE", text, re.IGNORECASE)
        if m:
            floors.append("RDC+MEZ")
            seen.add("RDC+MEZ")
        
        for pattern, val in self.FLOOR_PATTERNS:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                floor_val = val(m) if callable(val) else val
                if floor_val and floor_val not in seen:
                    floors.append(floor_val)
                    seen.add(floor_val)
        # Sort floors: RDC first, then MEZ, then R+1, R+2, etc.
        def floor_sort_key(f):
            if f == "RDC":
                return (0, 0)
            if f == "MEZZANINE":
                return (0, 1)
            if f == "RDC+MEZ":
                return (0, 2)
            m = re.match(r"R\+(\d+)", f)
            if m:
                return (1, int(m.group(1)))
            return (2, f)
        floors.sort(key=floor_sort_key)
        return floors

    def _detect_promoter(self, text):
        for pattern, name in self.PROMOTER_SIGNATURES.items():
            if re.search(pattern, text, re.IGNORECASE):
                return name
        return ""

    def _extract_surface(self, text, patterns):
        for p in patterns:
            m = re.search(p, text, re.IGNORECASE)
            if m:
                try:
                    return float(m.group(1).replace(",", "."))
                except ValueError:
                    pass
        return 0.0

    def _extract_address(self, text):
        # Look for postal code + city format - very flexible pattern
        # Handle encoding issues with special characters
        
        # Pattern 1: French postal code format with space: "29 200" or "77100"
        # Handle both "XX XXX" (with space) and "XXXXX" (no space) formats
        # Also handle "CITY POSTALCODE" format (MEAUX 77100)
        m = re.search(r'(\d{2,2}\s*\d{3,3}|\d{5,5})\s+(\w+)', text)
        if m:
            postal = m.group(1).replace(' ', '')  # Remove any spaces in postal code
            city = m.group(2)
            # Make sure it's a valid postal code (5 digits after removing spaces)
            if postal.isdigit() and len(postal) == 5:
                # Skip if city looks like noise (NOTA, RP, etc.)
                if city.upper() in ['NOTA', 'RP', 'JUIN', 'DATE', 'TYPE', 'NUMERO', 'BATIMENT', 'SURFACES', 'LEGENDE']:
                    pass  # Will try other patterns
                else:
                    # Format as "XXXXX CITY" - preserve original format (with or without space)
                    # Check if original had space
                    if ' ' in m.group(1):
                        return f"{postal[:2]} {postal[2:]} {city}"  # "29 200 BREST"
                    else:
                        return f"{postal} {city}"  # "77100 MEAUX"
        
        # Pattern 2: Look for any 5 digits with optional space followed by city name
        m = re.search(r'(\d{5,5})\s+(\w+)', text)
        if m:
            postal = m.group(1)
            city = m.group(2)
            if postal.isdigit() and len(postal) == 5:
                if city.upper() not in ['NOTA', 'RP', 'JUIN', 'DATE', 'TYPE', 'NUMERO', 'BATIMENT', 'SURFACES', 'LEGENDE']:
                    return f"{postal} {city}"
        
        # Pattern 3: Look for "CITY POSTALCODE" format (MEAUX 77100)
        m = re.search(r'\b(MEAUX|Paris|Lyon|Marseille|Brest|Bordeaux|Toulouse|Nantes|Nice|Lille|Strasbourg|Montpellier|Rennes|Grenoble|Dijon|Angers|Le Havre|Villeurbanne|Aix en Provence|Clermont-Ferrand|Saint-Étienne|Le Mans|Tours|Amiens|Mulhouse|Perpignan|Boulogne-Billancourt|Caen|Orléans|Limoges|Dunkerque|Saint-Denis|Saint-Paul|Saint-Louis|Pont-à-Mousson)\s+(\d{5,5})\b', text, re.IGNORECASE)
        if m:
            city = m.group(1)
            postal = m.group(2)
            return f"{postal} {city}"
        
        # Pattern 4: Look for any French city name followed by 5-digit postal code
        # Common French city names
        french_cities = r"MEAUX|Paris|Lyon|Marseille|Brest|Bordeaux|Toulouse|Nantes|Nice|Lille|Strasbourg|Montpellier|Rennes|Grenoble|Dijon|Angers|Le Havre|Villeurbanne|Aix|Clermont|Saint-Étienne|Le Mans|Tours|Amiens|Mulhouse|Perpignan|Boulogne|Caen|Orléans|Limoges|Dunkerque|Saint-Denis|Saint-Paul|Saint-Louis|Pont-à-Mousson"
        m = re.search(rf'\b({french_cities})\s+(\d{{5,5}})\b', text, re.IGNORECASE)
        if m:
            city = m.group(1)
            postal = m.group(2)
            return f"{postal} {city}"
        
        # Pattern 5: Look after PLAN DE LOCALISATION header
        m = re.search(r'PLAN DE LOCALISATION\s*\n?\s*(\d{2,2}\s*\d{3,3}|\d{5,5})\s+(\w+)', text, re.IGNORECASE)
        if m:
            postal = m.group(1).replace(' ', '')
            city = m.group(2)
            if ' ' in m.group(1):
                return f"{postal[:2]} {postal[2:]} {city}"
            else:
                return f"{postal} {city}"
        
        # Pattern 6: Handle street address with postal code and city
        # Look for "24 Avenue du ... MEAUX 77100" pattern
        m = re.search(
            r'(\d+)\s+(Avenue|Rue|Boulevard|Quai|Place|Impasse|All[ée]e|Chemin|Route)[^,\n]{0,50}', 
            text, re.IGNORECASE)
        if m:
            addr_start = m.group(0).strip()
            # Find the postal code and city after the street address
            m2 = re.search(r'(\w+)\s+(\d{5,5})\b', text[max(0, m.end()-20):], re.IGNORECASE)
            if m2:
                city = m2.group(1)
                postal = m2.group(2)
                if city.upper() not in ['NOTA', 'RP', 'JUIN', 'DATE', 'TYPE', 'NUMERO', 'BATIMENT', 'SURFACES', 'LEGENDE']:
                    return f"{addr_start}, {postal} {city}"
            return addr_start
        return ""

    def _extract_typology_hint(self, text):
        # Pattern "Appartement B13 -Type 2 -Niveau R+1"
        m = re.search(r"-?\s*Type\s*(\d+)", text, re.IGNORECASE)
        if m:
            return f"T{m.group(1)}"
        m = re.search(r"TYPE\s*[:\s]*(\d+)\s*pi[èe]ces?", text, re.IGNORECASE)
        if m:
            return f"T{m.group(1)}"
        m = re.search(r"(\d+)\s*pi[èe]ces?", text, re.IGNORECASE)
        if m:
            return f"T{m.group(1)}"
        
        # Pattern: "TYPE : CHAMBRE + SALON + SDB + KITCHENETTE" (Moroccan/French floor plans)
        # Count CHAMBRE/CHAMBRES as bedrooms, ignore SALON, SDB, KITCHENETTE
        m = re.search(r"TYPE\s*[:\s]*(.+?)(?:\n|$)", text, re.IGNORECASE)
        if m:
            type_str = m.group(1).upper()
            # Check for MAGASIN (shop/commercial) - special type
            if "MAGASIN" in type_str or "COMMERCE" in type_str:
                return "Commercial"
            # Count CHAMBRE occurrences
            chambre_count = len(re.findall(r"\bCHAMBRE\b", type_str))
            # Also check for alternative names
            chambre_count += len(re.findall(r"\bCHAMBER\b", type_str))
            chambre_count += len(re.findall(r"\bCH\b", type_str))  # Common abbreviation
            
            if chambre_count > 0:
                return f"T{chambre_count}"
            # If no bedroom found but has SALON (living room), assume T1 (studio with living room)
            elif "SALON" in type_str or "SEJOUR" in type_str:
                return "T1"
        
        return ""

    def _extract_property_type_hint(self, text: str) -> str:
        """Extract property type hint from text (MAGASIN, COMMERCE, etc.)"""
        text_upper = text.upper()
        
        # Check for MAGASIN in reference patterns
        if re.search(r"\bMAGASIN\b", text_upper):
            return "magasin"
        
        # Check for COMMERCE in TYPE field
        m = re.search(r"TYPE\s*[:\s]*(.+?)(?:\n|$)", text, re.IGNORECASE)
        if m:
            type_str = m.group(1).upper()
            if "MAGASIN" in type_str or "COMMERCE" in type_str:
                return "magasin"
        
        # Check for commercial keywords in the text
        # NOTE: Removed BUREAU as it causes false positives (e.g., "bureau de contrôle" means control office, not commercial property)
        if re.search(r"\b(COMMERCE|COMMERCIAL)\b", text_upper):
            return "commercial"
        
        return ""