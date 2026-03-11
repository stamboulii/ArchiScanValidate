"""
Models - Dataclasses et enums partagés
"""

from typing import Dict, Optional, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum


class RoomType(Enum):
    ENTRY = "entree"
    LIVING_ROOM = "sejour"
    KITCHEN = "cuisine"
    LIVING_KITCHEN = "sejour_cuisine"
    RECEPTION = "reception"
    BEDROOM = "chambre"
    BATHROOM = "salle_de_bain"
    SHOWER_ROOM = "salle_d_eau"
    WC = "wc"
    CIRCULATION = "circulation"
    STORAGE = "storage"
    DRESSING = "dressing"
    BALCONY = "balcon"
    TERRACE = "terrasse"
    GARDEN = "jardin"
    LOGGIA = "loggia"
    PATIO = "patio"
    PARKING = "parking"
    CELLAR = "cave"
    UNKNOWN = "unknown"


EXTERIOR_ROOM_TYPES = {
    RoomType.BALCONY, RoomType.TERRACE, RoomType.GARDEN,
    RoomType.LOGGIA, RoomType.PATIO, RoomType.PARKING, RoomType.CELLAR,
}

SURFACE_RANGES = {
    RoomType.ENTRY: (1.0, 25.0),
    RoomType.LIVING_ROOM: (8.0, 80.0),
    RoomType.KITCHEN: (3.0, 40.0),
    RoomType.LIVING_KITCHEN: (15.0, 100.0),
    RoomType.RECEPTION: (15.0, 120.0),
    RoomType.BEDROOM: (5.0, 40.0),
    RoomType.BATHROOM: (2.0, 20.0),
    RoomType.SHOWER_ROOM: (2.0, 15.0),
    RoomType.WC: (0.8, 10.0),
    RoomType.CIRCULATION: (1.0, 20.0),
    RoomType.STORAGE: (0.5, 15.0),
    RoomType.DRESSING: (1.0, 20.0),
    RoomType.BALCONY: (1.0, 70.0),
    RoomType.TERRACE: (2.0, 200.0),
    RoomType.GARDEN: (5.0, 5000.0),
    RoomType.LOGGIA: (2.0, 30.0),
    RoomType.PARKING: (8.0, 40.0),
    RoomType.CELLAR: (2.0, 50.0),
}


