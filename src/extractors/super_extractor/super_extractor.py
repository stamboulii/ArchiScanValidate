"""
SuperExtractor v3 - Orchestrateur principal
# VERSION: 2026-02-27-SUBTABLE-FIX
============================================

Pipeline:
1. TextExtractor     → texte brut (PyMuPDF + OCR)
2. SpatialExtractor  → tableau récapitulatif par positions
3. RoomNormalizer    → normalisation des noms
4. CompositeResolver → Réception = Séjour + Cuisine
5. MetadataExtractor → référence, étage, promoteur
6. PlanValidator     → validation mathématique

Usage:
    extractor = SuperExtractor()
    result = extractor.extract("plan.pdf", "A008")
    data = result.to_legacy_format()  # dict
"""

import re
import logging
from pathlib import Path
from typing import Dict, Optional, Any, List, Callable

from .models import RoomType, ExtractedRoom, ExtractionResult, EXTERIOR_ROOM_TYPES
from .text_extractor import TextExtractor
from .spatial_extractor import SpatialExtractor
from .room_normalizer import RoomNormalizer
from .composite_resolver import CompositeResolver
from .metadata_extractor import MetadataExtractor
from .plan_validator import PlanValidator
from .floor_utils import FloorUtils
from .room_parsers import RoomParsers
from .room_inference import RoomInference
from .deduplication import DeduplicationUtils

logger = logging.getLogger(__name__)


