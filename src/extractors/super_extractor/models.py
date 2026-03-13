"""
Models - Dataclasses et enums partagés
"""

import re
import logging
from typing import Dict, Optional, Any, List, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


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
    parcel_label: str = ""
    reference_valid: bool = True
    page_number: int = 0
    property_type: str = "appartment"
    property_type_hint: str = ""
    typology: str = ""
    floor: str = ""
    building: str = ""
    program_name: str = ""
    address: str = ""
    living_space: float = 0.0
    annex_space: float = 0.0
    surface_propriete: float = 0.0
    surface_espaces_verts: float = 0.0
    has_terrace_detected: bool = False
    has_balcony_detected: bool = False
    niveaux: List[str] = field(default_factory=list)
    rooms: List[ExtractedRoom] = field(default_factory=list)
    composites: Dict[str, List[str]] = field(default_factory=dict)
    validation_errors: List[str] = field(default_factory=list)
    validation_warnings: List[str] = field(default_factory=list)
    sources: Dict[str, str] = field(default_factory=dict)
    raw_text: str = ""
    floor_results: "List[Any]" = field(default_factory=list)
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
        patterns = [
            rf'{surface_type}\s*:\s*(\d+[,\.]\d+)\s*m?²?',
            rf'{surface_type}\s+(\d+[,\.]\d+)\s*m?²?',
            rf'{surface_type}\s+[A-Za-z0-9_-]+\s*(\d+[,\.]\d+)',
            rf'{surface_type}\s*\n\s*(\d+[,\.]\d+)\s*m?²?',
            rf'{surface_type}\s*\r\n\s*(\d+[,\.]\d+)\s*m?²?',
        ]
        all_surfaces = []
        for pattern in patterns:
            for match in re.finditer(pattern, raw_text, re.IGNORECASE | re.DOTALL):
                try:
                    all_surfaces.append(float(match.group(1).replace(',', '.')))
                except ValueError:
                    pass
        return max(all_surfaces) if all_surfaces else 0.0

    def _extract_wc_surface_from_raw(self, raw_text: str) -> float:
        """Extract WC surface from raw text using regex."""
        if not raw_text:
            return 0.0
        patterns = [
            r'WC\s*:\s*(\d+[,\.]\d+)\s*m?²?',
            r'WC\s+(\d+[,\.]\d+)\s*m?²?',
            r'WC\s+[A-Za-z0-9_-]+\s*(\d+[,\.]\d+)',
        ]
        all_surfaces = []
        for pattern in patterns:
            for match in re.finditer(pattern, raw_text, re.IGNORECASE):
                try:
                    all_surfaces.append(float(match.group(1).replace(',', '.')))
                except ValueError:
                    pass
        return max(all_surfaces) if all_surfaces else 0.0

    # ----------------------------------------------------------------
    # Display key mapping: name_normalized base -> display label
    # Preserves all specificity from RoomNormalizer (buanderie, arriere_cuisine, etc.)
    # ----------------------------------------------------------------
    _NORM_TO_DISPLAY = {
        # Interior
        'sejour':           'SEJOUR',
        'sejour_cuisine':   'SEJOUR_CUISINE',
        'reception':        'RECEPTION',
        'cuisine':          'CUISINE',
        'arriere_cuisine':  'ARRIERE_CUISINE',
        'buanderie':        'BUANDERIE',
        'entree':           'ENTREE',
        'circulation':      'DEGAGEMENT',
        'palier':           'PALIER',
        'placard':          'PLACARD',
        'placard_escalier': 'PLACARD_ESCALIER',
        'storage':          'PLACARD',
        'dressing':         'DRESSING',
        'rangement':        'RANGEMENT',
        'salle_de_bain':    'SALLE_DE_BAIN',
        'salle_d_eau':      'SALLE_EAU',
        'wc':               'WC',
        'chambre':          'CHAMBRE',
        # Exterior
        'balcon':           'BALCON',
        'terrasse':         'TERRASSE',
        'garden':           'JARDIN',
        'loggia':           'LOGGIA',
        'patio':            'PATIO',
        'porche':           'PORCHE',
        'parking':          'PARKING',
        'garage':           'GARAGE',
        'cave':             'CAVE',
    }

    def _to_display_key(self, room: ExtractedRoom) -> str:
        """
        Convert name_normalized to display key, preserving numeric suffixes.

        Examples:
            'balcon_1'      -> 'BALCON_1'
            'balcon_2'      -> 'BALCON_2'
            'salle_d_eau_1' -> 'SALLE_EAU_1'
            'chambre_3'     -> 'CHAMBRE_3'
            'buanderie'     -> 'BUANDERIE'
            'arriere_cuisine' -> 'ARRIERE_CUISINE'
        """
        name = room.name_normalized or ''

        # Extract trailing _N suffix (e.g. '_1', '_2')
        m = re.search(r'_(\d+)$', name)
        suffix = f'_{m.group(1)}' if m else ''
        base   = name[:m.start()] if m else name

        display_base = self._NORM_TO_DISPLAY.get(base, base.upper())
        return f'{display_base}{suffix}'

    def to_legacy_format(self, include_raw_text: bool = False) -> Dict[str, Any]:

        surface_detail = {}

        # ----------------------------------------------------------------
        # Step 1: Build surface_detail from name_normalized.
        # Trust RoomNormalizer's output — it already handles numbering
        # (balcon_1, balcon_2, salle_d_eau_1, chambre_2 …).
        # Keep the largest surface on key collision.
        # ----------------------------------------------------------------
        for room in self.rooms:
            key     = self._to_display_key(room)
            surface = room.surface or 0.0

            # Surface range guard: reject values outside known bounds for this room type.
            # Catches spatial extractor mis-pairs (e.g. balcony grabbing living_space total).
            if surface > 0 and room.room_type in SURFACE_RANGES:
                lo, hi = SURFACE_RANGES[room.room_type]
                if surface > hi:
                    logger.warning(
                        f"Surface hors-plage ignorée: {room.name_normalized} "
                        f"{surface}m² (max {hi}m²)"
                    )
                    surface = 0.0

            if surface > 0 and (key not in surface_detail or surface > surface_detail[key]):
                surface_detail[key] = surface

        # ----------------------------------------------------------------
        # Step 2: WC sanity check — raw-text override when value is wrong
        # ----------------------------------------------------------------
        wc_surface = self._extract_wc_surface_from_raw(self.raw_text)
        if wc_surface > 0 and 'WC' in surface_detail:
            if surface_detail['WC'] > 4 and wc_surface < surface_detail['WC']:
                surface_detail['WC'] = wc_surface

        # ----------------------------------------------------------------
        # Step 3: Totals (no SURFACE prefix — extract_cli adds it)
        # ----------------------------------------------------------------
        habitable = self.living_space or self.interior_surface_calc
        annexe    = self.annex_space  or self.annex_surface_calc
        if habitable > 0:
            surface_detail['TOTAL_HABITABLE'] = round(habitable, 2)
        if annexe > 0:
            surface_detail['TOTAL_ANNEXE'] = round(annexe, 2)

        # ----------------------------------------------------------------
        # Step 4: Options — derived directly from rooms, no overrides
        # ----------------------------------------------------------------
        has_balcony = (
            any(r.room_type == RoomType.BALCONY for r in self.rooms)
            or self.has_balcony_detected
        )
        has_terrace = (
            any(r.room_type == RoomType.TERRACE for r in self.rooms)
            or self.has_terrace_detected
        )
        has_garden = (
            any(r.room_type == RoomType.GARDEN for r in self.rooms)
            or self.surface_espaces_verts > 0
        )
        has_loggia = any(r.room_type == RoomType.LOGGIA for r in self.rooms)
        has_parking = any(
            r.room_type == RoomType.PARKING
            and 'garage' not in r.name_normalized.lower()
            for r in self.rooms
        )
        has_garage = any(
            r.room_type == RoomType.PARKING
            and (
                'garage' in r.name_normalized.lower()
                or 'box' in r.name_normalized.lower()
            )
            for r in self.rooms
        )

        has_duplex = False
        if self.property_type not in ('magasin', 'commercial'):
            if self.floor:
                fu = self.floor.upper()
                has_duplex = (
                    'DUPLEX' in fu
                    or ('+' in fu and fu.count('R+') > 1)
                )
            if not has_duplex and self.raw_text:
                has_duplex = 'duplex' in self.raw_text.lower()

        # ----------------------------------------------------------------
        # Step 5: Build result dict
        # ----------------------------------------------------------------
        result = {
            self.reference: {
                "parcelTypeId":    self.property_type,
                "parcelTypeLabel": self._property_label(),
                "typology":        self.typology,
                "floor":           self.floor,
                "building":        self.building,
                "orientation":     "",
                "price":           "N.C",
                "living_space":    (
                    str(self.living_space)
                    if self.living_space
                    else str(self.interior_surface_calc)
                ),
                "annex_space": str(
                    self.annex_space if self.annex_space else self.annex_surface_calc
                ),
                "surfaceDetail":    surface_detail,
                "surfaceComposites": self.composites,
                "multi_floor_surfaces": getattr(self, 'multi_floor_surfaces', {}),
                "surfaceTotals": {
                    "habitable":      self.living_space or self.interior_surface_calc,
                    "habitable_calc": self.interior_surface_calc,
                    "annexe":         self.annex_space or self.annex_surface_calc,
                    "annexe_calc":    self.annex_surface_calc,
                },
                "option": {
                    "balcony":       has_balcony,
                    "terrace":       has_terrace,
                    "garden":        has_garden,
                    "loggia":        has_loggia,
                    "duplex":        has_duplex,
                    "parking":       has_parking,
                    "garage":        has_garage,
                },
                "tva":    "",
                "pinel":  "",
                "state":  "available",
                "customData": {
                    "sources":  self.sources,
                    "method":   "super_extractor_v3",
                    "promoter": self.promoter_detected,
                    "address":  self.address,
                    "program":  self.program_name,
                    "surface_propriete_totale": self.surface_propriete,
                    "surface_espaces_verts":    self.surface_espaces_verts,
                    "niveaux": (
                        self.niveaux
                        if self.niveaux
                        else self._floor_to_niveaux(self.floor)
                    ),
                },
                "pageNumber":  self.page_number,
                "parcelLabel": self.parcel_label or self.reference,
                "_validation": {
                    "is_valid":        len(self.validation_errors) == 0,
                    "reference_valid": self.reference_valid,
                    "errors":          self.validation_errors,
                    "warnings":        self.validation_warnings,
                },
            }
        }

        logger.info(
            f"to_legacy_format: reference={self.reference}, "
            f"has_validation={'_validation' in result[self.reference]}"
        )

        if include_raw_text and self.raw_text:
            result[self.reference]['_raw_text'] = self.raw_text

        # ----------------------------------------------------------------
        # Step 6: Multi-floor (duplex/maison) — nest per-floor results
        # ----------------------------------------------------------------
        if self.floor_results:
            nested = {}
            for floor_result in self.floor_results:
                floor_legacy = floor_result.to_legacy_format(include_raw_text=False)
                floor_key    = f"{floor_result.reference}_{floor_result.floor}"
                inner        = list(floor_legacy.values())[0]
                inner["floor_key"] = floor_key
                nested[floor_key]  = inner
            result[self.reference] = nested

        return result

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    def _floor_to_niveaux(self, floor: str) -> List[str]:
        """Convertit le floor string en liste de niveaux."""
        if not floor:
            return []
        floor_upper = floor.upper()
        niveaux = []
        if "RDC" in floor_upper or floor == "RDC":
            niveaux.append("Rez-de-chaussée")
        for match in re.finditer(r"R\+(\d+)", floor_upper):
            niveau = int(match.group(1))
            niveaux.append("Étage" if niveau == 1 else f"{niveau}e étage")
        return niveaux

    def _property_label(self) -> str:
        if self.property_type in ("appartment", "house"):
            self.property_type = "appartment"
            return "appartement"
        return {
            "commercial": "Commerce",
            "magasin":    "Magasin",
            "office":     "Bureau",
        }.get(self.property_type, "appartement")