@dataclass
class ExtractedRoom:
    name_raw: str
    name_normalized: str
    surface: float
    room_type: RoomType
    is_exterior: bool = False
    is_composite: bool = False
    room_number: Optional[int] = None
    source: str = "unknown"
    confidence: float = 1.0
    bbox: Optional[Tuple[float, float, float, float]] = None
    children: List[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    reference: str = ""
    parcel_label: str = ""  # Label du lot (ex: "M011")
    reference_valid: bool = True  # Validation: does reference exist in PDF text?
    page_number: int = 0  # Numéro de page d'où vient l'extraction
    property_type: str = "appartment"
    property_type_hint: str = ""  # Hint from metadata (e.g., "magasin" from MAGASIN reference)
    typology: str = ""
    floor: str = ""
    building: str = ""
    program_name: str = ""
    address: str = ""
    living_space: float = 0.0
    annex_space: float = 0.0
    # Nouveaux champs pour maisons
    surface_propriete: float = 0.0
    surface_espaces_verts: float = 0.0
    # Fields to store option detection from raw text parsing
    has_terrace_detected: bool = False  # Terrasse detected in raw text
    has_balcony_detected: bool = False  # Balcon detected in raw text
    niveaux: List[str] = field(default_factory=list)
    rooms: List[ExtractedRoom] = field(default_factory=list)
    composites: Dict[str, List[str]] = field(default_factory=dict)
    validation_errors: List[str] = field(default_factory=list)
    validation_warnings: List[str] = field(default_factory=list)
    sources: Dict[str, str] = field(default_factory=dict)
    raw_text: str = ""
    floor_results: "List[Any]" = field(default_factory=list)  # pour duplex/maison multi-niveaux
    promoter_detected: str = ""

    @property
    def interior_rooms(self) -> List[ExtractedRoom]:
        """Pièces intérieures hors composites (évite double-comptage)"""
        return [r for r in self.rooms if not r.is_exterior and not r.is_composite]

    @property
    def exterior_rooms(self) -> List[ExtractedRoom]:
        return [r for r in self.rooms if r.is_exterior]

    @property
    def interior_surface_calc(self) -> float:
        return round(sum(r.surface for r in self.interior_rooms), 2)

    @property
    def annex_surface_calc(self) -> float:
        return round(sum(r.surface for r in self.exterior_rooms), 2)

    def _extract_exterior_surface_from_raw(self, surface_type: str, raw_text: str) -> float:
        """Extract exterior surface (balcony/terrace) from raw text using regex."""
        if not raw_text:
            return 0.0
        import re
        # Pattern to find "Balcon" or "Terrasse" followed by a surface value
        # Handles formats like "Balcon 15.99 m²", "Balcon 15,99m²", "Balcon : 15.99"
        # Also handles multi-line format where name and surface are on separate lines
        patterns = [
            rf'{surface_type}\s*:\s*(\d+[,\.]\d+)\s*m?²?',  # "Balcon: 15.99 m²"
            rf'{surface_type}\s+(\d+[,\.]\d+)\s*m?²?',        # "Balcon 15.99 m²"
            rf'{surface_type}\s+[A-Za-z0-9_-]+\s*(\d+[,\.]\d+)',  # "Balcon A011 15.99"
            # Multi-line patterns (name on one line, surface on next)
            rf'{surface_type}\s*\n\s*(\d+[,\.]\d+)\s*m?²?',  # "Balcon\n15.99 m²"
            rf'{surface_type}\s*\r\n\s*(\d+[,\.]\d+)\s*m?²?',  # "Balcon\r\n15.99 m²"
        ]
        # Find ALL matches and return the maximum value
        all_surfaces = []
        for pattern in patterns:
            matches = re.finditer(pattern, raw_text, re.IGNORECASE | re.DOTALL)
            for match in matches:
                try:
                    surface_val = float(match.group(1).replace(',', '.'))
                    all_surfaces.append(surface_val)
                except ValueError:
                    pass
        if all_surfaces:
            return max(all_surfaces)
        return 0.0

    def to_legacy_format(self, include_raw_text: bool = False) -> Dict[str, Any]:
        import logging
        logger = logging.getLogger(__name__)
        
        surface_detail = {r.name_normalized: r.surface for r in self.rooms}
        
        # Determine options - set to true if room type exists OR detected from raw text
        has_garden  = (
            any(r.room_type == RoomType.GARDEN for r in self.rooms)
            or self.surface_espaces_verts > 0  # fallback: détecté dans les métadonnées
        )
        # Check for balcony/terrace room type existence OR detection from raw text
        has_balcony = any(r.room_type == RoomType.BALCONY for r in self.rooms) or self.has_balcony_detected
        has_terrace = any(r.room_type == RoomType.TERRACE for r in self.rooms) or self.has_terrace_detected
        has_loggia = any(r.room_type == RoomType.LOGGIA for r in self.rooms)
        has_duplex = False
        # Only detect duplex for apartments (not commercial spaces like MAGASIN)
        if self.property_type != 'magasin' and self.property_type != 'commercial':
            # Duplex detection: check if floor has multiple levels (R+X pattern) or if there's a clear duplex indicator
            if self.floor:
                floor_upper = self.floor.upper()
                # Check for patterns like R+1+R+2, or duplex in floor name
                if 'DUPLEX' in floor_upper or 'DUPLEX' in (self.raw_text or '').upper():
                    has_duplex = True
                # Check for multi-level pattern (R+1+R+2)
                elif '+' in floor_upper and 'R+' in floor_upper:
                    has_duplex = True
            # Also check if there are multiple floor surfaces in multi_floor_surfaces
            if not has_duplex and hasattr(self, 'multi_floor_surfaces') and self.multi_floor_surfaces:
                if len(self.multi_floor_surfaces) > 1:
                    has_duplex = True
        
        # Fallback: detect terrace and balcony from raw text ONLY if not found in rooms
        # Also check for exterior spaces like "Terrasse", "Balcon", "Jardin", "Exterieur", "Loggia"
        # Set to true if room type exists (regardless of surface value)
        if self.raw_text:
            raw_lower = self.raw_text.lower()
            # Check for terrace - only if no actual terrace room found AND text has meaningful terrace info
            if not has_terrace:
                # Check for any terrace room (surface can be 0)
                terrace_rooms = [r for r in self.rooms if r.room_type == RoomType.TERRACE]
                has_terrace = len(terrace_rooms) > 0
            # Check for balcony - only if no actual balcony room found
            if not has_balcony:
                # Also check raw text for balcony keyword (even without surface)
                if self.raw_text and 'balcon' in self.raw_text.lower():
                    has_balcony = True
                else:
                    balcony_rooms = [r for r in self.rooms if r.room_type == RoomType.BALCONY]
                    has_balcony = len(balcony_rooms) > 0
            # Check for loggia - only if no actual loggia room found
            if not has_loggia:
                loggia_rooms = [r for r in self.rooms if r.room_type == RoomType.LOGGIA]
                has_loggia = len(loggia_rooms) > 0
            # Check for garden - only if no actual garden room found
            if not has_garden:
                garden_rooms = [r for r in self.rooms if r.room_type == RoomType.GARDEN]
                has_garden = len(garden_rooms) > 0 or self.surface_espaces_verts > 0
            # Check for exterior (balcony/terrace indicator) - only if no actual exterior found
            # Set to true if any exterior room type exists (regardless of surface value)
            if not has_balcony and not has_terrace:
                # Check for any exterior rooms
                has_balcony = self.annex_surface_calc > 0
        has_loggia = has_loggia or any(r.room_type == RoomType.LOGGIA for r in self.rooms)
        # Parking pur: type PARKING dont le nom normalisé NE contient PAS 'garage'
        has_parking = any(
            r.room_type == RoomType.PARKING and "garage" not in r.name_normalized.lower()
            for r in self.rooms
        )
        # Garage: type PARKING dont le nom normalisé contient 'garage' (ou 'box')
        # OU cave/cellar (il arrive que le garage soit classé en cave pour les maisons)
        has_garage = (
            any(
                r.room_type == RoomType.PARKING
                and ("garage" in r.name_normalized.lower() or "box" in r.name_normalized.lower())
                for r in self.rooms
            )
            or any(r.room_type == RoomType.CELLAR for r in self.rooms)
        )
        
        # Add option surfaces to surface_detail when option is true (only if not already present)
        # Include even if no surface found - to show the option exists
        if has_garden and "garden" not in surface_detail:
            garden_surfaces = [r.surface for r in self.rooms if r.room_type == RoomType.GARDEN]
            if garden_surfaces:
                surface_detail["garden"] = max(garden_surfaces)
            elif self.surface_espaces_verts > 0:
                surface_detail["garden"] = self.surface_espaces_verts
            else:
                # Include even with no surface to indicate option exists
                surface_detail["garden"] = 0
        if has_balcony and "balcony" not in surface_detail:
            balcony_surfaces = [r.surface for r in self.rooms if r.room_type == RoomType.BALCONY]
            if balcony_surfaces:
                surface_detail["balcony"] = max(balcony_surfaces)
            else:
                # Try to extract balcony surface from raw text
                balcony_surface = self._extract_exterior_surface_from_raw('balcon', self.raw_text)
                if balcony_surface:
                    surface_detail["balcony"] = balcony_surface
                else:
                    # Include even with no surface to indicate option exists
                    surface_detail["balcony"] = 0
        if has_terrace and "terrace" not in surface_detail and not any(k.lower().startswith("terrasse") for k in surface_detail):
            terrace_surfaces = [r.surface for r in self.rooms if r.room_type == RoomType.TERRACE]
            if terrace_surfaces:
                surface_detail["terrace"] = max(terrace_surfaces)
            else:
                # Try to extract terrace surface from raw text
                terrace_surface = self._extract_exterior_surface_from_raw('terrasse', self.raw_text)
                if terrace_surface:
                    surface_detail["terrace"] = terrace_surface
                else:
                    # Include even with no surface to indicate option exists
                    surface_detail["terrace"] = 0
        if has_loggia and "loggia" not in surface_detail:
            loggia_surfaces = [r.surface for r in self.rooms if r.room_type == RoomType.LOGGIA]
            if loggia_surfaces:
                surface_detail["loggia"] = max(loggia_surfaces)
            else:
                # Include even with no surface to indicate option exists
                surface_detail["loggia"] = 0
        if has_parking and "parking" not in surface_detail:
            parking_surfaces = [r.surface for r in self.rooms if r.room_type == RoomType.PARKING]
            if parking_surfaces:
                surface_detail["parking"] = max(parking_surfaces)
            else:
                # Include even with no surface to indicate option exists
                surface_detail["parking"] = 0
        if has_garage and "garage" not in surface_detail:
            garage_surfaces = [r.surface for r in self.rooms if r.room_type in (RoomType.PARKING, RoomType.CELLAR)]
            if garage_surfaces:
                surface_detail["garage"] = max(garage_surfaces)
            else:
                # Include even with no surface to indicate option exists
                surface_detail["garage"] = 0
        
        result = {
            self.reference: {
                "parcelTypeId": self.property_type,
                "parcelTypeLabel": self._property_label(),
                "typology": self.typology,
                "floor": self.floor,
                "building": self.building,
                "orientation": "",
                "price": "N.C",
                "living_space": str(self.living_space) if self.living_space else str(self.interior_surface_calc),
                "annex_space": str(self.annex_space if self.annex_space else self.annex_surface_calc),
                "surfaceDetail": surface_detail,
                "surfaceComposites": self.composites,
                "multi_floor_surfaces": getattr(self, 'multi_floor_surfaces', {}),  # Add floor surfaces
                "surfaceTotals": {
                    "habitable": self.living_space if self.living_space else self.interior_surface_calc,
                    "habitable_calc": self.interior_surface_calc,
                    "annexe": self.annex_space if self.annex_space else self.annex_surface_calc,
                    "annexe_calc": self.annex_surface_calc,
                },
                "option": {
                    "balcony": has_balcony,
                    "terrace": has_terrace,
                    "garden": has_garden,
                    "loggia": has_loggia,
                    "duplex": has_duplex,
                    "parking": has_parking,
                    "garage": has_garage,
                },
                "tva": "",
                "pinel": "",
                "state": "available",
                "customData": {
                    "sources": self.sources,
                    "method": "super_extractor_v3",
                    "promoter": self.promoter_detected,
                    "address": self.address,
                    "program": self.program_name,
                    # Nouveaux champs pour maisons
                    "surface_propriete_totale": self.surface_propriete,
                    "surface_espaces_verts": self.surface_espaces_verts,
                    "niveaux": self.niveaux if self.niveaux else self._floor_to_niveaux(self.floor),
                },
                "pageNumber": self.page_number,
                "parcelLabel": self.parcel_label or self.reference,
                "_validation": {
                    "is_valid": len(self.validation_errors) == 0,
                    "reference_valid": self.reference_valid,
                    "errors": self.validation_errors,
                    "warnings": self.validation_warnings,
                },
            }
        }
        
        # Debug: afficher la cle et la presence de _validation
        logger.info(f"to_legacy_format: reference={self.reference}, has_validation={'_validation' in result[self.reference]}")
        
        # Ajouter le texte brut seulement si demande
        if include_raw_text and self.raw_text:
            result[self.reference]['_raw_text'] = self.raw_text

        # Si multi-niveaux (duplex/maison): imbriquer les résultats par étage
        if self.floor_results:
            nested = {}
            for floor_result in self.floor_results:
                floor_legacy = floor_result.to_legacy_format(include_raw_text=False)
                # floor_legacy = {"A18": {...}} → on veut {"A18_R+1": {...}}
                floor_key = f"{floor_result.reference}_{floor_result.floor}"
                inner = list(floor_legacy.values())[0]
                inner["floor_key"] = floor_key
                nested[floor_key] = inner
            # Remplacer le contenu par la structure imbriquée
            result[self.reference] = nested

        return result
    
    def _floor_to_niveaux(self, floor: str) -> List[str]:
        """Convertit le floor string en liste de niveaux."""
        if not floor:
            return []
        floor_upper = floor.upper()
        niveaux = []
        if "RDC" in floor_upper or floor == "RDC":
            niveaux.append("Rez-de-chaussée")
        # Chercher les etages (R+1, R+2, etc.)
        import re
        for match in re.finditer(r"R\+(\d+)", floor_upper):
            niveau = int(match.group(1))
            if niveau == 1:
                niveaux.append("Étage")
            else:
                niveaux.append(f"{niveau}e étage")
        return niveaux

    def _property_label(self) -> str:
        # Always return "Appartement" for both house and apartment types
        # Also update parcelTypeId to "appartment" to match
        if self.property_type in ("appartment", "house"):
            self.property_type = "appartment"  # Update to standard value
            return "appartement"
        return {"commercial": "Commerce", "magasin": "Magasin", "office": "Bureau"
                }.get(self.property_type, "appartement")