class SuperExtractor:

    SURFACE_PATTERNS = [
        # Format standard: NOM 00.00 m²
        r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-/\.\d]*?)\s+(\d+[\.,]\d+)\s*m[²2]",
        # Format collé: NOM 00.00m²  (pas d'espace avant m²)
        r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-/\.\d]*?)\s+(\d+[\.,]\d+)m[²2]",
        # Format avec : NOM: 00.00 m²
        r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-/\.\d]*?)\s*:\s*(\d+[\.,]\d+)\s*m[²2]",
        # Format tableau: NOM    00,00m²  (espaces multiples, virgule)
        r"([A-Za-zÀ-ÿ][A-Za-zÀ-ÿ\s\-/\.\d]*?)\s{2,}(\d+[\.,]\d+)\s*m[²2]?",
    ]

    SKIP_KEYWORDS = [
        "TOTAL", "SURFACE HABITABLE", "SURFACE ANNEXE",
        "PLAN", "VENTE", "DATE", "IND", "ECHELLE",
        "SURF", "LOT", "M00", "TYPE:", "N°",  # Filtres pour PDFs scannes
    ]

    def __init__(self, use_ocr: bool = True, tesseract_path: Optional[str] = None):
        self.text_extractor = TextExtractor(
            use_ocr=use_ocr, tesseract_path=tesseract_path
        )
        self.spatial_extractor = SpatialExtractor()
        self.normalizer = RoomNormalizer()
        self.composite_resolver = CompositeResolver()
        self.metadata_extractor = MetadataExtractor()
        self.validator = PlanValidator()
        # Modular components
        self.floor_utils = FloorUtils(self.normalizer)
        self.parsers = RoomParsers(self.normalizer)
        self.inference = RoomInference()
        self.dedup = DeduplicationUtils()

    def extract(
        self, pdf_path: str, reference_hint: Optional[str] = None
    ) -> ExtractionResult:
        """
        Point d'entree principal.

        Args:
            pdf_path: Chemin vers le fichier PDF
            reference_hint: Reference attendue (ex: "A008")

        Returns:
            ExtractionResult (appeler .to_legacy_format() pour obtenir un dict)
        """
        # Verifier si c'est un PDF multi-pages
        path = Path(pdf_path)
        if path.suffix.lower() == '.pdf':
            try:
                import fitz
                doc = fitz.open(pdf_path)
                page_count = len(doc)
                doc.close()
                
                if page_count > 1:
                    logger.info(f"PDF detecte avec {page_count} pages - analyse multi-pages")
                    return self._extract_multipage(pdf_path, reference_hint)
            except:
                pass
        
        # Extraction simple (une seule page)
        return self._extract_single_page(pdf_path, reference_hint)
    
    def _extract_multipage(self, pdf_path: str, reference_hint: str = None) -> ExtractionResult:
        """Extrait les donnees de plusieurs pages PDF.
        
        extract_all_pages() gère maintenant le groupage et la combinaison
        des pages partageant la même référence. On retourne juste le premier
        (ou celui qui correspond au hint).
        """
        all_results = self.extract_all_pages(pdf_path, reference_hint)
        
        if not all_results:
            result = ExtractionResult()
            result.validation_errors.append("Aucun plan detecte dans les pages")
            return result
        
        # Si un hint est fourni, chercher la référence correspondante
        if reference_hint and reference_hint in all_results:
            return all_results[reference_hint]
        
        # Sinon retourner le premier résultat combiné
        first_ref = list(all_results.keys())[0]
        return all_results[first_ref]
    

    def _split_if_distinct_apartments(self, page_results: list) -> list:
        """
        Détermine si des pages groupées sous la même référence sont:
        A) Le même lot sur plusieurs niveaux → retourne [page_results] (liste de 1)
        B) Des appartements distincts mal groupés → retourne [[page1], [page2], ...]
        
        Heuristiques:
        - Chaque page a une living_space DIFFÉRENTE et cohérente avec ses pièces
        - Les pièces des deux pages contiennent les mêmes types de rooms (séjour, entrée...)
        - La somme des living_space des pages != living_space max des pages
        """
        if len(page_results) <= 1:
            return [page_results]
        
        # Récupérer les surfaces déclarées valides
        declared_spaces = [(i, r.living_space) for i, r in enumerate(page_results) if r.living_space > 0]
        
        if len(declared_spaces) < 2:
            return [page_results]  # Pas assez d'info → combiner
        
        # Si toutes les pages ont la même surface déclarée → c'est le même lot
        unique_spaces = {round(ls, 1) for _, ls in declared_spaces}
        if len(unique_spaces) == 1:
            return [page_results]  # Même surface déclarée → multi-étage
        
        # Si les surfaces sont très différentes (pas un ratio de ~2x) → appartements distincts
        max_space = max(ls for _, ls in declared_spaces)
        min_space = min(ls for _, ls in declared_spaces)
        
        # Vérifier si chaque page a une living_space qui correspond à ses propres pièces
        distinct_apartments = []
        for i, result in enumerate(page_results):
            if result.living_space <= 0:
                distinct_apartments.append([result])
                continue
            
            interior = [r for r in result.rooms if not r.is_exterior and not r.is_composite]
            calc = round(sum(r.surface for r in interior), 2)
            diff_pct = abs(calc - result.living_space) / result.living_space if result.living_space > 0 else 1
            
            # Si le calc de cette page seule est proche de sa declared_space → appartement autonome
            if diff_pct < 0.20:  # Moins de 20% d'écart
                distinct_apartments.append([result])
            else:
                # Page incomplète → probablement un niveau d'un même lot
                if distinct_apartments:
                    distinct_apartments[-1].append(result)
                else:
                    distinct_apartments.append([result])
        
        # Si on a plusieurs groupes cohérents → appartements distincts
        if len(distinct_apartments) > 1:
            logger.info(f"    🏠 Détection: {len(distinct_apartments)} appartements distincts "
                       f"(surfaces: {[r[0].living_space for r in distinct_apartments]})")
            return distinct_apartments
        
        return [page_results]  # Par défaut: combiner

    def _combine_multi_floor_results(self, results: List['ExtractionResult']) -> 'ExtractionResult':
        """Combine les résultats de plusieurs pages (RDC + étage) en un seul."""
        if not results:
            return ExtractionResult()
        
        # Si on a plusieurs pages pour même référence, c'est probablement une maison multi-niveaux
        is_multi_page = len(results) > 1
        
        # Conserver le property_type "magasin" s'il est déjà défini
        original_property_type = results[0].property_type if results else "appartment"
        
        # Prendre le premier comme base
        combined = results[0]
        
        # Collecter toutes les pièces de toutes les pages
        all_rooms = list(combined.rooms)
        all_floors = [combined.floor] if combined.floor else []
        
        # ── CORRECTION SURFACE: prendre le MAX déclaré, pas additionner ─────
        # La surface habitable déclarée sur chaque page peut être le total
        # ou celle d'un seul niveau. On prend la valeur MAX.
        declared_living_spaces = [r.living_space for r in results if r.living_space > 0]
        declared_annex_spaces  = [r.annex_space  for r in results if r.annex_space  > 0]
        
        # Use max declared living space (not sum) - 87.79 not 84.99+84.99
        if declared_living_spaces:
            combined.living_space = max(declared_living_spaces)
        if declared_annex_spaces:
            combined.annex_space = max(declared_annex_spaces)
        
        # Propriétés: garder la valeur non-nulle trouvée (pas additionner)
        max_propriete     = max((r.surface_propriete     for r in results), default=0)
        max_espaces_verts = max((r.surface_espaces_verts for r in results), default=0)
        
        # Mettre à jour les propriétés
        combined.surface_propriete = max_propriete
        combined.surface_espaces_verts = max_espaces_verts
        
        # Page number: utiliser celle avec le plus de pièces (ou la dernière page)
        max_rooms_page = max(results, key=lambda r: len(r.rooms))
        combined.page_number = max_rooms_page.page_number
        
        for result in results[1:]:
            # Ajouter les pièces (les doublons seront dédoublonnés plus bas)
            all_rooms.extend(result.rooms)
            
            # Collecter les niveaux détectés
            if result.floor:
                all_floors.append(result.floor)
        
        # Dédoublonner les pièces fusionnées (même type+numéro+surface)
        all_rooms = self.dedup.final_dedup(all_rooms)
        
        # Remove false duplicates: if 'placard' has same surface as 'salle_de_bain', keep only salle_de_bain
        all_rooms = self._remove_false_duplicates(all_rooms)
        
        # Déterminer le floor combiné
        floor_set = set(f for f in all_floors if f)  # ignorer les vides
        
        # Normalize floor codes: "001" → "R+1", "002" → "R+2"
        normalized_floors = set()
        for f in floor_set:
            if re.match(r'^0*(\d+)$', f):
                n = int(re.match(r'^0*(\d+)$', f).group(1))
                normalized_floors.add(f"R+{n}" if n > 0 else "RDC")
            else:
                normalized_floors.add(f)
        floor_set = normalized_floors

        if is_multi_page and len(floor_set) <= 1 and (not floor_set or "RDC" in floor_set):
            combined.floor = "RDC+1"
        elif "RDC" in floor_set and "R+1" in floor_set:
            combined.floor = "RDC+1"
        elif "RDC" in floor_set and any(f.startswith("R+") for f in floor_set):
            other_floors = sorted([f for f in floor_set if f != "RDC" and f.startswith("R+")])
            combined.floor = f"RDC+{other_floors[-1].replace('R+', '')}"
        elif len(floor_set) > 1:
            # Multiple floors without RDC (e.g. R+1 + R+2 = duplex)
            sorted_floors = sorted(floor_set)
            combined.floor = "/".join(sorted_floors)
        elif floor_set:
            combined.floor = list(floor_set)[0]
        
        # Mettre à jour les pièces et surfaces
        combined.rooms = all_rooms
        combined.sources = {r.name_normalized: r.source for r in all_rooms}
        
        # Surface habitable: utiliser le MAX déclaré; si aucun, calculer
        if declared_living_spaces:
            combined.living_space = max(declared_living_spaces)
        else:
            combined.living_space = sum(
                r.surface for r in all_rooms if not r.is_exterior
            )
        
        combined.annex_space       = max(declared_annex_spaces, default=0)
        combined.surface_propriete  = max_propriete
        combined.surface_espaces_verts = max_espaces_verts
        
        # Niveaux lisibles pour customData
        niveaux = []
        if "RDC" in combined.floor.upper():
            niveaux.append("Rez-de-chaussée")
        if "+1" in combined.floor or "R+1" in combined.floor.upper():
            niveaux.append("Étage")
        combined.niveaux = niveaux if niveaux else combined._floor_to_niveaux(combined.floor)
        
        # Re-détecter le type de propriété après combinaison
        # Conserve le type "magasin" s'il était déjà détecté (from first result)
        # Also copy property_type_hint and typology to combined result
        if original_property_type == "magasin":
            combined.property_type = "magasin"
            combined.property_type_hint = "magasin"
        else:
            # Use property_type_hint and typology from first result if available
            property_hint = getattr(results[0], 'property_type_hint', '') if results else ''
            # Also get typology which may contain "Commercial" or "Magasin" from metadata
            typology_hint = getattr(results[0], 'typology', '') if results else ''
            combined.property_type_hint = property_hint
            combined.typology = typology_hint
            combined.property_type = self._detect_property_type(combined.rooms, combined.floor, typology_hint=typology_hint, property_type_hint=property_hint)
        
        # ── Re-détecter la typologie sur l'ensemble des pièces combinées ──────
        # Important: la page RDC seule peut ne pas avoir de chambre → T1 erroné
        # Also preserve Commercial/Magasin typology from metadata
        if combined.typology and combined.typology.lower() in ['commercial', 'magasin', 'commerce']:
            # Keep the Commercial typology from metadata
            pass
        else:
            combined.typology = self._detect_typology(combined.rooms)
        
        # ── Effacer les erreurs stales (venant de pages individuelles) ─────────
        # et re-valider sur le résultat combiné complet
        combined.validation_errors = []
        combined.validation_warnings = []
        self.validator.validate(combined)
        
        logger.info(
            f"  🔗 Combiné {len(results)} pages: floor={combined.floor}, "
            f"typology={combined.typology}, rooms={len(combined.rooms)}, "
            f"living={combined.living_space}"
        )
        
        return combined
    
    def extract_all_pages(self, pdf_path: str, reference_hint: str = None, progress_callback: Callable[[int, int, str], None] = None) -> Dict[str, Any]:
        """
        Extrait toutes les pages d'un PDF multi-pages.

        Stratégie: scan séquentiel avec regroupement par runs consécutifs.
        - Page 44: ref=B1, page 45: ref=B1, page 46: ref=C2 → B1 group=[44,45]
        - Chaque groupe = un lot immobilier (avec 1 ou plusieurs niveaux)

        Returns:
            Dict structuré:
            - Un seul niveau:  {"A08": ExtractionResult}
            - Multi-niveaux:   {"A18": {"A18_R+1": result1, "A18_R+2": result2}}
        """
        import fitz

        doc = fitz.open(pdf_path)
        page_count = len(doc)
        doc.close()

        logger.info(f"  📄 PDF: {page_count} pages")

        # ── ÉTAPE 1: Scan séquentiel → runs consécutifs par référence ──────────
        # Un "run" = séquence de pages avec la même référence
        # Ex: [A18(p1), A18(p2), A18(p3), B02(p4), B02(p5)] → 2 runs
        runs = []           # list of (ref, [page_results])
        current_ref = None
        current_pages = []
        plans_found = 0

        for page_num in range(page_count):
            if not self._is_plan_page(pdf_path, page_num):
                logger.info(f"  ⏭️ Page {page_num+1}: pas un plan")
                # Non-plan page breaks a run
                if current_pages:
                    runs.append((current_ref, current_pages))
                    current_ref = None
                    current_pages = []
                continue

            plans_found += 1
            result = self._extract_single_page(
                pdf_path, reference_hint, page_num, is_multipage_context=True
            )
            ref = result.reference if (result.reference and result.reference != "UNKNOWN")                   else f"PAGE_{page_num+1}"
            # Call progress callback if provided
            if progress_callback:
                progress_callback(page_num + 1, page_count, ref)

            if current_ref is None:
                # Start new run
                current_ref = ref
                current_pages = [result]
            elif ref == current_ref or self._refs_are_same(ref, current_ref):
                # Continue current run (same ref or close variant like A18/H180)
                current_pages.append(result)
                logger.info(f"  ➕ Page {page_num+1} → run '{current_ref}' ({len(current_pages)} pages)")
            else:
                # Ref changed → close current run, start new one
                runs.append((current_ref, current_pages))
                logger.info(f"  ✅ Run '{current_ref}' fermé: {len(current_pages)} pages")
                current_ref = ref
                current_pages = [result]

        # Close last run
        if current_pages:
            runs.append((current_ref, current_pages))

        logger.info(f"  📊 {plans_found} plan(s), {len(runs)} lot(s) détecté(s)")

        # ── ÉTAPE 2: Convertir chaque run en résultat(s) ────────────────────────
        all_results = {}

        for ref, page_results in runs:
            # Consolider les variantes de ref dans le run (H180 → A18)
            ref = self._dominant_ref(ref, page_results)

            # Skip invalid references (not found in PDF text - likely OCR false positive like N5)
            # Check all page_results for validity
            if page_results and not getattr(page_results[0], 'reference_valid', True):
                logger.warning(f"⚠️ Référence '{ref}' ignorée - non trouvée dans le texte PDF")
                continue

            if len(page_results) == 1:
                # Single page lot
                single = page_results[0]
                single.reference = ref
                single.parcel_label = ref  # Ensure consistency
                if single.living_space > 0:
                    single.rooms = self.dedup.filter_by_reference(
                        single.rooms, ref, single.living_space
                    )
                    single.sources = {r.name_normalized: r.source for r in single.rooms}
                all_results[ref] = single

            else:
                # Multi-page lot: check for distinct floors
                floor_split = self.floor_utils.build_floor_split(ref, page_results)

                if len(floor_split) > 1:
                    # Duplex/maison: create parent ExtractionResult with nested floors
                    import copy
                    parent = copy.deepcopy(list(floor_split.values())[0])
                    parent.reference = ref
                    # Also set parcel_label to ref for consistency in lookup
                    parent.parcel_label = ref
                    parent.floor = "/".join(floor_split.keys())
                    parent.floor_results = list(floor_split.values())
                    # Use MAX of floor results (each floor's declared living space is the total for that floor)
                    # floor_utils now calculates correctly excluding CIRCULATION
                    parent.living_space = round(
                        max(r.living_space for r in parent.floor_results), 2)
                    parent.annex_space = round(
                        max(r.annex_space for r in parent.floor_results), 2)
                    parent.typology = self._detect_typology(
                        [room for r in parent.floor_results for room in r.rooms])
                    all_results[ref] = parent
                    logger.info(f"  🏢 '{ref}': {len(floor_split)} niveaux → JSON imbriqué")
                else:
                    # Same floor repeated (multiple views): combine into one
                    combined = self._combine_multi_floor_results(page_results)
                    combined.reference = ref
                    combined.parcel_label = ref  # Ensure consistency
                    all_results[ref] = combined
                    logger.info(f"  🔗 '{ref}': {len(page_results)} vues → combiné")

        return all_results

    def _refs_are_same(self, ref_a: str, ref_b: str) -> bool:
        """
        Two refs are considered the same lot if:
        - They are identical
        - One is a height code variant of the other (H180 vs A18 — false ref)
        - One starts with the other (A18 vs A18_PMR)
        """
        if ref_a == ref_b:
            return True
        # Height codes: H + 3 digits = not a real ref
        import re
        HEIGHT_RE = re.compile(r'^H\d{3,4}$')
        if HEIGHT_RE.match(ref_a) or HEIGHT_RE.match(ref_b):
            return True
        # One is prefix of the other
        if ref_a.startswith(ref_b) or ref_b.startswith(ref_a):
            return True
        return False

    def _dominant_ref(self, current_ref: str, page_results: list) -> str:
        """Pick the best reference from a run of pages."""
        import re
        LOT_RE = re.compile(r'^[A-Z]\d{2,4}$')
        HEIGHT_RE = re.compile(r'^H\d{3,4}$')

        # Collect all refs from pages
        refs = [r.reference for r in page_results
                if r.reference and r.reference != "UNKNOWN"]
        refs.append(current_ref)

        # Score: lot-pattern ref wins over height code wins over generic
        def score(r):
            if LOT_RE.match(r):
                return 3
            if HEIGHT_RE.match(r):
                return 0
            if r.startswith("PAGE_"):
                return 0
            return 1

        best = max(refs, key=score) if refs else current_ref
        return best

    def _normalize_floor_label(self, floor: str) -> str:
        """Normalize floor label: '001' -> 'R+1', '002' -> 'R+2', 'RDC' stays."""
        import re
        if not floor:
            return ""
        m = re.match(r'^0*(\d+)$', floor.strip())
        if m:
            n = int(m.group(1))
            return f"R+{n}" if n > 0 else "RDC"
        return floor.strip()

    def _build_floor_split(self, ref: str, page_results: list) -> dict:
        """
        From a list of pages for the same ref, build per-floor results.

        Returns:
            - {"A18_R+1": result1, "A18_R+2": result2} if distinct floors found
            - {} if all pages have the same floor (caller will combine)
        """
        import copy, re

        # Group pages by normalized floor
        by_floor = {}
        for r in page_results:
            floor = self.floor_utils.normalize_floor_label(r.floor or "")
            if not floor:
                floor = "unknown"
            by_floor.setdefault(floor, []).append(r)

        # Remove "unknown" — can't assign to a floor
        known = {f: pages for f, pages in by_floor.items() if f != "unknown"}

        # If only one known floor (or none), no split possible
        if len(known) <= 1:
            return {}

        # Build combined surface lookup from all pages
        all_rooms_by_norm = {}
        for r in page_results:
            for room in r.rooms:
                key = room.name_normalized
                if key not in all_rooms_by_norm or room.confidence > all_rooms_by_norm[key].confidence:
                    all_rooms_by_norm[key] = room

        if not all_rooms_by_norm:
            return {}

        declared_total  = max((r.living_space for r in page_results if r.living_space > 0), default=0)
        declared_annexe = max((r.annex_space  for r in page_results if r.annex_space  > 0), default=0)

        split = {}
        assigned_norms = set()  # track which rooms have been assigned

        for floor in sorted(known.keys()):
            pages = known[floor]

            # Get floor plan labels for this floor
            # (room names printed on the drawing, not in the table)
            labels = self.floor_utils._get_floor_plan_labels(pages, all_rooms_by_norm)

            if labels:
                floor_rooms = labels
                assigned_norms.update(r.name_normalized for r in floor_rooms)
            else:
                # Fallback: assign remaining unassigned rooms to this floor
                floor_rooms = [r for r in all_rooms_by_norm.values()
                               if r.name_normalized not in assigned_norms]

            if not floor_rooms:
                continue

            base = copy.deepcopy(page_results[0])
            base.reference = ref
            base.floor = floor
            base.rooms = floor_rooms
            base.sources = {r.name_normalized: r.source for r in floor_rooms}
            base.living_space = round(
                sum(r.surface for r in floor_rooms if not r.is_exterior), 2)
            base.annex_space = round(
                sum(r.surface for r in floor_rooms if r.is_exterior), 2)
            base.typology = self._detect_typology(floor_rooms)
            base.validation_errors = []
            base.validation_warnings = []

            floor_key = f"{ref}_{floor}"
            split[floor_key] = base
            logger.info(f"    🏢 {floor_key}: {len(floor_rooms)} pièces, "
                        f"habitable={base.living_space}m²")

        return split

    def _get_floor_plan_labels(self, pages: list, all_rooms_by_norm: dict) -> list:
        """
        Extract room names that appear as labels on the floor plan drawing.
        
        In two-block format PDFs, the surface table groups ALL names together
        (no name is immediately adjacent to its surface), so we can't use
        adjacency to distinguish labels from table entries.
        
        Instead, we look for room names that appear OUTSIDE the surface table block.
        The surface table block is the large contiguous group of room names.
        Floor plan labels are the same room names appearing at OTHER positions
        (earlier in the text, with different x-coordinates on the drawing).
        
        For each page, we look at PyMuPDF spatial data to find names that are
        positioned on the FLOOR PLAN (left side, x < 60% of page width) 
        rather than in the TABLE (right side).
        """
        import re
        found = []
        seen = set()

        for result in pages:
            raw = getattr(result, 'raw_text', '') or ''
            if not raw:
                continue
            
            lines = [l.strip() for l in re.split(r'[\n\r]+', raw) if l.strip()]
            SURF_RE = re.compile(r'^\d+[,.]\d+\s*(?:m[²2]?)?\s*$')
            
            # Find the surface table block: the longest consecutive run of
            # (name, name, ..., surface, surface, ...) or interleaved
            # We identify it by finding where the surface run starts
            surface_positions = [i for i, l in enumerate(lines) if SURF_RE.match(l)]
            
            if not surface_positions:
                continue
            
            # The table block spans from the first name before the first surface
            # to the last surface. Names BEFORE this block are floor plan labels.
            first_surf = surface_positions[0]
            
            # Find the start of the name block preceding the surface run
            # Walk backwards from first_surf to find where names start
            table_name_start = first_surf
            for i in range(first_surf - 1, -1, -1):
                line = lines[i]
                if SURF_RE.match(line):
                    continue
                # Check if it's a valid room name
                norm, rtype, _, _, _ = self.normalizer.normalize(line)
                if rtype and norm in all_rooms_by_norm:
                    table_name_start = i
                else:
                    break  # Stop at first non-room line
            
            # Lines BEFORE table_name_start are potential floor plan labels
            # Lines AT OR AFTER table_name_start are table entries
            for i in range(table_name_start):
                line = lines[i]
                if SURF_RE.match(line) or len(line) < 3:
                    continue
                norm, rtype, _, _, _ = self.normalizer.normalize(line)
                if not norm or not rtype:
                    continue
                if norm in seen:
                    continue
                if norm in all_rooms_by_norm:
                    seen.add(norm)
                    found.append(all_rooms_by_norm[norm])

        return found

    def _is_plan_page(self, pdf_path: str, page_num: int) -> bool:
        """Detecte si une page contient un plan d'architecture."""
        import fitz
        import re
        
        try:
            doc = fitz.open(pdf_path)
            page = doc[page_num]
            text = page.get_text()
            doc.close()
            
            # Essayer aussi OCR si le texte PyMuPDF est vide
            if len(text.strip()) < 20:
                logger.info(f"    Page {page_num + 1}: texte PyMuPDF faible, utilisation OCR...")
                # Extraire texte OCR pour cette page seulement
                text = self._extract_ocr_single_page(pdf_path, page_num)
            
            # Indicateurs d'un plan d'architecture - plus de patterns specifiques
            lot_patterns = [
                r'\b[A-Z]\d{2,4}\b',  # A01, A001, A0001
                r'\bLOT[_\s]?\d+\b',    # LOT_1, LOT 1
                r'\bT\d+\b',       # T1, T2, T3
                r'\b\d+\s*pieces?\b',  # 3 pieces, 3 piece
                r'\bSURFACE\b',     # SURFACE
                r'\bMAGASIN\b',     # MAGASIN
                r'\bAPPARTEMENT\b',  # APPARTEMENT
                r'\bMAISON\b',     # MAISON
                r'\bCOMMERCE\b',   # COMMERCE
                r'\bIMMEUBLE\b',   # IMMEUBLE
                r'\bBATIMENT\b',   # BATIMENT
                r'\bETAGE\s*\d+\b',  # ETAGE 1, ETAGE 2
            ]
            
            matched_patterns = []
            score = 0
            for pattern in lot_patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    score += 1
                    matched_patterns.append(pattern)
            
            # Verifier les mots cles d'un plan - necessite plus de mots cles
            plan_keywords = ['appartement', 'chambre', 'sejour', 'cuisine', 'sdb', 'wc', 
                           'terrasse', 'balcon', 'etage', 'rdc', 'surface', 'habitable', 'magasin', 
                           'commerce', 'maison', 'immeuble', 'batiment', 'niveau', 'rez', 'chal',
                           'salon', 'douche', 'entree', 'hall', 'couloir', 'placard', 'cuisine']
            keyword_count = sum(1 for kw in plan_keywords if kw in text.lower())
            matching_keywords = [kw for kw in plan_keywords if kw in text.lower()]
            
            # Decision: plus permissif - accepter avec 1 pattern ou 1 keyword
            # pour capturer tous les formats de PDF architecturaux
            # MAIS: reject pages with only building/floor hints but no actual parcel data
            is_plan = score >= 1 or keyword_count >= 1
            
            # Additional check: require actual parcel data (not just building/floor headers)
            # Reject pages that only have "IMMEUBLE X" or "ETAGE Y" without actual parcel info
            if is_plan:
                # Must have actual parcel indicators
                parcel_indicators = [
                    r'\bSURFACE\b',
                    r'\bAPPARTEMENT\s*N?\s*[:]?\s*\d+',  # APPARTEMENT 35 or APPARTEMENT N° 35
                    r'\bMAGASIN\s*N?\s*[:]?\s*\d+',  # MAGASIN 1
                    r'\b\d+\s*m²?\b',  # 48 m2
                    r'\bTYPE\s*[:]',  # TYPE: CHAMBRE...
                ]
                has_parcel_data = any(re.search(p, text, re.IGNORECASE) for p in parcel_indicators)
                
                # If no parcel data, reject the page
                if not has_parcel_data:
                    is_plan = False
                    logger.info(f"    📄 Page {page_num + 1}: rejected - no parcel data (only headers)")
            
            # Log details for debugging
            logger.info(f"    📄 Page {page_num + 1}:")
            logger.info(f"       Patterns matched ({score}): {matched_patterns}")
            logger.info(f"       Keywords found ({keyword_count}): {matching_keywords}")
            logger.info(f"       → Is plan: {is_plan}")
            
            return is_plan
            
        except Exception as e:
            logger.warning(f"Erreur detection plan page {page_num}: {e}")
            return False
    
    def _extract_ocr_single_page(self, pdf_path: str, page_num: int) -> str:
        """Extrait le texte OCR pour une seule page."""
        try:
            import fitz
            from PIL import Image, ImageEnhance, ImageFilter
            import pytesseract
            
            doc = fitz.open(pdf_path)
            page = doc[page_num]
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            doc.close()
            
            # Preprocessing
            img_gray = img.convert('L')
            enhancer = ImageEnhance.Contrast(img_gray)
            img_gray = enhancer.enhance(2.0)
            img_gray = img_gray.filter(ImageFilter.SHARPEN)
            
            # OCR
            text = pytesseract.image_to_string(img_gray, lang="fra+eng", config="--oem 3 --psm 3")
            return text
        except Exception as e:
            logger.warning(f"Erreur OCR page {page_num}: {e}")
            return ""
    
    def _extract_single_page(self, pdf_path: str, reference_hint: str = None, page_num: int = None, is_multipage_context: bool = False) -> ExtractionResult:
        """Extrait les donnees d'une seule page."""
        page_info = f" (page {page_num + 1})" if page_num is not None else ""
        logger.info(f"🔍 SuperExtractor v3: {pdf_path}{page_info}")
        result = ExtractionResult()
        result.page_number = page_num + 1 if page_num is not None else 1
        path = Path(pdf_path)

        if not path.exists():
            result.validation_errors.append(f"Fichier non trouvé: {pdf_path}")
            return result

        # Reset normalizer pour cette extraction
        self.normalizer.reset()

        # ── ÉTAPE 1: Extraction texte brut ────────────────────
        text_data = self.text_extractor.extract(pdf_path, page_num=page_num)
        primary_text = text_data["text_pymupdf"] or text_data["text_ocr"]
        result.raw_text = primary_text
        logger.info(
            f"  📄 Texte: {len(primary_text)} chars, "
            f"source={text_data['primary_source']}"
        )

        # ── ETAPE 2: Extraction spatiale (tableau récap) ──────
        spatial_data = self.spatial_extractor.extract_from_pages(
            text_data.get("ocr_pages_data") or text_data["pages_data"], 
            reference_hint=reference_hint
        )
        spatial_rows = spatial_data["table_rows"]
        logger.info(f"  📐 Spatial: {len(spatial_rows)} lignes de tableau")
        logger.info(f"  📐 Spatial rows trouvées:")
        for name, surface in spatial_rows:
            logger.info(f"    '{name}' → {surface}")
        logger.info(f"  📄 Texte brut (500 chars):")
        logger.info(primary_text[:500])

        # ── ÉTAPE 3: Construction des pièces ──────────────────
        rooms = []

        # # Priorité 1: tableau spatial (le plus fiable)
        # if spatial_rows:
        #     rooms = self._rooms_from_table(spatial_rows, "spatial")
        #     logger.info(f"  ✅ {len(rooms)} pièces depuis tableau spatial")

        # # Priorité 2: regex sur texte PyMuPDF
        # if len(rooms) < 3:
        #     rooms_regex = self._rooms_from_regex(
        #         text_data["text_pymupdf"], "pymupdf"
        #     )
        #     rooms = self._merge_rooms(rooms, rooms_regex)
        #     logger.info(f"  📝 +regex PyMuPDF → {len(rooms)} pièces")

        # # Priorité 3: regex sur texte OCR
        # if len(rooms) < 3 and text_data["text_ocr"]:
        #     rooms_ocr = self._rooms_from_regex(text_data["text_ocr"], "ocr")
        #     rooms = self._merge_rooms(rooms, rooms_ocr)
        #     logger.info(f"  🔍 +regex OCR → {len(rooms)} pièces")
        # ── ÉTAPE 3: Construction des pièces ──────────────────
        rooms = []

        # Priorité 1b: Two-block format (NOM/NOM/NOM...SURF/SURF/SURF)
        # Try this FIRST — if it succeeds, skip spatial (incompatible formats)
        raw_pymupdf = text_data.get("raw_pymupdf", text_data["text_pymupdf"])
        rooms_tb = []
        
        # PRE-SCAN: Find LOGEMENT and Terrasse in raw text FIRST
        declared_living = 0.0
        has_terrace = False
        if raw_pymupdf:
            import re as _re_pre
            # Find LOGEMENT surface
            _lg_match = _re_pre.search(r'LOGEMENT\s+(\d+[,\.]\d+)', raw_pymupdf, _re_pre.IGNORECASE)
            if _lg_match:
                declared_living = float(_lg_match.group(1).replace(',', '.'))
            # Check for terrace (keyword-based detection)
            if 'Terrasse' in raw_pymupdf:
                has_terrace = True
        
        if raw_pymupdf:
            self.normalizer.reset()
            # Try two-block format first (NAMES block / SURFACES block)
            # Pass declared_living to use for filtering instead of inferring from runs
            rooms_tb, tb_living, tb_annex = self._rooms_from_two_block_text(
                raw_pymupdf, "pymupdf_tb", declared_living
            )
            import sys as _sys2
            # print(f"[DBG two-block] rooms_tb={[(r.name_normalized, r.surface, r.source) for r in rooms_tb]}", file=_sys2.stderr)
            if rooms_tb:
                rooms = self._merge_rooms(rooms, rooms_tb)
                logger.info(f"  📦 Two-block PyMuPDF → {len(rooms)} pièces")
                # Use declared_living from LOGEMENT if found (>40, covers most apartments),
                # otherwise use tb_living. The >40 threshold ensures we don't use noise values.
                if declared_living > 40:
                    spatial_data['living_space'] = declared_living
                elif tb_living > 0:
                    spatial_data['living_space'] = tb_living
                if tb_annex > 0:
                    spatial_data['annex_space'] = tb_annex
            # Mark terrace if found
            if has_terrace:
                spatial_data['has_terrace'] = True

            # Try inverted-pairs format (SURFACE\nNAME interleaved on floor plan)
            # Only use if it produces a coherent result (interior sum matches declared total)
            self.normalizer.reset()
            rooms_inv, inv_living, inv_annex = self._rooms_from_inverted_pairs(raw_pymupdf, "pymupdf_inv")
            
            # Always check for exterior rooms (terrace, balcony) from inverted-pairs
            # even when two-block found interior rooms
            logger.info(f"DEBUG: rooms_inv={len(rooms_inv) if rooms_inv else 0}, rooms_tb={len(rooms_tb) if rooms_tb else 0}")
            
            # Also search for "Terrasse" directly in raw text and extract surface
            # This is a fallback when inverted-pairs fails
            if raw_pymupdf:
                import re as _re_terr
                # Look for "Terrasse" followed by a surface value
                _terr_match = _re_terr.search(r'Terrasse\s+(\d+[,\.]\d+)\s*m?²?', raw_pymupdf, _re_terr.IGNORECASE)
                if _terr_match:
                    try:
                        terr_surface = float(_terr_match.group(1).replace(',', '.'))
                        logger.info(f"Found Terrasse with surface {terr_surface} in raw text")
                        # Create a terrace room
                        terr_room = ExtractedRoom(
                            name_raw='Terrasse', 
                            name_normalized='terrasse', 
                            surface=terr_surface,
                            room_type=RoomType.TERRACE, 
                            is_exterior=True, 
                            room_number=None,
                            source='raw_text_fallback',
                            confidence=0.9,
                        )
                        rooms = self._merge_rooms(rooms, [terr_room])
                        logger.info(f"  🔁 Added Terrasse ({terr_surface}) from raw text")
                        spatial_data['annex_space'] = max(spatial_data.get('annex_space') or 0, terr_surface)
                    except (ValueError, AttributeError) as e:
                        logger.debug(f"Could not parse terrace surface: {e}")
            
            if rooms_inv and rooms_tb:
                logger.info(f"DEBUG: Processing {len(rooms_inv)} inverted-pairs rooms")
                for r in rooms_inv:
                    logger.info(f"DEBUG: inv room: {r.name_raw}, surface={r.surface}, type={r.room_type}, is_ext={r.is_exterior}")
                exterior_rooms = [r for r in rooms_inv if r.is_exterior or r.room_type.name in ('TERRACE', 'BALCONY', 'GARDEN', 'LOGGIA')]
                # Also check for rooms with very large surfaces (>80) that are likely exterior
                # even if misclassified as interior (bug in inverted-pairs)
                for r in rooms_inv:
                    if r.surface > 80:
                        # This is definitely a terrace or similar exterior space
                        r.is_exterior = True
                        r.room_type = RoomType.TERRACE
                        exterior_rooms.append(r)
                        logger.info(f"Treating {r.name_raw} ({r.surface}) as exterior due to large surface")
                if exterior_rooms:
                    rooms = self._merge_rooms(rooms, exterior_rooms)
                    logger.info(f"  🔁 Added {len(exterior_rooms)} exterior rooms from inverted-pairs")
                    # Also update annex space
                    if inv_annex > 0:
                        spatial_data['annex_space'] = inv_annex
            
            if rooms_inv and not rooms_tb:
                # Validate coherence using two criteria (either is sufficient):
                # 1. Surface sum matches declared living_space (within 5%)
                # 2. High normalization rate (>60% of pairs produced valid room types)
                interior_sum = sum(r.surface for r in rooms_inv if not r.is_exterior)
                declared = inv_living if inv_living > 0 else (spatial_data.get('living_space') or 0)
                # Scan raw text for habitable surface if still unknown
                if declared == 0 and raw_pymupdf:
                    import re as _re2
                    for _pat in [r'Surface.{0,20}Habitable.{0,5}[\n\r]\s*(\d+[\.,]\d+)',
                                 r'Habitable\s*:\s*(\d+[\.,]\d+)']:
                        _sh = _re2.search(_pat, raw_pymupdf, _re2.IGNORECASE)
                        if _sh:
                            declared = float(_sh.group(1).replace(',', '.'))
                            break
                interior_sum = sum(r.surface for r in rooms_inv if not r.is_exterior)
                sum_coherent = (declared > 0 and interior_sum > 0
                                and abs(interior_sum - declared) / declared < 0.15)
                # Use pair_coherent when declared surface is unknown:
                # count consecutive SURF\nNAME pairs in raw text
                # INK-style PDFs have tight pairs (>= 5), B01 has scattered noise
                import re as _re3
                _PAIR_RE = _re3.compile(
                    r'\d+[\.,]\d+\s*m?[²2]?\s*\n[A-Za-zÀ-ÿ][^\n]{2,30}',
                    _re3.MULTILINE
                )
                consecutive_pairs = len(_PAIR_RE.findall(raw_pymupdf)) if raw_pymupdf else 0
                pair_coherent = (declared == 0 and consecutive_pairs >= 5)
                is_coherent = sum_coherent or pair_coherent
                if is_coherent:
                    rooms = self._merge_rooms(rooms, rooms_inv)
                    logger.info(f"  🔁 Inverted-pairs accepted: {len(rooms_inv)} pièces (consecutive_pairs={consecutive_pairs})")
                    # Only use inverted-pairs living_space if Two-block didn't find a valid declared_living
                    # Preserve declared living from Two-block (87.79) over calculated sum (84.99)
                    existing_living = spatial_data.get('living_space', 0)
                    if inv_living > 0 and (existing_living == 0 or existing_living < 50):
                        spatial_data['living_space'] = inv_living
                    if inv_annex > 0:
                        spatial_data['annex_space'] = inv_annex
                    rooms_tb = rooms_inv  # gate: skip spatial/multiline/regex
                else:
                    logger.info(f"  🔁 Inverted-pairs REJECTED (consecutive_pairs={consecutive_pairs}, interior={interior_sum:.2f}, declared={declared:.2f})")

        # Priorité 1: tableau spatial (le plus fiable) — skip if two-block succeeded
        if spatial_rows and not rooms_tb:
            self.normalizer.reset()
            rooms = self._rooms_from_table(spatial_rows, "spatial")
            logger.info(f"  ✅ {len(rooms)} pièces depuis tableau spatial")
        elif spatial_rows and rooms_tb:
            logger.info(f"  ⏭️ Spatial skipped (two-block already found {len(rooms_tb)} pièces)")

        # Priorité 1c: texte multi-lignes PyMuPDF (format NOM\nSurface\nNOM\nSurface)
        # Seulement si le two-block parser n'a rien trouvé (les deux formats sont incompatibles)
        if raw_pymupdf and not rooms_tb:
            self.normalizer.reset()
            rooms_ml = self._rooms_from_multiline_text(raw_pymupdf, "pymupdf_ml")
            rooms = self._merge_rooms(rooms, rooms_ml)
            logger.info(f"  📋 +multi-ligne PyMuPDF → {len(rooms)} pièces")

        # Priorité 2: regex sur texte PyMuPDF (si spatial+multiline insuffisant)
        # Ne pas lancer si two-block a déjà trouvé les pièces (formats incompatibles)
        spatial_calc = sum(r.surface for r in rooms)
        needs_regex = not rooms_tb and (len(rooms) < 10 or spatial_calc < 100)
        
        if needs_regex and text_data["text_pymupdf"]:
            self.normalizer.reset()
            rooms_regex = self._rooms_from_regex(
                text_data["text_pymupdf"], "pymupdf"
            )
            rooms = self._merge_rooms(rooms, rooms_regex)
            logger.info(f"  📝 +regex PyMuPDF → {len(rooms)} pièces")

        # Priorité 3: OCR tolerant pass — ALWAYS runs when OCR text available
        # (not just as fallback) because some rooms only appear in OCR text,
        # e.g. Entrée and Chambre 2 buried in noisy lines the spatial extractor can't parse
        if text_data["text_ocr"]:
            self.normalizer.reset()
            rooms_ocr, ocr_total = self._extract_rooms_from_text(text_data["text_ocr"], "ocr")
            if rooms_ocr:
                before = len(rooms)
                rooms = self._merge_rooms(rooms, rooms_ocr)
                added = len(rooms) - before
                logger.info(f"  🔍 +OCR toléré → {added} pièces ajoutées ({len(rooms)} total)")
            
            # Utiliser la surface totale OCR si spatial n'a pas trouvé de surface
            if ocr_total and ocr_total > 0 and not spatial_data.get('living_space'):
                spatial_data['living_space'] = ocr_total
                logger.info(f"  📄 Surface totale OCR: {ocr_total} m²")

        # Étape 3b: Dédoublonnage final
        rooms = self.dedup.final_dedup(rooms)

        result.rooms = rooms
        result.sources = {r.name_normalized: r.source for r in rooms}

        # ── ÉTAPE 4: Résolution composites ────────────────────
        result.rooms, result.composites = self.composite_resolver.resolve(
            result.rooms
        )
        if result.composites:
            logger.info(f"  🔗 Composites: {result.composites}")

        # ── ÉTAPE 5: Métadonnées ──────────────────────────────
        meta = self.metadata_extractor.extract(
            primary_text,
            reference_hint=reference_hint,
            spatial_metadata=spatial_data.get("metadata_lines"),
        )

        result.reference = meta.get("reference", reference_hint or "UNKNOWN")
        # Use original_reference for parcelLabel (without MAGASIN_ prefix)
        result.parcel_label = meta.get("original_reference", meta.get("reference", reference_hint or ""))
        # Reference validation: does the reference actually exist in the PDF text?
        result.reference_valid = meta.get("reference_valid", True)
        result.floor = meta.get("floor", "")
        result.building = meta.get("building", "")
        result.promoter_detected = meta.get("promoter", "")
        result.address = meta.get("address", "")
        result.program_name = meta.get("program", "")
        
        # Nouveaux champs pour maisons
        result.surface_propriete = meta.get("surface_propriete", 0.0)
        result.surface_espaces_verts = meta.get("surface_espaces_verts", 0.0)
        
        # Store multi-floor surfaces for surface detail display
        result.multi_floor_surfaces = meta.get('multi_floor_surfaces', {})
        
        # Surfaces: spatial has priority over metadata
        # Also check multi-floor surfaces (RDC + Mezzanine sum)
        multi_floor_surfaces = meta.get('multi_floor_surfaces', {})
        mezzanine_surface = multi_floor_surfaces.get('mezz', 0.0)  # Mezzanine surface if present
        multi_floor_total = sum(multi_floor_surfaces.values()) if multi_floor_surfaces else 0.0
        
        # Get the base living space from spatial or metadata
        base_living_space = (
            spatial_data.get("living_space")
            or meta.get("living_space", 0.0)
        )
        
        # Try to find LOGEMENT in raw text if base is too low
        # This ensures we use the declared total (87.79) over room sum (84.99)
        if base_living_space < 50 and raw_pymupdf:
            import re as _re_decl
            _lg_match = _re_decl.search(r'LOGEMENT\s+(\d+[,\.]\d+)', raw_pymupdf, _re_decl.IGNORECASE)
            if _lg_match:
                _declared = float(_lg_match.group(1).replace(',', '.'))
                if _declared > 50:
                    base_living_space = _declared
        
        # For multi-floor properties (RDC + MEZ), add the mezzanine surface to get the total
        # The declared living_space is typically just the RDC surface
        if mezzanine_surface > 0 and base_living_space > 0:
            # Add mezzanine to get total living space
            result.living_space = base_living_space + mezzanine_surface
        elif base_living_space > 0:
            result.living_space = base_living_space
        elif multi_floor_total > 0:
            # No base living space, use multi-floor total
            result.living_space = multi_floor_total
        else:
            result.living_space = base_living_space
            
        result.annex_space = (
            spatial_data.get("annex_space")
            or meta.get("annex_space", 0.0)
        )
        
        # Fallback: si living_space est 0, utiliser la surface calculee
        if result.living_space == 0:
            result.living_space = sum(r.surface for r in result.rooms if not r.is_exterior)
        
        # ── ÉTAPE 5b: Filtrage multi-appartement ─────────────
        # Ne pas filtrer si on est dans un contexte multi-page:
        # le filtre sera appliqué APRÈS combinaison des étages.
        if result.living_space > 0 and not is_multipage_context:
            result.rooms = self.dedup.filter_by_reference(
                result.rooms, result.reference, result.living_space
            )
            result.sources = {r.name_normalized: r.source for r in result.rooms}


        # ── ÉTAPE 6: Typology + property type ─────────────────
        # Prefer room-based detection over metadata hint (more reliable)
        room_typology = self._detect_typology(result.rooms)
        meta_typology = meta.get("typology_hint", "")
        
        # Use meta hint only if it seems reasonable (not empty, matches bedroom count)
        bedrooms = sum(1 for r in result.rooms if r.room_type == RoomType.BEDROOM)
        if meta_typology and meta_typology != room_typology:
            # Accept Commercial/Magasin type from metadata (Moroccan floor plans)
            if meta_typology.lower() in ['commercial', 'magasin', 'commerce']:
                result.typology = meta_typology
            # Check if meta hint is close to what we'd expect
            else:
                expected_from_rooms = f"T{bedrooms + 1}" if bedrooms > 0 else "Studio"
                if meta_typology == expected_from_rooms:
                    result.typology = meta_typology
                else:
                    logger.info(f"  ℹ️ Typology: meta_hint={meta_typology}, room_calc={room_typology}, using={room_typology}")
                    result.typology = room_typology
        else:
            result.typology = room_typology
        # Store property_type_hint for later use when combining results
        result.property_type_hint = meta.get("property_type_hint", "")
        result.property_type = self._detect_property_type(result.rooms, result.floor, meta.get("typology_hint", ""), meta.get("property_type_hint", ""), primary_text)

        # ── ÉTAPE 7a: Inférence chambre manquante ────────────
        # Si la surface calculée est inférieure à la surface déclarée d'exactement
        # la surface d'une chambre plausible (5-40 m²), on infère la chambre manquante.
        # Cas typique: OCR dégradé sur une cellule du tableau récapitulatif.
        result = self.inference.infer_missing_living_room(result)
        result = self.inference.infer_missing_bedroom(result)

        # ── ÉTAPE 7: Validation ───────────────────────────────
        self.validator.validate(result)

        logger.info(
            f"  ✅ {result.reference} | {result.typology} | {result.floor} | "
            f"{len(result.rooms)} pièces | "
            f"valid={len(result.validation_errors) == 0}"
        )
        return result

    # ─── Méthodes internes ────────────────────────────────

    def _rooms_from_table(self, rows, source):
        """Convertit les lignes du tableau spatial en ExtractedRoom"""
        rooms = []
        for name_raw, surface_str in rows:
            try:
                surface = float(surface_str)
            except ValueError:
                continue
            if surface < 0.5 or surface > 500:
                continue

            norm, rtype, num, ext, conf = self.normalizer.normalize(name_raw)
            if not rtype:
                logger.debug(f"Pièce non reconnue (table): '{name_raw}'")
                continue

            rooms.append(ExtractedRoom(
                name_raw=name_raw,
                name_normalized=norm,
                surface=surface,
                room_type=rtype,
                is_exterior=ext,
                room_number=num,
                source=source,
                confidence=conf,
            ))
        return rooms

    # def _rooms_from_regex(self, text, source):
    #     """Extrait les pièces par regex depuis le texte brut (fallback)"""
    #     rooms = []
    #     for pattern in self.SURFACE_PATTERNS:
    #         for match in re.finditer(pattern, text, re.IGNORECASE):
    #             name_raw = match.group(1).strip()
    #             surface_str = match.group(2)

    #             if len(name_raw) < 2 or len(name_raw) > 50:
    #                 continue
    #             try:
    #                 surface = float(surface_str.replace(",", "."))
    #             except ValueError:
    #                 continue
    #             if surface < 0.5 or surface > 500:
    #                 continue
    #             if any(kw in name_raw.upper() for kw in self.SKIP_KEYWORDS):
    #                 continue

    #             norm, rtype, num, ext, conf = self.normalizer.normalize(name_raw)
    #             if not rtype:
    #                 continue

    #             rooms.append(ExtractedRoom(
    #                 name_raw=name_raw,
    #                 name_normalized=norm,
    #                 surface=surface,
    #                 room_type=rtype,
    #                 is_exterior=ext,
    #                 room_number=num,
    #                 source=source,
    #                 confidence=conf * 0.85,  # Moins fiable que spatial
    #             ))
    #     return rooms

    # def _rooms_from_regex(self, text, source):
    #     rooms = []
    #     seen_surfaces = {}  # (room_type, surface) → déjà vu
        
    #     for pattern in self.SURFACE_PATTERNS:
    #         for match in re.finditer(pattern, text, re.IGNORECASE):
    #             name_raw = match.group(1).strip()
    #             surface_str = match.group(2)

    #             if len(name_raw) < 2 or len(name_raw) > 50:
    #                 continue
    #             try:
    #                 surface = float(surface_str.replace(",", "."))
    #             except ValueError:
    #                 continue
    #             if surface < 0.5 or surface > 500:
    #                 continue
    #             if any(kw in name_raw.upper() for kw in self.SKIP_KEYWORDS):
    #                 continue

    #             norm, rtype, num, ext, conf = self.normalizer.normalize(name_raw)
    #             if not rtype:
    #                 continue

    #             # ── Anti-doublon intra-source ──
    #             # Même type + même surface = même pièce vue 2 fois
    #             dedup_key = (rtype, round(surface, 2))
    #             if dedup_key in seen_surfaces:
    #                 logger.debug(
    #                     f"  Doublon intra-source ignoré: '{name_raw}' "
    #                     f"{surface}m² (déjà vu comme '{seen_surfaces[dedup_key]}')"
    #                 )
    #                 continue
    #             seen_surfaces[dedup_key] = name_raw

    #             rooms.append(ExtractedRoom(
    #                 name_raw=name_raw,
    #                 name_normalized=norm,
    #                 surface=surface,
    #                 room_type=rtype,
    #                 is_exterior=ext,
    #                 room_number=num,
    #                 source=source,
    #                 confidence=conf * 0.85,
    #             ))
    #     return rooms
    
    def _rooms_from_inverted_pairs(self, text: str, source: str):
        """
        Parse SURF\nNAME\nSURF\nNAME format (floor plan drawing labels).
        Some PDFs (e.g. Groupe Duval) put surface values ABOVE room names
        in the drawing, creating inverted pairs.

        Returns (rooms, living_space, annex_space)
        """
        lines = [l.strip() for l in re.split(r'[\n\r]+', text) if l.strip()]
        SURFACE_RE = re.compile(r'^(\d+[,\.]\d+)\s*m?[²2]?\s*$')
        TOTAL_KEYWORDS = ['HABITABLE', 'PRIVATIVE', 'ANNEXE', 'EXTÉRIEUR',
                          'EXTERIEUR', 'À VIVRE', 'A VIVRE']

        living_space = 0.0
        annex_space  = 0.0
        rooms        = []
        seen         = {}

        # Pre-scan for declared totals - handle both same-line and next-line formats
        # "SURFACE TOTALE HABITABLE 40.13 m²" OR "SURFACE TOTALE HABITABLE\n40.13 m²"
        HABITABLE_RE = re.compile(
            r'(?:Surface\s*(?:Totale\s*)?Habitable|SURFACE\s*(?:TOTALE\s*)?HABITABLE'
            r'|Total\s*surface\s*[àa]\s*vivre)'
            r'[^\d\n]*\n?\s*(\d+[,.]\d+)', re.IGNORECASE
        )
        ANNEXE_RE = re.compile(
            r'(?:Surface\s*(?:Totale\s*)?(?:Annexe|Ext[ée]rieure?)|'
            r'SURFACE\s*(?:TOTALE\s*)?(?:ANNEXE|EXT[ÉE]RIEURE?)|'
            r'Total\s*Ext[ée]rieurs?)'
            r'[^\d\n]*\n?\s*(\d+[,.]\d+)', re.IGNORECASE
        )
        # Collect ALL totals (cross-page contamination injects multiple values)
        all_hab = [float(m.group(1).replace(',', '.')) for m in HABITABLE_RE.finditer(text)]
        all_ann = [float(m.group(1).replace(',', '.')) for m in ANNEXE_RE.finditer(text)]
        # Use MINIMUM valid total (= current page's total, not downstream pages)
        valid_hab = [v for v in all_hab if v >= 10.0]
        valid_ann = [v for v in all_ann if v >= 1.0]
        if valid_hab:
            living_space = min(valid_hab)
        if valid_ann:
            annex_space = min(valid_ann)

        i = 0
        while i < len(lines) - 1:
            m = SURFACE_RE.match(lines[i])
            if m:
                surface_str = m.group(1).replace(',', '.')
                name_candidate = lines[i + 1]
                # name must have letters, not be another surface, not be a number code
                if (re.search(r'[A-Za-zÀ-ÿ]{2,}', name_candidate)
                        and not SURFACE_RE.match(name_candidate)
                        and not re.match(r'^\d+$', name_candidate)):
                    try:
                        surface = float(surface_str)
                    except ValueError:
                        i += 1
                        continue
                    if not (0.5 <= surface <= 500):
                        i += 1
                        continue

                    # Check if this is a total line
                    name_up = name_candidate.upper()
                    if any(kw in name_up for kw in TOTAL_KEYWORDS):
                        if 'HABITABLE' in name_up and living_space == 0:
                            living_space = surface
                        elif any(k in name_up for k in ['EXTÉRIEUR','EXTERIEUR']) and annex_space == 0:
                            annex_space = surface
                        i += 2
                        continue

                    norm, rtype, num, ext, conf = self.normalizer.normalize(name_candidate)
                    if rtype:
                        key = (rtype, round(surface, 2))
                        if key not in seen:
                            seen[key] = True
                            rooms.append(ExtractedRoom(
                                name_raw=name_candidate,
                                name_normalized=norm,
                                surface=surface,
                                room_type=rtype,
                                is_exterior=ext,
                                room_number=num,
                                source=source,
                                confidence=conf,
                            ))
                    i += 2
                    continue
            i += 1

        # ── Post-processing: trim cross-page contamination ──────────────────
        # If we have more interior rooms than expected (duplicate room types),
        # find the FIRST contiguous subset whose sum ≈ living_space.
        if living_space > 0 and rooms:
            interior = [r for r in rooms if not r.is_exterior]
            exterior = [r for r in rooms if r.is_exterior]
            interior_sum = sum(r.surface for r in interior)
            diff_pct = abs(interior_sum - living_space) / living_space if living_space > 0 else 1

            if diff_pct > 0.10:
                # Too much deviation → try to find a prefix of interior rooms that matches
                cumsum = 0.0
                best_idx = len(interior)
                for idx_r, r in enumerate(interior):
                    cumsum = round(cumsum + r.surface, 2)
                    if abs(cumsum - living_space) / living_space < 0.08:
                        best_idx = idx_r + 1
                        break
                if best_idx < len(interior):
                    logger.info(
                        f"  ✂️ Inverted-pairs: trimmed {len(interior) - best_idx} cross-page rooms "
                        f"(sum={cumsum:.2f} ≈ declared={living_space:.2f})"
                    )
                    interior = interior[:best_idx]
                    rooms = interior + exterior

        logger.info(f"  🔁 Inverted-pairs: {len(rooms)} pièces, living={living_space}")
        return rooms, living_space, annex_space

    def _rooms_from_two_block_text(self, text: str, source: str, declared_living: float = 0.0):
        """
        Parse 'two-block' format from vector PDFs:
        all room names in one column, all surfaces in another.
        PyMuPDF produces: NAME\nNAME\n...\nSURF\nSURF\n...

        Key heuristic: only match a surface run to its preceding name block
        when the count of valid names ≈ count of surfaces (±2).
        This prevents leaked floor-plan annotations from being mismatched.

        Returns (rooms, living_space, annex_space)
        
        Args:
            text: The raw text to parse
            source: The source identifier (e.g., "pymupdf_tb")
            declared_living: Pre-declared living space from LOGEMENT (if > 50), used for filtering
        """
        lines = [l.strip() for l in re.split(r'[\n\r]+', text) if l.strip()]

        # DEBUG VERSION CHECK
        # import sys as _sys
        # print(f"[DEBUG] two_block parser running, lines={len(lines)}, version=2026-02-27-v2", file=_sys.stderr)
        # # Print ALL lines for diagnosis
        # for _i, _l in enumerate(lines):
        #     print(f"[DEBUG]   line[{_i:03d}] {_l!r}", file=_sys.stderr)

        # ── Pre-processing: remove "surfaces indicatives inf. à 1,8m Ht" sub-table ──
        # This sub-table (low-ceiling areas) appears BEFORE the main habitable table.
        # In two-block format, PyMuPDF reads:
        #   [sub-table names block] then [sub-table surfaces block] later
        # We must remove BOTH the names AND the surfaces of the sub-table.
        # Strategy:
        #   1. Count names stripped (N_names)
        #   2. Also strip the next N_names+1 surface-only lines after the footer
        SUBTABLE_HEADER_RE = re.compile(r'surfaces?\s+indicatives?', re.IGNORECASE)
        SUBTABLE_FOOTER_RE = re.compile(r'Surface\s+totale\b(?!\s+habitable)(?!\s+privative)', re.IGNORECASE)
        SURFACE_ONLY_RE = re.compile(r'^\d+[,.]\d+\s*(?:m[²2²]?)?\s*$')
        
        # Pass 1: strip names block, count how many names were in sub-table
        cleaned_lines = []
        in_subtable = False
        n_subtable_names = 0
        for line in lines:
            if SUBTABLE_HEADER_RE.search(line):
                in_subtable = True
                continue
            if in_subtable:
                if SUBTABLE_FOOTER_RE.search(line):
                    in_subtable = False
                    # Footer itself is skipped
                else:
                    # Count non-surface lines as "names" to know how many values to skip
                    if not SURFACE_ONLY_RE.match(line):
                        n_subtable_names += 1
                continue  # skip all sub-table content
            cleaned_lines.append(line)
        lines = cleaned_lines
        
        # Pass 2: if we stripped N names, also skip the next N+1 surface-only lines
        # (N surfaces + 1 total value like "12,67 m²")
        if n_subtable_names > 0:
            to_skip = n_subtable_names + 1
            final_lines = []
            skipped = 0
            for line in lines:
                if skipped < to_skip and SURFACE_ONLY_RE.match(line):
                    skipped += 1
                    continue
                final_lines.append(line)
            lines = final_lines

        SURFACE_RE = re.compile(r'^\d+[,\.]\d+\s*(?:m[²2²]?)?\s*$')
        TOTAL_KEYWORDS = [
            'SURFACE TOTALE HABITABLE', 'TOTAL SURFACE HABITABLE', 'TOTAL SH',
            'SURFACE HABITABLE', 'SURFACE PRIVATIVE', 'SURFACE ANNEXE',
            'TOTAL ANNEXE', 'TOTAL EXTERIEURS', 'HABITABLE :',
            'EXTÉRIEUR :', 'EXTERIEUR :',
            'TOTAL SURFACE À VIVRE', 'TOTAL SURFACE A VIVRE',
        ]
        # Sub-table totals to SKIP (low-ceiling annotations, not habitable surface)
        SUBTABLE_SKIP = [
            'SURFACE TOTALE',  # generic total used by "Surface totale: 12.67" sub-tables
        ]
        SUBTABLE_LABEL = 'INDICATIVE'  # lines containing this word precede sub-tables
        NOISE_RE = re.compile(
            r'^(PIECES|SURFACES|LEGENDE|TYPE|PLAN|DATE|IND|ECHELLE|BATIMENT|' 
            r'LOGEMENT|ETAGE|N°|PP\d+|OB|VR|PF|[A-Z]{1,3}\d{2,}|' 
            r'\d{2,}|\d+\s+\d+)$|^\d[\d\s]{3,}$'
            r'|^Niv\.\s|^hors\s+surfaces|^N[°\u2510\u2591-\u2593]',
            re.IGNORECASE
        )

        def is_surface(line):
            return bool(SURFACE_RE.match(line))

        def is_total(line):
            lu = line.upper()
            # "Surface totale habitable" = real total → handle as total
            if any(kw in lu for kw in TOTAL_KEYWORDS):
                return True
            # Standalone "Total" row (bold footer without full keyword label)
            if re.match(r'^TOTAL\s*$', lu):
                return True
            # "Surface totale" alone (sub-table like "surfaces indicatives") → treat as noise/skip
            if 'SURFACE TOTALE' in lu and 'HABITABLE' not in lu and 'PRIVATIVE' not in lu:
                return 'subtable'  # truthy but flagged
            return False

        def is_noise(line):
            return bool(NOISE_RE.match(line))

        def is_valid_name(line):
            return (not is_noise(line) and not is_surface(line)
                    and bool(re.search(r'[A-Za-zÀ-ÿ]{2,}', line)))

        def parse_value(s):
            return float(s.replace(',', '.').replace('m²', '')
                          .replace('m2', '').strip())

        # ═══════════════════════════════════════════════════════════════════════
        # NEW: Parse "Surfaces des annexes" and "Surfaces habitables" sections
        # These sections have alternating name/surface pattern
        # ═══════════════════════════════════════════════════════════════════════
        HABITABLE_SECTION_RE = re.compile(r'^Surfaces?\s+habitables?$', re.IGNORECASE)
        ANNEXE_SECTION_RE = re.compile(r'^Surfaces?\s+(?:des\s+)?annexes?$', re.IGNORECASE)
        
        def _parse_section_alternating(start_idx, section_type):
            """Parse alternating name/surface pattern from a section header."""
            import sys as _sys
            rooms = []
            total = 0.0
            annex = 0.0
            
            # Walk through lines after section header, collecting names then surfaces.
            # Also track how many total-keyword names appear before/after room names —
            # in two-block format a filtered total name still has a corresponding surface
            # value that needs to be skipped to keep names↔surfaces aligned.
            names = []
            surfaces = []
            totals_before = 0   # total-keyword lines seen before first room name
            totals_after = 0    # total-keyword lines seen after first room name
            found_first_name = False
            j = start_idx + 1
            while j < len(lines):
                line = lines[j]
                # Stop at a section header — but only once we have already collected
                # at least one room name.  Before any names, a header like
                # "Surface habitable" is a reference-total row (its corresponding
                # surface value in the surfaces block must be skipped), not a
                # genuine new section boundary.
                if HABITABLE_SECTION_RE.match(line) or ANNEXE_SECTION_RE.match(line):
                    if found_first_name:
                        break  # Genuine new section after room names → stop
                    else:
                        # Reference total before any room names → count and continue
                        totals_before += 1
                        j += 1
                        continue
                if is_surface(line):
                    surfaces.append(line)
                elif is_total(line):
                    if found_first_name:
                        totals_after += 1
                    else:
                        totals_before += 1
                elif not is_noise(line) and bool(re.search(r'[A-Za-zÀ-ÿ]{2,}', line)):
                    found_first_name = True
                    names.append(line)
                j += 1

            # DEBUG
            print(f"[DBG _parse_section_alternating] type={section_type} start={start_idx}", file=_sys.stderr)
            print(f"[DBG]   names={names}", file=_sys.stderr)
            print(f"[DBG]   surfaces={surfaces}", file=_sys.stderr)
            print(f"[DBG]   totals_before={totals_before} totals_after={totals_after}", file=_sys.stderr)
            # DEBUG END

            # Re-align surfaces with names by trimming the orphaned total surface(s).
            if totals_before > 0 and len(surfaces) > totals_before:
                logger.debug(
                    f"  🗑️ Section annexe: {totals_before} surface(s) de total "
                    f"retirée(s) au début (totaux avant noms)"
                )
                surfaces = surfaces[totals_before:]
            if totals_after > 0 and len(surfaces) > totals_after:
                logger.debug(
                    f"  🗑️ Section annexe: {totals_after} surface(s) de total "
                    f"retirée(s) à la fin (totaux après noms)"
                )
                surfaces = surfaces[:-totals_after]

            # Fallback: if still mismatched and declared_living is known, remove
            # a surface value matching it (grand-total footer without a name label).
            if len(surfaces) > len(names):
                ref_val = declared_living if declared_living > 40 else 0
                if ref_val > 0:
                    for _sl in list(surfaces):
                        try:
                            if abs(parse_value(_sl) - ref_val) <= 1.0:
                                surfaces.remove(_sl)
                                logger.debug(
                                    f"  🗑️ Section annexe: valeur totale parasite "
                                    f"({parse_value(_sl)}) retirée (ref={ref_val})"
                                )
                                break
                        except (ValueError, AttributeError):
                            pass

            # Pair names with surfaces (minimum of the two)
            n_pairs = min(len(names), len(surfaces))
            for k in range(n_pairs):
                name = names[k]
                surf_str = surfaces[k]
                try:
                    surface = parse_value(surf_str)
                except (ValueError, AttributeError):
                    continue

                if surface < 0.5 or surface > 500:
                    continue

                # Determine room type via normalizer (handles all cases correctly)
                norm, rtype, num, ext, conf = self.normalizer.normalize(name)
                if not rtype:
                    logger.debug(f"Pièce non reconnue ({section_type}): '{name}'")
                    continue
                is_ext = ext

                # If exterior, add to annex space
                if is_ext:
                    annex = max(annex, surface)

                rooms.append(ExtractedRoom(
                    name_raw=name, name_normalized=norm, surface=surface,
                    room_type=rtype, is_exterior=is_ext, room_number=num,
                    source=source, confidence=conf,
                ))
            
            return rooms, total, annex
        
        # Try to find and parse "Surfaces des annexes" section first
        for idx, line in enumerate(lines):
            if ANNEXE_SECTION_RE.match(line):
                logger.debug(f"Found 'Surfaces des annexes' at index {idx}, parsing...")
                annex_rooms, annex_total, annex_space = _parse_section_alternating(idx, 'annexe')
                # Now look for habitable section after annexe
                for idx2 in range(idx + 1, len(lines)):
                    if HABITABLE_SECTION_RE.match(lines[idx2]):
                        logger.debug(f"Found 'Surfaces habitables' at index {idx2}, parsing...")
                        hab_rooms, hab_total, _ = _parse_section_alternating(idx2, 'habitable')
                        # Combine rooms (annexe first, then habitable)
                        all_rooms = annex_rooms + hab_rooms
                        living = max(hab_total, declared_living) if hab_total > 0 else declared_living
                        if living > 0:
                            logger.debug(f"  → Extracted {len(all_rooms)} rooms from annexe+habitable sections")
                            return all_rooms, living, annex_space
                # If we only found annexe section
                if annex_rooms:
                    logger.debug(f"  → Extracted {len(annex_rooms)} rooms from annexe section only")
                    return annex_rooms, annex_total, annex_space
        
        # Try to find and parse "Surfaces habitables" section
        for idx, line in enumerate(lines):
            if HABITABLE_SECTION_RE.match(line):
                logger.debug(f"Found 'Surfaces habitables' at index {idx}, parsing...")
                rooms, living, annex = _parse_section_alternating(idx, 'habitable')
                if rooms:
                    logger.debug(f"  → Extracted {len(rooms)} rooms from habitable section")
                    return rooms, living, annex

        surface_indices = [i for i, l in enumerate(lines) if is_surface(l)]
        if len(surface_indices) < 3:
            return [], 0.0, 0.0

        runs, cur = [], [surface_indices[0]]
        for idx in surface_indices[1:]:
            if idx == cur[-1] + 1:
                cur.append(idx)
            else:
                runs.append(cur); cur = [idx]
        runs.append(cur)
        # Keep runs with 1+ surfaces (was >=3, but exterior spaces may have only 1-2)
        runs = [r for r in runs if len(r) >= 1]
        if not runs:
            return [], 0.0, 0.0

        living_space = 0.0
        annex_space  = 0.0
        all_rooms    = []
        runs_data    = []  # (run_total, rooms_from_this_run)

        for run_idx, run in enumerate(runs):
            surf_start = run[0]
            surf_lines  = lines[surf_start:run[-1]+1]
            prev_end    = runs[run_idx-1][-1] + 1 if run_idx > 0 else 0
            all_candidates = lines[prev_end:surf_start]

            # ── Use the LAST CONTIGUOUS NAME BLOCK before the surface run ──
            # Walking backwards from surf_start, collect valid-name lines in the
            # last tight block (allow at most 2 noise/blank lines as gap).
            # This ignores legend items and floor-plan labels far from the table.
            block = []
            gap = 0
            for line in reversed(all_candidates):
                if is_total(line):
                    block.insert(0, line)
                    gap = 0
                elif is_valid_name(line):
                    block.insert(0, line)
                    gap = 0
                elif is_surface(line):
                    break  # hit previous surface run → stop
                else:
                    gap += 1
                    if gap > 2:
                        break  # too many noise lines → stop looking further back
            candidates = block

            total_names = [l for l in candidates if is_total(l) and is_total(l) != 'subtable']
            room_names_raw = [l for l in candidates if is_valid_name(l) and not is_total(l)]
            # Deduplicate names (floor-plan labels may duplicate table names)
            seen_names = set()
            room_names = []
            for n in room_names_raw:
                key = n.strip().upper()
                if key not in seen_names:
                    seen_names.add(key)
                    room_names.append(n)
            # Build ordered list preserving original order, deduplicated
            seen_ord = set()
            ordered = []
            for l in candidates:
                key = l.strip().upper()
                if is_total(l):
                    ordered.append(l)
                elif is_valid_name(l) and not is_total(l) and key not in seen_ord:
                    seen_ord.add(key)
                    ordered.append(l)

            # Process the run: pair names with surfaces.
            # The run may contain habitable section + annexe section separated by totals.
            # Strategy: walk ordered names + surf_lines together.
            # Accept the run if at least one section has name_count ≈ surface_count.

            def _process_ordered(ordered_list, surf_lines_list):
                """Walk ordered list and surf_lines together, extracting rooms and totals."""
                nonlocal living_space, annex_space
                surf_iter = iter(surf_lines_list)
                for name in ordered_list:
                    try:
                        surface = parse_value(next(surf_iter))
                    except (StopIteration, ValueError):
                        break
                    total_flag = is_total(name)
                    if total_flag:
                        if total_flag != 'subtable':
                            nu = name.upper()
                            if 'HABITABLE' in nu and living_space == 0:
                                living_space = surface
                            elif any(k in nu for k in ['EXTÉRIEUR','EXTERIEUR',
                                                        'PRIVATIVE','ANNEXE']) and annex_space == 0:
                                annex_space = surface
                        continue
                    if len(name) < 2 or surface < 0.5 or surface > 500:
                        continue
                    norm, rtype, num, ext, conf = self.normalizer.normalize(name)
                    if not rtype:
                        logger.debug(f"Pièce non reconnue (two-block): '{name}'")
                        continue
                    all_rooms.append(ExtractedRoom(
                        name_raw=name, name_normalized=norm, surface=surface,
                        room_type=rtype, is_exterior=ext, room_number=num,
                        source=source, confidence=conf,
                    ))

            expected = len(total_names) + len(room_names)
            count_diff = abs(expected - len(surf_lines))

            # Detect run's declared total (last surf_line containing 'm')
            run_total_val = 0.0
            for sl in reversed(surf_lines):
                if 'm' in sl.lower():
                    try:
                        run_total_val = parse_value(sl)
                        break
                    except (ValueError, AttributeError):
                        pass

            rooms_before_run = len(all_rooms)

            if count_diff <= 2:
                # When there is exactly one surplus surface, it may be a grand-total
                # footer that leaked into the run without a corresponding name entry
                # (e.g. "114.46 m²" total appearing before terrasse surfaces).
                # Detect and remove it so the remaining surfaces align with room names.
                if count_diff == 1:
                    ref = declared_living if declared_living > 40 else living_space
                    if ref > 0:
                        surf_lines_clean = []
                        total_removed = False
                        for _sl in surf_lines:
                            try:
                                _sv = parse_value(_sl)
                                if not total_removed and abs(_sv - ref) <= 1.0:
                                    total_removed = True
                                    logger.debug(
                                        f"  🗑️ Suppression valeur totale parasite "
                                        f"({_sv}) des surf_lines (ref={ref})"
                                    )
                                    continue
                            except (ValueError, AttributeError):
                                pass
                            surf_lines_clean.append(_sl)
                        if total_removed:
                            surf_lines = surf_lines_clean

                # Perfect match: process directly
                _process_ordered(ordered, surf_lines)
            else:
                # Mismatch: the run may have multiple sections (habitable + annexe).
                # Split candidates into sections at total-keyword boundaries,
                # match each section's names to a slice of surf_lines.
                sections = []
                cur_section = []
                for name in ordered:
                    cur_section.append(name)
                    if is_total(name) and is_total(name) != 'subtable':
                        sections.append(cur_section)
                        cur_section = []
                if cur_section:
                    sections.append(cur_section)

                if len(sections) > 1:
                    # Try to pair each section with a slice of surf_lines
                    surf_pos = 0
                    matched = False
                    for section in sections:
                        n_items = len(section)
                        if surf_pos + n_items <= len(surf_lines):
                            slice_ = surf_lines[surf_pos:surf_pos + n_items]
                            _process_ordered(section, slice_)
                            surf_pos += n_items
                            matched = True
                    if matched:
                        pass  # done
                    else:
                        # Last resort: process all together ignoring count check
                        _process_ordered(ordered, surf_lines)
                else:
                    # Single section but count mismatch: try anyway if totals present
                    if total_names:
                        _process_ordered(ordered, surf_lines)

            # Track rooms from this run for sub-table filtering
            runs_data.append((run_total_val, all_rooms[rooms_before_run:]))

        # ── Post-processing: remove sub-table runs ──────────────────────────
        # If living_space was not set (total keyword not near the surface run),
        # infer it from the largest run's declared total.
        # BUT: exclude exterior runs (jardin, porche) from living_space calculation
        # as they have small surfaces and would be incorrectly picked up.
        # ALSO: ignore runs with 0 rooms - these are likely grand totals, not actual runs.
        run_totals = []
        for rt, rooms_in_run in runs_data:
            if rt > 0 and len(rooms_in_run) > 0:
                # Check if this run contains exterior rooms
                has_exterior = any(r.is_exterior for r in rooms_in_run if hasattr(r, 'is_exterior'))
                if not has_exterior:
                    run_totals.append(rt)
        
        if living_space == 0.0 and run_totals:
            living_space = max(run_totals)
            logger.info(f"  ℹ️ living_space inferred from largest run: {living_space:.2f}")
        if annex_space == 0.0 and len(run_totals) >= 2:
            sorted_totals = sorted(run_totals, reverse=True)
            annex_space = sorted_totals[1]
            logger.info(f"  ℹ️ annex_space inferred from 2nd run: {annex_space:.2f}")

        # Use declared_living if provided (from LOGEMENT pre-scan), otherwise use inferred
        # Lower threshold to >40 to handle smaller apartments (T2 can have 47m²)
        filter_living_space = declared_living if declared_living > 40 else living_space
        
        # Filter out sub-table runs (total << living_space)
        # Only filter multi-room runs (cumulative sub-tables), keep all single-room runs
        if filter_living_space > 0 and runs_data:
            filtered = []
            for rt, rrooms in runs_data:
                # Single-room runs are always valid (individual rooms)
                # Only filter multi-room runs that look like sub-tables
                is_single_room = len(rrooms) == 1
                
                keep = (
                    is_single_room  # always keep single rooms (individual pieces)
                    or rt == 0.0  # no declared total → keep
                    or abs(rt - filter_living_space) < 1.0
                    or abs(rt - annex_space) < 1.0
                )
                if keep:
                    filtered.extend(rrooms)
                else:
                    logger.info(
                        f"  🗑️ Sub-table skipped: total={rt:.2f} "
                        f"vs living={filter_living_space:.2f}, dropped {len(rrooms)} rooms"
                    )
            all_rooms = filtered

        # Deduplicate by (room_type, room_number), keeping the room with the largest surface.
        # The normalizer persists across runs so a room seen twice gets a _2 suffix the second
        # time; the second occurrence usually has a smaller, wrong surface (floor-plan label).
        dedup_by_type: dict = {}
        for r in all_rooms:
            key = (r.room_type, r.room_number)
            if key not in dedup_by_type or r.surface > dedup_by_type[key].surface:
                dedup_by_type[key] = r
        all_rooms = list(dedup_by_type.values())

        logger.info(f"  📦 Two-block: {len(all_rooms)} pièces, living={filter_living_space}")
        return all_rooms, filter_living_space, annex_space

    def _rooms_from_multiline_text(self, text, source):
        """
        Parse le format 'NomPièce\\nSurface\\nNomPièce\\nSurface' des tableaux PDF.

        Dans M011.pdf (et similaires), le tableau récap extrait par PyMuPDF ressemble à:
            Bains\n5,92m2\nWc\n1,60m2\nPlacard\n0,83m2\n...

        Ce format n'est pas capturé par _rooms_from_regex (qui travaille sur le texte
        nettoyé où les \\n sont remplacés par des espaces).
        """
        rooms = []
        seen = {}

        # Découper sur les fins de ligne (garder le \\n original)
        lines = [l.strip() for l in re.split(r'[\n\r]+', text) if l.strip()]

        SURFACE_RE = re.compile(
            r'^[\(\[]?U?\s*(\d+[\.,]\d+)\s*m[²2]?\s*[\)\]]?$', re.IGNORECASE
        )
        SKIP = {"TOTAL", "SURFACE HABITABLE", "SURFACE ANNEXE",
                "PLAN", "DATE", "IND", "ECHELLE", "LOT", "N°"}

        i = 0
        while i < len(lines) - 1:
            name_candidate = lines[i]
            surface_candidate = lines[i + 1]

            # Vérifier si la ligne suivante est une surface
            m_surf = SURFACE_RE.match(surface_candidate)
            if m_surf:
                name_raw = name_candidate
                surface_str = m_surf.group(1).replace(",", ".")

                # Filtrer les noms trop longs, numeriques, ou mots-cles de skip
                if (len(name_raw) >= 2 and len(name_raw) <= 50
                        and not re.match(r'^\d+', name_raw)
                        and not any(kw in name_raw.upper() for kw in SKIP)):

                    try:
                        surface = float(surface_str)
                    except ValueError:
                        i += 1
                        continue

                    if 0.5 <= surface <= 500:
                        name_clean = self._clean_room_name(name_raw)
                        if len(name_clean) >= 2:
                            norm, rtype, num, ext, conf = self.normalizer.normalize(name_clean)
                            if rtype:
                                dedup_key = (rtype, round(surface, 2))
                                if dedup_key not in seen:
                                    seen[dedup_key] = name_raw
                                    rooms.append(ExtractedRoom(
                                        name_raw=name_raw,
                                        name_normalized=norm,
                                        surface=surface,
                                        room_type=rtype,
                                        is_exterior=ext,
                                        room_number=num,
                                        source=source,
                                        confidence=conf * 0.90,
                                    ))
                                    logger.debug(f"  📋 Multi-ligne: '{name_raw}' → {norm} ({surface}m²)")
                i += 2  # Avancer de 2 (nom + surface consommés)
            else:
                i += 1  # Pas de surface après ce nom, avancer

        logger.info(f"  📋 Multi-ligne: {len(rooms)} pièces trouvées")
        return rooms

    def _rooms_from_regex(self, text, source):

        rooms = []
        seen_surfaces = {}

        for pattern in self.SURFACE_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                name_raw = match.group(1).strip()
                surface_str = match.group(2)

                if len(name_raw) < 2 or len(name_raw) > 50:
                    continue
                try:
                    surface = float(surface_str.replace(",", "."))
                except ValueError:
                    continue
                if surface < 0.5 or surface > 500:
                    continue
                if any(kw in name_raw.upper() for kw in self.SKIP_KEYWORDS):
                    continue

                # ── Nettoyage du nom brut ──
                name_raw = self._clean_room_name(name_raw)
                if len(name_raw) < 2:
                    continue

                norm, rtype, num, ext, conf = self.normalizer.normalize(name_raw)
                if not rtype:
                    continue

                dedup_key = (rtype, round(surface, 2))
                if dedup_key in seen_surfaces:
                    continue
                seen_surfaces[dedup_key] = name_raw

                rooms.append(ExtractedRoom(
                    name_raw=name_raw,
                    name_normalized=norm,
                    surface=surface,
                    room_type=rtype,
                    is_exterior=ext,
                    room_number=num,
                    source=source,
                    confidence=conf * 0.85,
                ))
        return rooms

    def _extract_rooms_from_text(self, text: str, source: str) -> List['ExtractedRoom']:
        """
        Version tolérante aux erreurs d'OCR.
        Nettoie d'abord le texte OCR avant d'appliquer les patterns.
        """
        # Mapping des noms normalisés vers les RoomTypes
        ROOM_TYPE_MAPPING = {
            'entree': 'ENTRY',
            'sejour': 'LIVING_ROOM',
            'cuisine': 'KITCHEN',
            'sejour_cuisine': 'LIVING_KITCHEN',
            'circulation': 'CIRCULATION',
            'storage': 'STORAGE',
            'dressing': 'DRESSING',
            'salle_de_bain': 'BATHROOM',
            'salle_d_eau': 'SHOWER_ROOM',
            'salle_d_eau_wc': 'SHOWER_ROOM',
            'wc': 'WC',
            'balcon': 'BALCONY',
            'terrasse': 'TERRACE',
            'jardin': 'GARDEN',
            'loggia': 'LOGGIA',
            'patio': 'PATIO',
            'parking': 'PARKING',
            'cave': 'CELLAR',
        }
        # Toutes les chambres numérotées (1..9) → BEDROOM
        for n in range(1, 10):
            ROOM_TYPE_MAPPING[f'chambre_{n}'] = 'BEDROOM'
        ROOM_TYPE_MAPPING['chambre'] = 'BEDROOM'
        
        # RoomTypes extérieurs (ne comptent pas dans habitable)
        EXTERIOR_ROOM_TYPES = {'GARDEN', 'BALCONY', 'TERRACE', 'LOGGIA', 'PATIO', 'PARKING', 'CELLAR'}
        
        # Nettoyage OCR
        text = self._clean_ocr_text(text)
        
        rooms = []
        
        # Patterns spécifiques pour OCR dégradé
        # Note: Chambre N est géré dynamiquement plus bas (générique, N=1..9)
        ocr_tolerant_patterns = [
            # Entrée + Pl. — [^\d]{0,20} tolerates OCR noise like "' + PI." between name and surface
            (r"Entr(?:é|e|è)e?[^\d]{0,20}(\d+[\.,]\d+)", 'entree'),
            # SDE + WC (avec variantes: /|+)
            (r'SDE\s*[/+]\s*WC\s*(\d+[\.,]\d+)', 'salle_d_eau_wc'),
            # Séjour / Cuisine + Pl. (variantes: /|+, avec ou sans Pl.)
            (r'S[ée]jour\s*[/+]\s*Cuisine(?:\s*\+\s*Pl?\.?)?\s*[^\d]*(\d+[\.,]\d+)', 'sejour_cuisine'),
            # SDB + WC (avec variantes: /|+, sDB OCR variant)
            (r'[Ss][Dd][Bb]\s*[/+]\s*WC[^\d]*(\d+[\.,]\d+)', 'salle_de_bain'),
            # Dgt. + Pl. (variantes OCR: Dot/Dgt/Dat, PI/Pl)
            (r'D[oOgGaA][tT]\.?\s*\+\s*P[lLiI]\.?\s*(\d+[\.,]\d+)', 'circulation'),
            # Jardin
            (r'Jardin[^\d]*(\d+[\.,]\d+)', 'jardin'),
        ]
        
        # Patterns génériques pour Chambre N (N=1..9) + Pl. optionnel
        # Gère: "Chambre 1 + Pl. 12.26", "Chambre 2 10.71", "Chambre 3 + Pl. 9.50"
        for n in range(1, 10):
            # Standard: with decimal point
            ocr_tolerant_patterns.append((
                rf'(?:Chambre|Ch\.?)\s*{n}(?:\s*\+\s*Pl?\.?)?\s*[^\d]{{0,5}}(\d+[\.,]\d+)',
                f'chambre_{n}'
            ))
            # OCR dropped decimal: "Chambre 2 1071" → 10.71 (4-digit integer)
            ocr_tolerant_patterns.append((
                rf'(?:Chambre|Ch\.?)\s*{n}(?:\s*\+\s*Pl?\.?)?\s+([1-9]\d{{3}})(?:\s|m|$)',
                f'chambre_{n}_nodecimal'
            ))
        
        seen_surfaces = {}
        
        for pattern, room_type in ocr_tolerant_patterns:
            matches = re.finditer(pattern, text, re.IGNORECASE)
            for match in matches:
                try:
                    # Extraire le nombre brut
                    raw_num = match.group(1)
                    
                    # Gérer les nombres sans décimale (ex: "1071" -> 10.71)
                    if ',' not in raw_num and '.' not in raw_num and len(raw_num) == 4:
                        try:
                            surface = float(raw_num[:2] + '.' + raw_num[2:])
                        except:
                            surface = float(raw_num)
                    else:
                        surface = float(raw_num.replace(',', '.'))
                    
                    # Filtrer les surfaces aberrantes
                    if surface < 0.5 or surface > 500:
                        continue
                    
                    # Strip _nodecimal suffix before mapping (used for integer-surface variants)
                    room_type_key = room_type.replace('_nodecimal', '')
                    # Mapper vers le type de pièce correct
                    enum_name = ROOM_TYPE_MAPPING.get(room_type_key, room_type_key.upper())
                    room_type_enum = getattr(RoomType, enum_name, None)
                    
                    if room_type_enum is None:
                        continue
                    
                    # Déterminer si c'est une pièce extérieure
                    is_exterior = enum_name in EXTERIOR_ROOM_TYPES
                    
                    dedup_key = (room_type_key, round(surface, 2))
                    if dedup_key in seen_surfaces:
                        continue
                    seen_surfaces[dedup_key] = match.group(0)
                    
                    rooms.append(ExtractedRoom(
                        name_raw=match.group(0),
                        name_normalized=room_type_key,
                        surface=surface,
                        room_type=room_type_enum,
                        is_exterior=is_exterior,
                        source=source,
                        confidence=0.7,
                    ))
                except (ValueError, AttributeError):
                    pass
        
        # Extraire la surface habitable totale depuis le texte OCR
        # Pattern: "SURFACE HABITABLE TOTALE 63.00 m" ou similaire
        total_pattern = r'SURFACE\s*HABITABLE\s*TOTALE\s*(\d+[\.,]\d+)'
        total_match = re.search(total_pattern, text, re.IGNORECASE)
        total_surface = None
        if total_match:
            try:
                total_surface = float(total_match.group(1).replace(',', '.'))
            except ValueError:
                pass
        
        return rooms, total_surface

    def _clean_ocr_text(self, text: str) -> str:
        """
        Nettoie les erreurs OCR courantes avant extraction.
        
        Transformations:
        - 'm?' -> 'm²' (m² mal reconnu)
        - 'PI.' -> 'Pl.' (P minuscule -> P majuscule)
        - 'chamerez' -> 'Chambre'
        - 'Stjour' -> 'Séjour'
        - Supprime caractères parasites: |, }, =, \
        """
        if not text:
            return text
        
        # Remplacements courants d'erreurs OCR
        text = text.replace('m?', 'm²')
        text = text.replace('PI.', 'Pl.')
        text = text.replace('Stjour', 'Séjour')
        text = text.replace('stjour', 'séjour')
        text = text.replace('chamerez', 'Chambre')
        text = text.replace('SDE+wC', 'SDE WC')
        text = text.replace('SDEwC', 'SDE WC')
        text = text.replace("'", "'")  # apostrophe curly -> droit
        text = text.replace("'", "'")  # otro apostrophe
        text = text.replace('D / } =+', '')
        
        # Supprime caractères parasites OCR
        text = re.sub(r'[|{}\\]', ' ', text)
        text = re.sub(r'\s+', ' ', text)
        
        return text
    def _merge_rooms(self, primary, secondary):
        """
        Fusionne deux listes. Primary a priorité.
        Détecte les doublons par (room_type, room_number) en plus du nom.
        """
        merged = {r.name_normalized: r for r in primary}

        # Index secondaire par (type, number) pour détecter les vrais doublons
        type_index = {}
        for r in primary:
            key = (r.room_type, r.room_number)
            type_index[key] = r

        for r in secondary:
            # Check 1: même nom normalisé
            if r.name_normalized in merged:
                existing = merged[r.name_normalized]
                if r.confidence > existing.confidence:
                    merged[r.name_normalized] = r
                continue

            # Check 2: même (type, number) = vrai doublon avec nom différent
            # Primary list always wins — _2 suffix variants must not be added.
            type_key = (r.room_type, r.room_number)
            if type_key in type_index:
                logger.debug(
                    f"  Doublon SKIP (type+num): '{r.name_raw}' ({r.surface}m²) "
                    f"vs '{type_index[type_key].name_raw}' ({type_index[type_key].surface}m²)"
                )
                continue

            merged[r.name_normalized] = r
            type_index[(r.room_type, r.room_number)] = r

        return list(merged.values())
    def _clean_room_name(self, name: str) -> str:
        """
        Nettoie le nom brut en supprimant le bruit technique des plans.
        'PP80 PP80 ENTREE' → 'ENTREE'
        'A ENTREE' → 'ENTREE'
        """
        # Supprimer les codes techniques courants
        noise_patterns = [
            r"\bPP\d+\b",          # PP80, PP90
            r"\bPF\w*\b",          # PFOB, PFC
            r"\bVR\b",             # Volet roulant
            r"\bOB\b",             # Oscillo-battant
            r"\bFAV\b",            # Fenêtre
            r"\bRGT\b",            # Rangement (contexte légende)
            r"\b\d{2,3}\s*x\s*\d{2,3}\b",  # Dimensions: 90 x 220
            r"\bfixe\b",
            r"\bOPALIN\b",
            r"\bgarde[\-\s]?corps\b",
            r"\bballon\s*thermo\b",
        ]
        
        cleaned = name
        for pattern in noise_patterns:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        
        # Supprimer les lettres isolées en début (ex: "A ENTREE" → "ENTREE")
        cleaned = re.sub(r"^[A-Z]\s+", "", cleaned.strip())
        
        # Nettoyer les espaces multiples
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        
        return cleaned


    def _infer_missing_living_room(self, result: 'ExtractionResult') -> 'ExtractionResult':
        """
        If OCR missed the living room (séjour/cuisine), infer from surface gap.

        Rules (all must hold):
        - declared living_space > 0
        - gap in [12, 60] m² (living rooms are 15-50m²)
        - no sejour, sejour_cuisine, reception, or living_kitchen already present
        - at least one bedroom present (confirms it's a real apartment)
        - gap does not match any existing room surface (avoid double-count)
        """
        if result.living_space <= 0:
            return result

        interior = [r for r in result.rooms if not r.is_exterior and not r.is_composite]
        calc = round(sum(r.surface for r in interior), 2)
        gap = round(result.living_space - calc, 2)

        if not (12.0 <= gap <= 60.0):
            return result

        # Check: no living room already present
        living_types = {RoomType.LIVING_ROOM, RoomType.LIVING_KITCHEN, RoomType.RECEPTION}
        if any(r.room_type in living_types for r in result.rooms):
            return result

        # Need at least one bedroom
        if not any(r.room_type == RoomType.BEDROOM for r in result.rooms):
            return result

        # Gap must not match an existing surface
        existing_surfaces = {round(r.surface, 2) for r in interior}
        if gap in existing_surfaces:
            return result

        logger.info(
            f"  🔧 Inférence séjour/cuisine: sejour_cuisine={gap}m² "
            f"(declared={result.living_space}, calc={calc})"
        )

        inferred_room = ExtractedRoom(
            name_raw="Séjour / Cuisine (inféré)",
            name_normalized="sejour_cuisine",
            surface=gap,
            room_type=RoomType.LIVING_KITCHEN,
            is_exterior=False,
            source="inferred",
            confidence=0.65,
        )
        result.rooms.append(inferred_room)
        result.sources["sejour_cuisine"] = "inferred"
        return result

    def _infer_missing_bedroom(self, result: 'ExtractionResult') -> 'ExtractionResult':
        """
        If OCR missed a bedroom (common when scan quality is poor on one cell),
        infer it from the surface gap between declared and calculated habitable.

        Rules (all must hold):
        - declared living_space > 0
        - gap is in bedroom range [5.0, 40.0] m²
        - we have at least one bedroom already (chambre / chambre_1)
        - we do NOT already have a chambre_2 (or higher matching the gap)
        - gap matches no other room type already present (avoid double-counting)
        """
        if result.living_space <= 0:
            return result

        interior = [r for r in result.rooms if not r.is_exterior and not r.is_composite]
        calc = round(sum(r.surface for r in interior), 2)
        gap = round(result.living_space - calc, 2)

        if not (5.0 <= gap <= 40.0):
            return result

        # Check we have at least one bedroom
        bedrooms = [r for r in result.rooms if r.room_type == RoomType.BEDROOM]
        if not bedrooms:
            return result

        # Find what number the next bedroom should be
        bedroom_numbers = sorted([r.room_number for r in bedrooms if r.room_number])
        next_num = (max(bedroom_numbers) + 1) if bedroom_numbers else 2
        inferred_name = f"chambre_{next_num}"

        # Make sure this bedroom doesn't already exist
        existing_names = {r.name_normalized for r in result.rooms}
        if inferred_name in existing_names:
            return result

        # Make sure gap doesn't match a surface already present (avoid double-count)
        existing_surfaces = {round(r.surface, 2) for r in interior}
        if gap in existing_surfaces:
            return result

        logger.info(
            f"  🔧 Inférence: {inferred_name}={gap}m² "
            f"(surface déclarée={result.living_space}, calc={calc})"
        )

        inferred_room = ExtractedRoom(
            name_raw=f"Chambre {next_num} (inféré)",
            name_normalized=inferred_name,
            surface=gap,
            room_type=RoomType.BEDROOM,
            is_exterior=False,
            room_number=next_num,
            source="inferred",
            confidence=0.6,
        )
        result.rooms.append(inferred_room)
        result.sources[inferred_name] = "inferred"
        return result

    def _detect_typology(self, rooms):
        """
        Detect typology: T1, T2, T3, T4, T5, etc.
        Tn = n rooms (bedrooms) + living room
        """
        bedrooms = sum(1 for r in rooms if r.room_type == RoomType.BEDROOM)
        if bedrooms == 0:
            has_living = any(
                r.room_type in [
                    RoomType.LIVING_ROOM, RoomType.LIVING_KITCHEN,
                    RoomType.RECEPTION,
                ]
                for r in rooms
            )
            return "Studio" if has_living else "T1"
        # T2 = 1 bedroom + living, T3 = 2 bedrooms + living, etc.
        return f"T{bedrooms + 1}"

    def _detect_property_type(self, rooms, floor: str = "", typology_hint: str = "", property_type_hint: str = "", full_text: str = ""):
        # Check for Commercial/Magasin type first (Moroccan floor plans)
        # First check explicit property_type_hint (from MAGASIN reference or TYPE field)
        if property_type_hint and property_type_hint.lower() in ['magasin', 'commercial']:
            return property_type_hint.lower()
        # Then check typology_hint for commercial keywords
        if typology_hint and typology_hint.lower() in ['commercial', 'magasin', 'commerce']:
            return "magasin"
        
        # Also check full_text for MAGASIN keyword if provided
        if full_text and re.search(r'\bMAGASIN\b', full_text.upper()):
            return "magasin"
        
        # Check if floor string contains MAGASIN (e.g., "MAGASIN RDC")
        if floor and 'MAGASIN' in floor.upper():
            return "magasin"
        
        has_garden = any(r.room_type == RoomType.GARDEN for r in rooms)
        has_cellar = any(r.room_type == RoomType.CELLAR for r in rooms)
        has_parking = any(r.room_type == RoomType.PARKING for r in rooms)
        
        # Maison: a jardin OU cave OU parking OU plusieurs niveaux réels
        # Only consider multi-floor if there are multiple actual floor levels (e.g., "RDC+R+1" or "R+1,R+2")
        # NOT just a single floor like "R+5" which is common for apartments
        # Also NOT RDC + Mezzanine - Mezzanine is just a half-floor in an apartment
        # Also NOT RDC + R+5 alone - that's just a ground floor + upper floor in same apartment
        if floor:
            floor_upper = floor.upper()
            # Count occurrences of R+ (multiple R+ floors = multi-level)
            r_floor_count = len(re.findall(r'\bR\+\d+\b', floor_upper))
            has_rdc = 'RDC' in floor_upper
            has_mezz = 'MEZ' in floor_upper
            # Multi-floor: only multiple R+ floors (e.g., R+1,R+2) OR actual duplex floors
            # Not: single R+ floor, not RDC+MEZ, not RDC+R+5
            is_multi_floor = r_floor_count > 1
        else:
            is_multi_floor = False
        
        return "house" if (has_garden or has_cellar or has_parking or is_multi_floor) else "appartment"

    # def _final_dedup(self, rooms):
    #     """
    #     Dédoublonnage final: supprime les pièces avec même type + même surface.
    #     Garde celle avec la meilleure confiance.
    #     """
    #     seen = {}  # (room_type, surface_arrondie) → ExtractedRoom
    #     deduped = []

    #     for r in rooms:
    #         key = (r.room_type, round(r.surface, 1))

    #         if key in seen:
    #             existing = seen[key]
    #             logger.info(
    #                 f"  🔄 Doublon final supprimé: '{r.name_raw}' ({r.surface}m²) "
    #                 f"= '{existing.name_raw}' ({existing.surface}m²)"
    #             )
    #             # Garde celui avec meilleure confiance
    #             if r.confidence > existing.confidence:
    #                 deduped.remove(existing)
    #                 seen[key] = r
    #                 deduped.append(r)
    #             continue

    #         seen[key] = r
    #         deduped.append(r)

    #     if len(deduped) < len(rooms):
    #         logger.info(
    #             f"  🧹 Dédoublonnage: {len(rooms)} → {len(deduped)} pièces"
    #         )

    #     return deduped

    def _final_dedup(self, rooms):
        """
        Dédoublonnage final:
        - Clé 1: name_normalized → même nom = doublon (garde meilleure confiance)
        - Clé 2: (type, numéro, surface) → même pièce vue deux fois avec noms différents
        """
        # Pass 1: deduplicate by exact name_normalized
        by_name = {}
        for r in rooms:
            key = r.name_normalized
            if key in by_name:
                existing = by_name[key]
                logger.info(
                    f"  🔄 Doublon nom supprimé: '{r.name_raw}' ({r.surface}m²) "
                    f"= '{existing.name_raw}' ({existing.surface}m²)"
                )
                if r.confidence > existing.confidence:
                    by_name[key] = r
            else:
                by_name[key] = r
        rooms = list(by_name.values())

        # Pass 2: deduplicate by (type, number, surface)
        seen = {}
        deduped = []
        for r in rooms:
            room_num = r.room_number if r.room_number else 0
            key = (r.room_type, room_num, round(r.surface, 1))
            if key in seen:
                existing = seen[key]
                logger.info(
                    f"  🔄 Doublon type+surf supprimé: '{r.name_raw}' ({r.surface}m²) "
                    f"= '{existing.name_raw}' ({existing.surface}m²)"
                )
                if r.confidence > existing.confidence:
                    deduped.remove(existing)
                    seen[key] = r
                    deduped.append(r)
                continue
            seen[key] = r
            deduped.append(r)

        if len(deduped) < len(rooms):
            logger.info(
                f"  🧹 Dédoublonnage: {len(rooms)} → {len(deduped)} pièces"
            )
        return deduped
    
    def _filter_by_reference(self, rooms, reference, living_space):
        if living_space <= 0 or len(rooms) < 3:
            return rooms

        interior = [r for r in rooms if not r.is_exterior and not r.is_composite]
        exterior = [r for r in rooms if r.is_exterior]

        calc = sum(r.surface for r in interior)
        diff = abs(calc - living_space)
        
        # Always run false duplicate removal to handle cases like 'placard' being confused with 'salle_de_bain'
        rooms = self._remove_false_duplicates(rooms)
        interior = [r for r in rooms if not r.is_exterior and not r.is_composite]
        exterior = [r for r in rooms if r.is_exterior]
        calc = sum(r.surface for r in interior)
        diff = abs(calc - living_space)

        if diff <= 1.0:
            return rooms

        # Also check for essential rooms that should always be kept
        essential_types = {"wc", "salle_de_bain", "salle_d_eau", "entree", "circulation", "storage", "cuisine", "reception",
                           "salle_de_bain_2", "salle_d_eau_2", "salle_de_bain_3",
                           "chambre", "chambre_1", "chambre_2", "chambre_3", "chambre_4",
                           "chambre_1_2", "chambre_2_2", "chambre_3_2"}
        essential_rooms = [r for r in interior if r.name_normalized.split("_")[0] in essential_types]
        
        # Identify rooms from spatial extraction (more reliable)
        spatial_sources = {r.source for r in rooms if r.source == "spatial"}
        spatial_rooms = {r.name_normalized for r in rooms if r.source == "spatial"}
        
        if calc > living_space * 1.10:
            logger.info(
                f"  🔍 Multi-appart détecté: calc={calc:.2f} >> "
                f"declared={living_space:.2f}. Filtrage..."
            )
            
            # First try to find subset that includes spatial rooms (they're more reliable)
            if spatial_rooms:
                # Filter interior to prioritize rooms that exist in spatial extraction
                spatial_priority = []
                other_rooms = []
                for r in interior:
                    base_name = r.name_normalized.split("_")[0]
                    # Check if this room type exists in spatial
                    if any(spatial_name.split("_")[0] == base_name for spatial_name in spatial_rooms):
                        spatial_priority.append(r)
                    else:
                        other_rooms.append(r)
                
                # Try finding best subset prioritizing spatial rooms
                best = self._find_best_subset(spatial_priority + other_rooms, living_space)
            else:
                best = self._find_best_subset(interior, living_space)
            if best:
                # Always keep essential rooms (WC, SDB, entrance, etc.)
                essential_in_best = {r.name_normalized.split("_")[0] for r in best}
                missing_essential = [r for r in essential_rooms 
                                   if r.name_normalized.split("_")[0] not in essential_in_best]
                
                if missing_essential:
                    logger.info(f"  🔧 Ajout {len(missing_essential)} pièces essentielles: "
                              f"{[r.name_normalized for r in missing_essential]}")
                    best = best + missing_essential
                
                # Keep only ONE room per type+number combination
                # This removes duplicates from multi-apartment extraction
                # Prefer: 1) spatial source, 2) larger surface
                by_type_num = {}
                for r in best:
                    key = r.name_normalized  # Use full normalized name as key
                    if key not in by_type_num:
                        by_type_num[key] = r
                    else:
                        # Keep the one from spatial source or with larger surface
                        existing = by_type_num[key]
                        if r.source == "spatial" and existing.source != "spatial":
                            by_type_num[key] = r
                        elif r.source == existing.source and r.surface > existing.surface:
                            by_type_num[key] = r
                best = list(by_type_num.values())
                
                # Remove false duplicates: if 'placard' has same surface as 'salle_de_bain', keep only salle_de_bain
                # This handles cases where OCR misreads room names
                sdb_surfaces = {round(r.surface, 2) for r in best if 'salle_de_bain' in r.name_normalized}
                if sdb_surfaces:
                    best = [r for r in best 
                            if not (r.name_normalized == 'placard' and round(r.surface, 2) in sdb_surfaces)]
                
                # Also deduplicate exterior rooms
                ext_by_type = {}
                for r in exterior:
                    base = r.name_normalized.split("_")[0]
                    if base not in ext_by_type or r.surface > ext_by_type[base].surface:
                        ext_by_type[base] = r
                exterior = list(ext_by_type.values())
                
                # Remove false duplicates: if 'placard' has same surface as 'salle_de_bain', keep only salle_de_bain
                # This handles cases where OCR misreads room names - apply to all results
                best = self._remove_false_duplicates(best)
                
                result = best + exterior
                logger.info(
                    f"  ✅ Filtré: {len(rooms)} → {len(result)} pièces"
                )
                return result

        return rooms

    def _remove_false_duplicates(self, rooms):
        """
        Remove false duplicates based on surface matching.
        If 'placard' has the same surface as 'salle_de_bain', remove the placard.
        This handles OCR misreads where room names are confused.
        """
        if not rooms:
            return rooms
        
        # Find surfaces that have both salle_de_bain and placard
        sdb_surfaces = {round(r.surface, 2) for r in rooms if 'salle_de_bain' in r.name_normalized}
        
        if not sdb_surfaces:
            return rooms
        
        # Filter out placard rooms with same surface as salle_de_bain
        filtered = [r for r in rooms 
                   if not (r.name_normalized == 'placard' and round(r.surface, 2) in sdb_surfaces)]
        
        if len(filtered) < len(rooms):
            logger.info(f"  🔧 Supprimé {len(rooms) - len(filtered)} doublons faux: placard avec même surface que salle_de_bain")
        
        return filtered
    
    def _filter_exteriors(self, exterior_rooms):
        """Dédoublonne les extérieurs: garde 1 par type (le plus grand)"""
        by_type = {}
        for r in exterior_rooms:
            if r.room_type not in by_type or r.surface > by_type[r.room_type].surface:
                by_type[r.room_type] = r
        return list(by_type.values())

    def _find_best_subset(self, rooms, target):
        """
        Trouve le sous-ensemble cohérent dont la somme ≈ target.
        Priorise la cohérence (pas de doublons de type) avant la somme.
        
        IMPORTANT: Only keep one room per type+number combination.
        """
        from itertools import combinations

        n = len(rooms)
        best_diff = float("inf")
        best_combo = None

        min_size = max(3, n // 2)
        max_size = min(n, n - 1) if n > 3 else n

        for size in range(min_size, max_size + 1):
            # Limiter les combinaisons pour éviter explosion
            if self._comb_count(n, size) > 50000:
                continue

            for combo in combinations(rooms, size):
                total = sum(r.surface for r in combo)
                diff = abs(total - target)

                if diff >= best_diff:
                    continue

                # Vérifier la cohérence: pas de doublon de type sans numéro
                if self._has_type_conflict(combo):
                    continue

                best_diff = diff
                best_combo = list(combo)

                if diff < 0.5:
                    return best_combo

        if best_combo and best_diff < 2.0:
            return best_combo
        return None

    def _has_type_conflict(self, combo):
        """
        Vérifie qu'un sous-ensemble est cohérent:
        - Pas 2 séjour/cuisine (1 seul par appart)
        - Pas 2 entrées
        """
        unique_types = [
            RoomType.LIVING_KITCHEN,
            RoomType.LIVING_ROOM,
            RoomType.ENTRY,
            RoomType.RECEPTION,
        ]
        for rt in unique_types:
            count = sum(1 for r in combo if r.room_type == rt)
            if count > 1:
                return True  # Conflit
        return False

    def _comb_count(self, n, r):
        """Nombre de combinaisons C(n,r)"""
        from math import comb
        return comb(n, r)


# ═══════════════════════════════════════════════
# API PUBLIQUE
# ═══════════════════════════════════════════════

def extract_plan_data(pdf_path: str, reference_hint: Optional[str] = None) -> Dict[str, Any]:
    """Extrait et retourne directement le format legacy (dict)"""
    extractor = SuperExtractor()
    result = extractor.extract(pdf_path, reference_hint)
    return result.to_legacy_format()


def extract_all_plans(pdf_path: str) -> Dict[str, Any]:
    """
    Extrait tous les plans d'un PDF multi-pages.

    Returns structure:
    - Single floor lot:  {"A08": { ...flat result... }}
    - Multi-floor lot:   {"A18": {"A18_R+1": {...}, "A18_R+2": {...}}}
    - Mixed PDF:         {"A08": {...}, "A18": {"A18_R+1": {...}, "A18_R+2": {...}}}
    """
    extractor = SuperExtractor()
    raw_results = extractor.extract_all_pages(pdf_path)

    output = {}
    for ref, value in raw_results.items():
        if isinstance(value, dict):
            # Multi-floor: value is already {"A18_R+1": result, "A18_R+2": result}
            nested = {}
            for floor_key, floor_result in value.items():
                floor_result.reference = ref  # ensure ref is the parent ref
                nested[floor_key] = floor_result.to_legacy_format()[floor_result.reference]
                nested[floor_key]["floor_key"] = floor_key
            output[ref] = nested
        else:
            # Single floor: flat result
            legacy = value.to_legacy_format()
            output.update(legacy)

    return output


def extract_plan_data_legacy(pdf_path: str, reference_hint: Optional[str] = None) -> Dict[str, Any]:
    """Alias pour compatibilité"""
    return extract_plan_data(pdf_path, reference_hint)


def batch_extract(pdf_paths: List[str], hints: Optional[List[str]] = None) -> Dict[str, Dict]:
    """Extraction batch de plusieurs PDFs"""
    extractor = SuperExtractor()
    results = {}
    for i, path in enumerate(pdf_paths):
        hint = hints[i] if hints and i < len(hints) else None
        results.update(extractor.extract(path, hint).to_legacy_format())
    return results