"""
Spatial Extractor - Extraction par position spatiale du tableau récapitulatif
Méthode la plus fiable: analyse les blocs PyMuPDF par coordonnées
"""

import re
import logging
from typing import List, Dict, Tuple, Optional

logger = logging.getLogger(__name__)


class SpatialExtractor:

    TOTAL_KEYWORDS = [
        # French standard
        "TOTAL SURFACE HABITABLE", "SURFACE HABITABLE", "TOTAL SH",
        # Additional surface indicators
        "LOGEMENT", "LOGEMENT TOTAL", "SURFACE LOGEMENT",
        "SURFACE TOTALE", "SURFACE TOTALE HABITABLE",
        "TOTAL HABITABLE", "TOTAL SHAB", "SH TOTALE",
        # Exterior totals (sometimes used as main surface)
        "TOTAL EXTERIEUR", "TOTAL EXTÉRIEUR", "TOTAL EXT",
        # Other common formats
        "SURFACE TOT", "TOTALE SURFACE",
    ]
    ANNEX_KEYWORDS = [
        "TOTAL SURFACE ANNEXE", "SURFACE ANNEXE", "TOTAL ANNEXE",
        "TOTAL EXTERIEURS", "TOTAL EXT",
    ]
    SKIP_KEYWORDS = [
        "BATIMENT", "APPARTEMENT", "NIVEAU", "LEGENDE",
        "DATE", "IND", "PLAN", "ECHELLE", "SCCV", "VENTE", "TOTAL",
        "SURF. LOT", "SURF.LOT", "N° LOT", "N°LOT", "LOT:",  # Filtres pour PDFs scannés
    ]

    # Types de pièces uniques (on ne veut qu'une seule occurrence - la plus grande)
    UNIQUE_ROOM_TYPES = ["SEJOUR", "CUISINE", "SEJOUR/CUISINE", "ENTREE", "RECEPTION", "JARDIN", "CELLIER"]
    # Types qui peuvent avoir plusieurs occurrences numérotées
    NUMBERED_ROOM_TYPES = ["CHAMBRE", "SDB", "SDE", "WC", "BALCON", "GARAGE", "BUANDERIE", "PLACARD", "TERRASSE", "TERRACE"]

    def extract_from_pages(self, pages_data: List[Dict], reference_hint: Optional[str] = None) -> Dict:
        """
        Analyse les pages pour trouver le tableau récapitulatif.
        Returns dict: table_rows, living_space, annex_space, metadata_lines, source
        """
        result = {
            "table_rows": [],
            "living_space": None,
            "annex_space": None,
            "metadata_lines": [],
            "source": "spatial",
        }
        if not pages_data:
            return result

        for page_data in pages_data:
            page_result = self._analyze_page(page_data, reference_hint)
            if len(page_result["table_rows"]) > len(result["table_rows"]):
                result = page_result
        
        # Post-traitement: dédoublonnage intelligent
        result["table_rows"] = self._deduplicate_rows(result["table_rows"])
        
        return result

    def _deduplicate_rows(self, rows: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """
        Dédoublonne les lignes du tableau.
        Stratégie:
        - Pour les pièces uniques (SEJOUR, ENTREE, JARDIN, CELLIER...): garde la plus grande surface
        - Pour les pièces numérotées (CHAMBRE 1, 2...): garde toutes si numéros différents
        - Pour les doublons exacts (même nom, même surface): supprime
        """
        if not rows:
            return rows
        
        # Grouper par nom normalisé
        by_name: Dict[str, List[Tuple[str, float]]] = {}
        
        for name, surface_str in rows:
            try:
                surface = float(surface_str)
            except ValueError:
                continue
            
            # Normaliser le nom pour le regroupement
            norm_name = self._normalize_room_name(name)
            
            if norm_name not in by_name:
                by_name[norm_name] = []
            by_name[norm_name].append((name, surface))
        
        # Sélectionner les meilleures entrées
        deduplicated = []
        seen_surfaces = set()  # Pour éviter les doublons exacts
        
        for norm_name, entries in by_name.items():
            # Supprimer les doublons exacts (même surface)
            unique_entries = []
            for name, surface in entries:
                key = (norm_name, round(surface, 2))
                if key not in seen_surfaces:
                    seen_surfaces.add(key)
                    unique_entries.append((name, surface))
            
            if not unique_entries:
                continue
            
            # Si c'est une pièce unique (pas numérotée), prendre la plus grande
            if self._is_unique_room(norm_name):
                best = max(unique_entries, key=lambda x: x[1])
                deduplicated.append((best[0], str(best[1])))
                if len(unique_entries) > 1:
                    logger.info(f"    🔄 Dédoublonnage '{norm_name}': {len(unique_entries)} entrées → garde {best[1]}m²")
            else:
                # Pour les pièces numérotées, garder toutes les surfaces différentes
                for name, surface in unique_entries:
                    deduplicated.append((name, str(surface)))
        
        return deduplicated

    def _normalize_room_name(self, name: str) -> str:
        """Normalise un nom de pièce pour le regroupement"""
        name_upper = name.upper().strip()
        
        # Types uniques spéciaux (regroupent tous les suffixes)
        for unique in self.UNIQUE_ROOM_TYPES:
            if unique in name_upper:
                return unique
        
        # Pour les types numérotés (CHAMBRE, SDB, etc.)
        for room_type in self.NUMBERED_ROOM_TYPES:
            if room_type in name_upper:
                # Extraire le numéro si présent
                match = re.search(rf"{room_type}\s*(\d+)", name_upper)
                if match:
                    return f"{room_type}_{match.group(1)}"
                return room_type
        
        return name_upper

    def _is_unique_room(self, norm_name: str) -> bool:
        """Détermine si une pièce doit être unique (pas de multiples)"""
        return norm_name in self.UNIQUE_ROOM_TYPES

    def _analyze_page(self, page_data: Dict, reference_hint: Optional[str] = None) -> Dict:
        width = page_data.get("width", 1000)
        height = page_data.get("height", 1000)
        
        # Gerer les deux formats: blocks (PyMuPDF) et lines (OCR)
        blocks = page_data.get("blocks", [])
        ocr_lines = page_data.get("lines", [])

        result = {
            "table_rows": [],
            "living_space": None,
            "annex_space": None,
            "metadata_lines": [],
            "source": "spatial",
        }

        # Si on a des lignes OCR, les utiliser directement
        if ocr_lines:
            text_lines = self._convert_ocr_lines(ocr_lines)
        else:
            text_lines = self._extract_text_lines(blocks)
        if not text_lines:
            logger.info("  ⚠️ Aucune ligne de texte extraite")
            return result

        # DEBUG: Toutes les lignes avant filtrage (limité aux 30 premières)
        logger.info(f"  📄 TOUTES LIGNES ({len(text_lines)} total):")
        for l in text_lines[:30]:
            logger.info(f"    x0={l['x0']:.0f} y0={l['y0']:.0f} | '{l['text']}'")

        # Stratégie de filtrage par référence si fournie
        target_lines = text_lines
        ref_y = None
        
        if reference_hint:
            ref_y = self._find_reference_position(text_lines, reference_hint)
            if ref_y:
                logger.info(f"  📍 Référence '{reference_hint}' trouvée à y={ref_y:.0f}")
                # Prendre les lignes au-dessus de la référence (même tableau)
                target_lines = [l for l in text_lines if l["y0"] < ref_y + 100]
                logger.info(f"  🎯 Lignes au-dessus de référence: {len(target_lines)}")
        
        # Si pas de référence ou non trouvée, utiliser la zone haute
        if not ref_y:
            target_lines = [l for l in text_lines if l["y0"] < height * 0.90]
            logger.info(f"  ⬆️ Zone haute y<{height*0.90:.0f}: {len(target_lines)} lignes")

        # Filtrer les lignes avec des surfaces - version tolérance OCR artifacts
        surface_lines = []
        total_lines = []  # Separate list for TOTAL lines (used for living_space only)
        
        for line in target_lines:
            text = line["text"]
            text_upper = text.upper()
            # Recherche de surface plus tolérante: autorise ? et autres caractères après m²
            if re.search(r"\d+[\.,]\d+\s*m\s*[²2\xa0]?[?]*", text, re.IGNORECASE):
                surface_lines.append(line)
            # Separate TOTAL lines - they should NOT be added as rooms
            elif any(kw in text_upper for kw in self.TOTAL_KEYWORDS):
                total_lines.append(line)
            elif any(kw in text_upper for kw in [
                "CHAMBRE", "SEJOUR", "CUISINE", "SDB", "SDE", "WC",
                "ENTREE", "ENTRÉÉ",  # avec accent
                "BALCON", "CELLIER", "SALLE",
                "JARDIN", "CIRCULATION", "DGT", "DÉGAGEMENT", "COULOIR",
                "PALIER", "ESCALIER",
                "GARAGE", "BOX", "PARKING", "STATIONNEMENT",
                "BUANDERIE", "LINGERIE", "PLACARD", "RANGEMENT",
                "CAVE", "GRENIER", "REMISE", "LOCAL",
                # Nouveaux mots-clés pour PDFs avec traductions anglais
                "PIECE DE VIE", "PIÈCE DE VIE", "BAINS",
                "DÉGT", "DG T", "PL.", "PL ",  # Circulation/Placard abreges
                "SÉJOUR", "SÉJOUR/ CUISINE",
                # Terrasses et extérieurs
                "TERRASSE", "TERRASSES", "TERR.",
                "LOGGIA", "LOGGIAS",
            ]):
                surface_lines.append(line)

        surface_lines.sort(key=lambda l: l["y0"])

        # DEBUG
        logger.info(f"  🔎 LIGNES AVEC SURFACES ({len(surface_lines)} lignes):")
        for l in surface_lines:
            logger.info(f"    x0={l['x0']:.0f} y0={l['y0']:.0f} | '{l['text']}'")

        # Fusionner les lignes proches verticalement
        merged_lines = self._merge_close_lines(surface_lines)
        logger.info(f"  🔗 MERGED LINES ({len(merged_lines)} lignes):")
        for l in merged_lines:
            logger.info(f"    '{l['text']}'")

        # Phase 2: Pair surfaces without room names with WC rooms nearby
        # DISABLED - This causes misclassification. Let rooms be extracted without forced WC pairing.
        # The room name should come from the PDF text, not from automatic pairing.
        extra_rooms = []
        # Original pairing code disabled:
        # for line in merged_lines:
        #     ... pairing logic ...

        # Ajouter les rooms appariées aux merged_lines (désactivé)
        # if extra_rooms:
        #     merged_lines.extend(extra_rooms)

        for line in merged_lines:
            text = line["text"].strip()
            # ── Nettoyer le bruit OCR AVANT tout matching ──────────────────
            # Supprime les préfixes parasites: '| " } 2 O Q G 228 Séjour' → 'Séjour'
            text = self._strip_line_noise(text)
            if not text:
                continue
            text_upper = text.upper()

            # Détecter totaux
            for kw in self.TOTAL_KEYWORDS:
                if kw in text_upper:
                    m = re.search(r"(\d+[\.,]\d+)", text)
                    if m:
                        result["living_space"] = float(m.group(1).replace(",", "."))
                        logger.info(f"    🏠 TOTAL SH trouvé: {result['living_space']}")

            for kw in self.ANNEX_KEYWORDS:
                if kw in text_upper:
                    m = re.search(r"(\d+[\.,]\d+)", text)
                    if m:
                        result["annex_space"] = float(m.group(1).replace(",", "."))
                        logger.info(f"    📦 TOTAL ANNEXE trouvé: {result['annex_space']}")

            # Skip métadonnées
            if any(kw in text_upper for kw in self.SKIP_KEYWORDS):
                result["metadata_lines"].append(text)
                logger.info(f"    ⏭️ SKIP (métadonnée): '{text}'")
                continue

            # Pattern matching avec tolérance pour OCR artifacts (?, etc.)
            # Pattern 1: Format standard "Nom pièce    XX.XX m²" 
            match = re.match(
                r"^([A-Za-z\u00C0-\u017F][A-Za-z\u00C0-\u017F\s\-'/\.\+\d]*\S)"
                r"\s+(\d+[\.,]\d+)\s*m\s*[²2\xa0]?[?]*\s*$",
                text, re.IGNORECASE
            )
            
            # Pattern 2: Format collé "ENTREE/DGT 9,85m²" 
            if not match:
                match = re.match(
                    r"^([A-Za-z\u00C0-\u017F/][A-Za-z\u00C0-\u017F\s\-'/\+\d]*?)"
                    r"\s*(\d+[\.,]\d+)\s*m\s*[²2\xa0]?[?]*\s*$",
                    text, re.IGNORECASE
                )
            
            # Pattern 3: Format ultra-collé sans espace "CELLIER1,78m²"
            if not match:
                match = re.match(
                    r"^([A-Za-z\u00C0-\u017F][A-Za-z\u00C0-\u017F\s\-'/\+\d]*?)"
                    r"(\d+[\.,]\d+)\s*m\s*[²2\xa0]?[?]*\s*$",
                    text, re.IGNORECASE
                )
            
            # Pattern 4: Format inversé "XX.XX m² NOM"
            if not match:
                match = re.match(
                    r"^(\d+[\.,]\d+)\s*m\s*[²2\xa0]?[?]*\s+([A-Za-z\u00C0-\u017F][A-Za-z\u00C0-\u017F\s\-'/0-9]+)",
                    text, re.IGNORECASE
                )
            
            # Pattern 5: Integer surface (OCR dropped decimal) "NOM 397 m?"
            if not match:
                match = re.match(
                    r"^([A-Za-z\u00C0-\u017F][A-Za-z\u00C0-\u017F\s\-'/\.\+\d]*?\S)"
                    r"\s+(\d{3,4})\s*m\s*[²2\xa0]?[?]*\s*$",
                    text, re.IGNORECASE
                )
                
            if match:
                # Déterminer quel groupe est le nom et lequel est la surface
                if len(match.groups()) >= 2:
                    g1, g2 = match.group(1), match.group(2)
                    # Si g1 est un nombre → c'est la surface (pattern inversé)
                    if re.match(r"^\d+[\.,]\d+$", g1.replace(",", ".")):
                        surface_str = g1.replace(",", ".")
                        name = g2.strip()
                    else:
                        name = g1.strip()
                        surface_str = g2.replace(",", ".")
                else:
                    continue
                
                # Filtrer noms numériques et trop courts
                if not re.match(r"^\d+$", name) and len(name) >= 2:
                    # Fix missing decimal point from OCR: 397→3.97, 1226→12.26
                    surface_str = self._fix_missing_decimal(surface_str)
                    result["table_rows"].append((name, surface_str))
                    logger.info(f"    ✅ MATCH: '{name}' = {surface_str}m²")
                else:
                    logger.info(f"    ❌ Rejeté (nom numérique ou trop court): '{name}'")
                continue
            
            # Log si aucun pattern ne matche
            if re.search(r"\d+[\.,]\d+", text):
                logger.info(f"    ❌ NON MATCH (a nombre): '{text}'")

        logger.info(f"  📊 RESULT AVANT DÉDOUBLONNAGE: {len(result['table_rows'])} lignes")
        for name, surf in result["table_rows"]:
            logger.info(f"      - {name}: {surf}m²")
            
        return result

    # Known room keyword prefixes used to detect where real name starts after noise
    # Order: longer/more-specific first to avoid partial matches
    ROOM_KEYWORDS_FOR_STRIP = [
        "SÉJOUR", "SEJOUR", "SALLE DE BAIN", "SALLE D'EAU", "SALLE",
        "CHAMBRE", "ENTREE", "ENTRÉE", "CUISINE",
        "SDB", "SDE", "WC", "DGT", "DÉGAGEMENT", "DEGAGEMENT",
        "CIRCULATION", "COULOIR", "PALIER", "ESCALIER",
        "BALCON", "TERRASSE", "JARDIN", "LOGGIA", "PATIO",
        "GARAGE", "PARKING", "CAVE", "CELLIER", "BUANDERIE",
        "PLACARD", "RANGEMENT", "DRESSING",
        "SURFACE", "TOTAL",
    ]

    def _strip_line_noise(self, text: str) -> str:
        """
        Remove OCR noise PREFIX from a line — only strips characters BEFORE
        the first known room keyword that appears after actual noise.

        'O Q G 228 Séjour / Cuisine + Pl 24.59 m?' → 'Séjour / Cuisine + Pl 24.59 m?'
        'D / } =+ Séjour / Cuisine + Pl 24.59 m?' → 'Séjour / Cuisine + Pl 24.59 m?'
        '| SDB + WC 3.97 m?' → 'SDB + WC 3.97 m?'

        Key rule: only strip if the characters BEFORE the keyword are pure noise
        (pipes, braces, isolated digits/letters) — not a valid compound name prefix
        like "Séjour / " before "Cuisine".
        """
        text_upper = text.upper()

        # Find the earliest keyword preceded only by noise (no real word before it)
        best_idx = None
        for kw in self.ROOM_KEYWORDS_FOR_STRIP:
            idx = text_upper.find(kw)
            if idx <= 0:
                continue  # not found or already at start — no stripping needed
            prefix = text[:idx]
            # Noise = no letter sequences of 3+ chars in the prefix
            real_words = re.findall(r'[A-Za-zÀ-ÿ]{3,}', prefix)
            if not real_words:
                if best_idx is None or idx < best_idx:
                    best_idx = idx

        if best_idx is not None:
            text = text[best_idx:].strip()
        else:
            # Fallback: strip leading non-letter chars (pipes, quotes, braces)
            text = re.sub(r"^[^A-Za-zÀ-ÿ]+", "", text).strip()

        # Remove inline noise tokens: isolated punctuation/braces between words
        # 'Dgt. } + Pl 4.04 m?' → 'Dgt. + Pl 4.04 m?'
        text = re.sub(r'\s+[^\w\s\.À-ÿ+/]+\s+', ' ', text)

        return text.strip() if text else text

    def _fix_missing_decimal(self, surface_str: str) -> str:
        """
        OCR sometimes drops the decimal point: '397' → '3.97', '1226' → '12.26'.
        Only applies to 3-4 digit integers in plausible room surface range.
        A decimal value is returned unchanged.
        """
        # Already has decimal → no fix needed
        if "." in surface_str or "," in surface_str:
            return surface_str
        try:
            val = int(surface_str)
            if 100 <= val <= 999:    # 3 digits: 397 → 3.97
                return f"{surface_str[0]}.{surface_str[1:]}"
            elif 1000 <= val <= 9999:  # 4 digits: 1226 → 12.26
                return f"{surface_str[:2]}.{surface_str[2:]}"
        except ValueError:
            pass
        return surface_str

    def _find_reference_position(self, lines: List[Dict], reference: str) -> Optional[float]:
        """Trouve la position Y de la référence dans le texte"""
        ref_upper = reference.upper()
        for line in lines:
            if ref_upper in line["text"].upper():
                return line["y0"]
        return None

    def _merge_close_lines(self, lines: List[Dict], y_tol: float = 15.0, x_tol: float = 30.0) -> List[Dict]:
        """Fusionne les lignes proches verticalement"""
        if not lines:
            return []
        
        merged = []
        current_group = [lines[0]]
        
        for i in range(1, len(lines)):
            prev = current_group[-1]
            curr = lines[i]
            
            y_diff = abs(curr["y0"] - prev["y0"])
            x_diff = abs(curr["x0"] - prev["x0"])
            
            # Pour les tables: si même Y (très proche), fusionner seulement si X est aussi proche
            # et seulement pour les lignes qui semblent liées (nom+surface)
            if y_diff <= 3 and x_diff <= 80:  # Très proche Y mais X doit être raisonnablement proche
                current_group.append(curr)
            elif y_diff <= y_tol and x_diff <= x_tol:
                current_group.append(curr)
            else:
                merged.append(self._combine_line_group(current_group))
                current_group = [curr]
        
        if current_group:
            merged.append(self._combine_line_group(current_group))
        
        return merged

    def _combine_line_group(self, group: List[Dict]) -> Dict:
        """Combine un groupe de lignes en une seule ligne"""
        if len(group) == 1:
            return group[0]
        
        group.sort(key=lambda l: l["x0"])
        combined_text = " ".join(l["text"] for l in group)
        
        return {
            "text": combined_text,
            "x0": min(l["x0"] for l in group),
            "y0": min(l["y0"] for l in group),
            "x1": max(l["x1"] for l in group),
            "y1": max(l["y1"] for l in group),
        }

    def _find_nearby_room(self, lines: List[Dict], y_pos: float, y_tolerance: float = 50.0, only_wc: bool = False) -> Optional[Dict]:
        """Trouve un nom de pièce à proximité (en Y) d'une position donnée
        
        Args:
            lines: Liste des lignes à rechercher
            y_pos: Position Y de référence
            y_tolerance: Tolérance verticale en pixels
            only_wc: Si True, cherche seulement WC (pas SDB/WC)
        """
        if only_wc:
            # Only look for WC specifically
            for line in lines:
                if abs(line["y0"] - y_pos) <= y_tolerance:
                    text = line["text"].upper()
                    # Only match standalone WC, not SDB/WC
                    if re.search(r"\bWC\b", text) or re.search(r"\bW\.\s*C\.\b", text):
                        return line
            return None
        
        ROOM_KEYWORDS = ["CHAMBRE", "SEJOUR", "CUISINE", "SDB", "SDE", "WC", "ENTREE", "BALCON", "CELLIER", "SALLE", "JARDIN", "CIRCULATION", "DGT", "DÉGAGEMENT", "COULOIR", "PALIER"]
        
        # Chercher dans les lignes à proximité
        for line in lines:
            if abs(line["y0"] - y_pos) <= y_tolerance:
                text = line["text"].upper()
                if any(kw in text for kw in ROOM_KEYWORDS):
                    # Vérifier que ce n'est pas déjà une ligne avec surface
                    if not re.search(r"\d+[\.,]\d+\s*m", line["text"], re.IGNORECASE):
                        return line
        return None

    def _find_nearby_room_any(self, lines: List[Dict], y_pos: float, y_tolerance: float = 40.0) -> Optional[Dict]:
        """Trouve n'importe quel nom de pièce à proximité (en Y) - moins strict
        
        Cette méthode est utilisée pour trouver des pièces qui pourraient être
 manquantes mais dont la surface apparaît séparément dans le PDF.
        """
        ROOM_KEYWORDS = ["CHAMBRE", "SEJOUR", "CUISINE", "SDB", "SDE", "WC", "ENTREE", "BALCON", "CELLIER", "SALLE", "JARDIN", "CIRCULATION", "DGT", "DÉGAGEMENT", "COULOIR", "PALIER", "RGT", "LOCAL"]
        
        best_match = None
        best_dist = float('inf')
        
        for line in lines:
            dist = abs(line["y0"] - y_pos)
            if dist <= y_tolerance:
                text = line["text"].upper()
                if any(kw in text for kw in ROOM_KEYWORDS):
                    # Vérifier que ce n'est pas déjà une ligne avec surface
                    if not re.search(r"\d+[\.,]\d+\s*m", line["text"], re.IGNORECASE):
                        # Prendre le plus proche
                        if dist < best_dist:
                            best_dist = dist
                            best_match = line
        return best_match

    def _convert_ocr_lines(self, ocr_lines: List[Dict]) -> List[Dict]:
        """Convertit les lignes OCR au format attendu par le spatial extractor"""
        lines = []
        for line in ocr_lines:
            lines.append({
                "text": line.get("text", ""),
                "x0": line.get("x0", 0),
                "y0": line.get("y0", 0),
                "x1": line.get("x1", 0),
                "y1": line.get("y0", 0) + 20,  # Estimate height
            })
        return lines

    def _extract_text_lines(self, blocks: List[Dict]) -> List[Dict]:
        """Extrait lignes de texte avec coordonnées depuis blocks PyMuPDF"""
        lines = []
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                text = " ".join(s.get("text", "") for s in spans).strip()
                if not text:
                    continue
                bbox = line.get("bbox", [0, 0, 0, 0])
                lines.append({
                    "text": text,
                    "x0": bbox[0], "y0": bbox[1],
                    "x1": bbox[2], "y1": bbox[3],
                })
        return lines