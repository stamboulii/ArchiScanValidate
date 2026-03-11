"""
Room Normalizer - Normalisation intelligente des noms de pièces
Clé: Réception est DISTINCT de Séjour (ne fusionne plus)
"""

import re
import logging
from typing import Tuple, Optional

from .models import RoomType

logger = logging.getLogger(__name__)


class RoomNormalizer:

    # (pattern, name_template, RoomType, is_exterior)
    # Ordre: composites et spécifiques EN PREMIER
    ROOM_ALIASES = [
            # ══════════════════════════════════════════
            # COMPOSITES (toujours EN PREMIER)
            # ══════════════════════════════════════════
            (r"^(SEJOUR|S[ÉE]JOUR|SALON|RECEPTION|R[ÉE]CEPTION)\s*/?\s*CUISINE",
            "sejour_cuisine", RoomType.LIVING_KITCHEN, False),
            (r"^CUISINE\s*/?\s*(SEJOUR|S[ÉE]JOUR|SALON)",
            "sejour_cuisine", RoomType.LIVING_KITCHEN, False),
            (r"^PI[ÈE]CE\s*(DE\s*VIE|PRINCIPALE|A\s*VIVRE)",
            "sejour_cuisine", RoomType.LIVING_KITCHEN, False),
            # Handle "Pièce de vie (Living room)" with parentheses translation
            (r"^PI[ÈE]CE\s*(DE\s*VIE|PRINCIPALE|A\s*VIVRE)\s*\(.*?\)",
            "sejour_cuisine", RoomType.LIVING_KITCHEN, False),
            (r"^(LIVING|ESPACE)\s*/?\s*(CUISINE|KITCHEN)",
            "sejour_cuisine", RoomType.LIVING_KITCHEN, False),
            # Séjour-Cuisine avec tiret (variante OCR/plan)
            (r"^(S[ÉE]JOUR|SEJOUR|SALON|RECEPTION|R[ÉE]CEPTION)\s*[-]\s*(CUISINE|KITCHEN)",
            "sejour_cuisine", RoomType.LIVING_KITCHEN, False),
            (r"^(CUISINE)\s*[-]\s*(S[ÉE]JOUR|SEJOUR|SALON)",
            "sejour_cuisine", RoomType.LIVING_KITCHEN, False),

            # SDB/WC combiné (accepte / ou + comme séparateur — OCR confond les deux)
            (r"^(SDB\s*[/+]\s*WC|SDB\s*WC|SALLE\s*DE\s*BAINS?\s*[/+]?\s*WC)$",
            "salle_de_bain", RoomType.BATHROOM, False),
            (r"^(WC\s*[/+]\s*SDB|WC\s*SDB)$",
            "salle_de_bain", RoomType.BATHROOM, False),

            # SDE/WC combiné (accepte / ou + comme séparateur — OCR confond les deux)
            (r"^(SDE\s*[/+]\s*WC|SDE\s*WC|SALLE\s*D['\u2019]?\s*EAU\s*[/+]?\s*WC)$",
            "salle_d_eau", RoomType.SHOWER_ROOM, False),
            (r"^(WC\s*[/+]\s*SDE|WC\s*SDE)$",
            "salle_d_eau", RoomType.SHOWER_ROOM, False),

            # ══════════════════════════════════════════
            # RÉCEPTION (distinct de séjour)
            # ══════════════════════════════════════════
            (r"^(RECEPTION|R[ÉE]CEPTION|PIECE\s*DE\s*RECEPTION)$",
             "reception", RoomType.RECEPTION, False),

            # ══════════════════════════════════════════
            # SÉJOUR / SALON - numbered suffixes FIRST (before basic pattern)
            # ══════════════════════════════════════════
            # Handle SEJOUR_2, SEJOUR_3, etc. (numbered suffixes already in raw name)
            (r"^(SEJOUR|S[ÉE]JOUR|SALON)_(\d+)$", "sejour_{n}", RoomType.LIVING_ROOM, False),
            # Basic SEJOUR pattern
            (r"^(SEJOUR|S[ÉE]JOUR|SALON|LIVING|DOUBLE\s*S[ÉE]JOUR|"
            r"SALLE\s*[AÀ]\s*MANGER|SAM|PIECE\s*PRINCIPALE)$",
            "sejour", RoomType.LIVING_ROOM, False),

            # ══════════════════════════════════════════
            # CUISINE
            # ══════════════════════════════════════════
            (r"^(CUISINE|KITCHENETTE|COIN\s*CUISINE|CUISINE\s*[ÉE]QUIP[ÉE]E|"
            r"ESPACE\s*CUISINE|CUISINE\s*AM[ÉE]RICAINE|OFFICE|"
            r"ARRI[ÈE]RE\s*CUISINE|CUISINE\s*OUVERTE|ARRIERE\s*CUISINE)$",
            "cuisine", RoomType.KITCHEN, False),

            # === Entrée + Placard combiné (OCR: "Entrée + PI.") ===
            (r"^(ENTR[ÉEée]E|HALL)\s*\+\s*(PI\.?|PL\.?|PLACARD|PLC)$",
            "entree", RoomType.ENTRY, False),
            # === Entrée/DGT combiné (AVANT le pattern entrée simple) ===
            (r"^(ENTREE\s*/\s*DGT|ENTR[ÉEée]E\s*/\s*D[ÉE]GAGEMENT|"
            r"HALL\s*/\s*DGT|ENTR[ÉEée]E\s*/\s*CIRCULATION)$",
            "entree", RoomType.ENTRY, False),
            
            # === PLACARD (ajoute) ===
            # Note: pattern must NOT match "Pl (sous escalier)" - more specific patterns first
            (r"^PLACARD$", "placard", RoomType.STORAGE, False),
            (r"^(DRESSING|ARMOIRE)$", "placard", RoomType.STORAGE, False),
            # Handle "Pl (sous escalier)" -> "placard_escalier"
            (r"^PL\s*\(.*?ESCALIER.*?\)$",
            "placard_escalier", RoomType.STORAGE, True),
            
            # === RANGEMENT (ajoute) ===
            (r"^(RANGEMENT|STOCKAGE|DEBARRAS)$",
            "storage", RoomType.STORAGE, False),
            
            # === RANGEMENT avec numero ===
            (r"^(RANGEMENT|PLACARD|DRESSING)\s*(\d+)$",
            "rangement_{n}", RoomType.STORAGE, False),
            
            # === PI/Pl (placard abrege OCR) ===
            (r"^(PI|PLC|PL\.?)$",
            "placard", RoomType.STORAGE, False),

            # ══════════════════════════════════════════
            # ENTRÉE / HALL
            # ══════════════════════════════════════════
            (r"^(ENTR[ÉEée]E|HALL\s*D['\u2019]?ENTR[ÉEée]E|HALL|VESTIBULE|"
            r"SAS\s*D['\u2019]?ENTR[ÉEée]E|SAS|ACCUEIL)$",
            "entree", RoomType.ENTRY, False),

            # ══════════════════════════════════════════
            # CIRCULATION / DÉGAGEMENT
            # ══════════════════════════════════════════
            # DGT + PLACARD combiné ex: "Dgt. + Pl." (OCR: "Dgt.+PI.", "Dot.+PI.")
            # After OCR corrections in normalize(), these all become "DGT.+PL."
            (r"^D\.?G\.?T\.?\s*[+]\s*P[LI]\.?\s*$",
             "circulation", RoomType.CIRCULATION, False),
            # PALIER -> palier (not circulation)
            # Handle "PALIER - BUREAU", "PALIER - bureau", etc.
            (r"^(PALIER)\s*[-]\s*.*$",
            "palier", RoomType.CIRCULATION, False),
            (r"^(PALIER)$",
            "palier", RoomType.CIRCULATION, False),
            (r"^(DGT|D\.G\.T\.?|DEG\.?|D[ÉE]G\.?|D[ÉE]GAGEMENT|COULOIR|CIRCULATION|"
            r"DIST\.?|DISTRIBUTION|PASSAGE|COURSIVE|ESCALIER)$",
            "circulation", RoomType.CIRCULATION, False),

            # ══════════════════════════════════════════
            # CHAMBRES
            # ══════════════════════════════════════════
            (r"^CHAMBRE\s*(\d+)$", "chambre_{n}", RoomType.BEDROOM, False),
            # Chambre avec placard: "Chambre 1 + Pl." or "Chambre1+Pl."
            (r"^CHAMBRE\s*(\d+)\s*[+\.].*$", "chambre_{n}", RoomType.BEDROOM, False),
            (r"^CHAMBRE$", "chambre", RoomType.BEDROOM, False),
            # OCR variant: digit misread as "Pl" → "Chambre Pl" = Chambre (numéro inconnu)
            (r"^CHAMBRE\s+PL\.?\s*(\d*)$", "chambre_{n}", RoomType.BEDROOM, False),
            (r"^CH\.?\s*(\d+)$", "chambre_{n}", RoomType.BEDROOM, False),
            (r"^SUITE\s*PARENTALE\s*(\d*)$", "chambre_{n}", RoomType.BEDROOM, False),
            (r"^(BUREAU|OFFICE|CABINET)\s*(\d*)$", "chambre_{n}", RoomType.BEDROOM, False),
            (r"^CHAMBRE\s*D['\u2019']?\s*(AMIS?|ENFANTS?)\s*(\d*)$",
            "chambre_{n}", RoomType.BEDROOM, False),
            (r"^CH\s*(\d+)$", "chambre_{n}", RoomType.BEDROOM, False),
            (r"^CHAMBRE\s*PARENTALE$", "chambre_1", RoomType.BEDROOM, False),
            (r"^CHAMBRE\s*/\s*BUREAU\s*(\d*)$", "chambre_{n}", RoomType.BEDROOM, False),

            # ══════════════════════════════════════════
            # SALLE DE BAIN
            # ══════════════════════════════════════════
            (r"^(SALLE\s*DE\s*BAINS?|SDB|S\.?\s*D\.?\s*B\.?|BAIN|BAINS)\s*(\d+)$",
            "salle_de_bain_{n}", RoomType.BATHROOM, False),
            (r"^(SALLE\s*DE\s*BAINS?|SDB|S\.?\s*D\.?\s*B\.?|BAIN|BAINS)$",
            "salle_de_bain", RoomType.BATHROOM, False),
            # Handle "Bains (Bathroom)" with parentheses translation
            (r"^(SALLE\s*DE\s*BAINS?|BAIN|BAINS)\s*\(.*?\)$",
            "salle_de_bain", RoomType.BATHROOM, False),
            (r"^(SALLE\s*DE\s*BAINS?\s*PARENTALE)$",
            "salle_de_bain_1", RoomType.BATHROOM, False),

            # ══════════════════════════════════════════
            # SALLE D'EAU
            # ══════════════════════════════════════════
            (r"^(SALLE\s*D['\u2019']?\s*EAU|SDE|S\.?\s*D\.?\s*E\.?|EAU)\s*(?:N[°\u00b0]?)?\s*(\d+)$",
            "salle_d_eau_{n}", RoomType.SHOWER_ROOM, False),
            (r"^(SALLE\s*D['\u2019']?\s*EAU|SDE|S\.?\s*D\.?\s*E\.?|EAU)$",
            "salle_d_eau", RoomType.SHOWER_ROOM, False),

            # ══════════════════════════════════════════
            # WC / TOILETTES
            # ══════════════════════════════════════════
            (r"^(WC|W\.?\s*C\.?|TOILETT?E?S?)\s*(\d+)$",
            "wc_{n}", RoomType.WC, False),
            (r"^(WC|W\.?\s*C\.?|TOILETT?E?S?)$",
            "wc", RoomType.WC, False),
            (r"^(RGT\s*WC|RGT\s*W\.?\s*C\.?)$",
            "wc", RoomType.WC, False),

            # ══════════════════════════════════════════
            # RANGEMENTS
            # ══════════════════════════════════════════
            (r"^(DRESSING)\s*(\d*)$", "dressing", RoomType.DRESSING, False),
               (r"^(BUANDERIE|LINGERIE)$", "buanderie", RoomType.STORAGE, False),
            (r"^ARRI[\u00c8E]RE\s*CUISINE$", "arriere_cuisine", RoomType.KITCHEN, False),
         (r"^(PLACARD|CELLIER|RANGEMENT|RGT|"
            r"LOCAL\s*TECHNIQUE|LOCAL\s*POUSSETTE|CELLIER\s*/\s*BUANDERIE|"
            r"GRENIER|REMISE|D[\u00c9E]BARRAS|CAVE\s*INT[\u00c9E]RIEURE|"
            r"LOCAL|STOCKAGE)$",
            "storage", RoomType.STORAGE, False),

            # ══════════════════════════════════════════
            # EXTÉRIEUR - BALCON
            # ══════════════════════════════════════════
            # Handle "Balcon" alone (without surface value)
            (r"^BALCON(?:Y)?$", "balcon", RoomType.BALCONY, True),
            # Handle "Balcon 15.99 m²" format (with unit)
            (r"^BALCON(?:Y)?\s*:?\s*(\d+[\.,]\d+)\s*m?²?", "balcon", RoomType.BALCONY, True),
            (r"^BALCON(?:Y)?\s*:?\s*(\d*)$", "balcon{n}", RoomType.BALCONY, True),
            (r"^BALCON:\s*(\d+[\.,]\d+)", "balcon", RoomType.BALCONY, True),
            (r"^BALCON\s*(\d+)$", "balcon_{n}", RoomType.BALCONY, True),
            
            # ══════════════════════════════════════════
            # EXTÉRIEUR - JARDIN
            # ══════════════════════════════════════════
            # Handle "SURFACE JARDIN" and "SURFACE GARDEN" → normalize to "garden"
            (r"^SURFACE\s*JARDIN\s*:?\s*(\d+[\.,]\d+)?", "garden", RoomType.GARDEN, True),
            (r"^SURFACE\s*GARDEN\s*:?\s*(\d+[\.,]\d+)?", "garden", RoomType.GARDEN, True),
            # Handle "Jardin" without colon, "Jardin 29.3", etc.
            (r"^JARDIN\s*:?\s*(\d+[\.,]\d+)", "garden", RoomType.GARDEN, True),
            (r"^JARDIN\s*(\d+)$", "garden", RoomType.GARDEN, True),
            (r"^JARDIN$", "garden", RoomType.GARDEN, True),
            
            # PORCHE (exterior)
            (r"^(PORCHE|PORCH)$", "porche", RoomType.TERRACE, True),
            (r"^BALCON:\s*(\d+[\.,]\d+)", "balcon", RoomType.BALCONY, True),
            (r"^BALCON\s*(\d+)$", "balcon_{n}", RoomType.BALCONY, True),

            # ══════════════════════════════════════════
            # EXTÉRIEUR - TERRASSE
            # ══════════════════════════════════════════
            (r"^TERRASSE\s*(\d*)$", "terrasse", RoomType.TERRACE, True),
            # Terrasse with unit code like "Terrasse C01" (surface may be on next line)
            (r"^TERRASSE\s+[A-Za-z0-9_-]+", "terrasse", RoomType.TERRACE, True),
            # Terrasse with surface on same line
            (r"^TERRASSE\s+[A-Za-z0-9_-]+.*?(\d+[\.,]\d+)", "terrasse", RoomType.TERRACE, True),
            # Terrasse avec jardinière (format "TERRASSE+JARDINIERE")
            (r"^TERRASSE\s*\+\s*(JARDINIERE|JARDIN[IÈ]RE|JARDIN)\s*(\d*)$",
            "terrasse", RoomType.TERRACE, True),
            (r"^(TERRASSE\s*COUVERTE)\s*(\d*)$", "terrasse", RoomType.TERRACE, True),
            (r"^(ROOF\s*TOP|TOIT\s*TERRASSE)\s*(\d*)$", "terrasse", RoomType.TERRACE, True),
            (r"^(SOLARIUM)\s*(\d*)$", "terrasse", RoomType.TERRACE, True),

            # ══════════════════════════════════════════
            # EXTÉRIEUR - JARDIN
            # ══════════════════════════════════════════
            # Handle "SURFACE JARDIN" and "SURFACE GARDEN" → normalize to "garden"
            (r"^SURFACE\s*JARDIN\s*:?\s*(\d+[\.,]\d+)?", "garden", RoomType.GARDEN, True),
            (r"^SURFACE\s*GARDEN\s*:?\s*(\d+[\.,]\d+)?", "garden", RoomType.GARDEN, True),
            # Handle "Jardin" with or without colon, "Jardin 29.3", etc.
            (r"^JARDIN\s*:?\s*(\d+[\.,]\d+)", "garden", RoomType.GARDEN, True),
            (r"^JARDIN\s*(\d+)$", "garden", RoomType.GARDEN, True),
            (r"^JARDIN$", "garden", RoomType.GARDEN, True),
            # Handle "MI011 Espaces verts" - filter out reference prefix
            (r"^(ESPACES\s*VERTS|JARDINET|JARDIN\s*PRIVATIF)\s*(\d*)$",
            "garden", RoomType.GARDEN, True),
            # Espace planté (jardinière sur terrasse)
            (r"^(ESPACE\s*PLANT[ÉEée]S?|ESPACE\s*VERT)\s*(\d*)$",
            "garden", RoomType.GARDEN, True),

            # ══════════════════════════════════════════
            # EXTÉRIEUR - LOGGIA
            # ══════════════════════════════════════════
            (r"^(LOGGIA|LOGIA)\s*(\d*)$", "loggia", RoomType.LOGGIA, True),

            # ══════════════════════════════════════════
            # EXTÉRIEUR - PATIO / COUR / PORCHE
            # ══════════════════════════════════════════
            (r"^(PATIO|COUR|COURETTE|COUR\s*ANGLAISE)\s*(\d*)$",
            "patio", RoomType.PATIO, True),
            # Porche (covered entrance area)
            (r"^(PORCHE)\s*(\d*[\.,]?\d*)$",
            "porche", RoomType.TERRACE, True),
            # Porche (covered entrance area)
            (r"^(PORCHE)\s*(\d*[\.,]?\d*)$",
            "porche", RoomType.TERRACE, True),

            # ══════════════════════════════════════════
            # PARKING / GARAGE (distingués par le nom normalisé)
            # ══════════════════════════════════════════
            # Garage / box fermé → nom 'garage' (permet détection has_garage)
            (r"^(GARAGE|BOX)\s*(\d*)$",
            "garage", RoomType.PARKING, True),
            # Garage avec parenthèses (format OCR)
            (r"^\(?GARAGE\)?$",
            "garage", RoomType.PARKING, True),
            # Parking / stationnement ouvert → nom 'parking'
            (r"^(PARKING|STATIONNEMENT|PLACE\s*DE\s*PARKING)\s*(\d*)$",
            "parking", RoomType.PARKING, True),

            # ══════════════════════════════════════════
            # CAVE
            # ══════════════════════════════════════════
            (r"^(CAVE|SOUS[\s\-]?SOL)\s*(\d*)$", "cave", RoomType.CELLAR, True),

            # ══════════════════════════════════════════
            # EXTÉRIEUR - VÉRANDA (bonus)
            # ══════════════════════════════════════════
            (r"^(V[ÉE]RANDA|PERGOLA|AUVENT)\s*(\d*)$",
            "terrasse", RoomType.TERRACE, True),
        ]

    def __init__(self):
        self._seen_names = {}

    def reset(self):
        """Reset entre deux extractions"""
        self._seen_names = {}

    def normalize(self, name_raw: str) -> Tuple[Optional[str], Optional[RoomType], Optional[int], bool, float]:
        """
        Returns: (name_normalized, room_type, room_number, is_exterior, confidence)
        Returns (None, None, None, False, 0.0) si non reconnu
        """
        # Nettoyer le nom: supprimer parenthèses, accents, etc.
        name_clean = name_raw.strip().upper()
        
        # Supprimer les références comme "MI011" en début de chaîne
        # Patterns: M011, MI011, LOT_001, A008, etc.
        name_clean = re.sub(r"^(MI\d+|M\d+|LOT_?\d+|[A-Z]\d{2,})\s+", "", name_clean)
        
        # Supprimer les parenthèses mais garder le contenu (format OCR: "(Garage)" -> "Garage")
        # Handle "Pièce de vie (Living room)" - parenthèses partout dans la chaîne
        name_clean = re.sub(r"\([^)]*\)", "", name_clean).strip()
        
        # Supprimer les préfixes comme "m2" (format OCR corrompu: "m2 Entrée")
        name_clean = re.sub(r"^M2\s+", "", name_clean).strip()
        
        # Supprimer le bruit OCR en FIN seulement (ex: 'Dgt.+Pl. !' → 'Dgt.+Pl.')
        # NB: le bruit en DÉBUT est géré par spatial_extractor._strip_line_noise()
        name_clean = re.sub(r"[^A-ZÀ-Ÿ0-9\.\+/\s]+$", "", name_clean).strip()

        # ── Corrections OCR systématiques ──────────────────────────────────
        # "DOT" ou "DAT" → "DGT" (g lu comme o ou a par l'OCR)
        name_clean = re.sub(r"\bD[OA]T\b", "DGT", name_clean)
        # "Dgt" → "DGT" (minuscule t lu comme t par l'OCR), with or without trailing dot
        name_clean = re.sub(r"\bDgt\.?\b", "DGT", name_clean, flags=re.IGNORECASE)
        # "PI" → "PL" quand c'est un placard abrégé (l minuscule lu comme I majuscule)
        # Règle: PI seul, ou après +/espace, suivi d'un point ou fin de mot
        # Exclure: PIECE, PLAN, PISCINE, etc. (PI suivi d'une lettre autre que . ou fin)
        name_clean = re.sub(r"(?<=[\s\+])PI(?=[\.,\s]|$)", "PL", name_clean)
        name_clean = re.sub(r"\+PI(?=[\.,\s]|$)", "+PL", name_clean)
        # "CHAMBRE1+PL" → "CHAMBRE 1 + PL" (normaliser les espaces autour du +)
        name_clean = re.sub(r"(CHAMBRE\d+)\+", r"\1 + ", name_clean)

        # Normaliser les espaces
        name_clean = re.sub(r"\s+", " ", name_clean).strip()

        for pattern, name_template, room_type, is_exterior in self.ROOM_ALIASES:
            match = re.match(pattern, name_clean, re.IGNORECASE)
            if match:
                # Extraire numéro si présent
                number = None
                for g in match.groups():
                    if g and g.isdigit():
                        number = int(g)
                        break

                # Construire nom normalisé
                if "{n}" in name_template:
                    if number:
                        norm_name = name_template.replace("{n}", str(number))
                    else:
                        # Remove both _{n} and {n}
                        norm_name = name_template.replace("_{n}", "").replace("{n}", "")
                else:
                    norm_name = name_template

                # Dédoublonnage
                norm_name = self._deduplicate(norm_name)
                confidence = 0.95 if number or len(name_clean) > 3 else 0.8
                return norm_name, room_type, number, is_exterior, confidence

        logger.warning(f"Pièce non reconnue: '{name_raw}'")
        return None, None, None, False, 0.0

    def _deduplicate(self, name: str) -> str:
        """Si 'sejour' existe déjà, retourne 'sejour_2'"""
        if name not in self._seen_names:
            self._seen_names[name] = 1
            return name
        self._seen_names[name] += 1
        return f"{name}_{self._seen_names[name]}"
