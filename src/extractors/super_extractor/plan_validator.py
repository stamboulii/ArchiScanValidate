"""
Plan Validator - Validation mathématique multi-niveau
"""

import logging
from .config import SUM_TOLERANCE_ERROR, SUM_TOLERANCE_WARNING
from .models import RoomType, SURFACE_RANGES, ExtractionResult

logger = logging.getLogger(__name__)


class PlanValidator:

    def validate(self, result: ExtractionResult) -> None:
        """Valide un ExtractionResult en place"""
        self._validate_surface_sum(result)
        self._validate_composites(result)
        self._validate_typology(result)
        self._validate_ranges(result)
        self._validate_basic(result)

    def _validate_surface_sum(self, result: ExtractionResult) -> None:
        if result.living_space <= 0:
            result.validation_warnings.append("Surface habitable non trouvée")
            return
        calc = result.interior_surface_calc
        diff = abs(calc - result.living_space)
        # Utiliser un pourcentage de la surface déclarée comme tolérance
        # 5% d'erreur = erreur, 2% = avertissement
        if result.living_space > 0:
            diff_pct = diff / result.living_space
            if diff_pct > SUM_TOLERANCE_ERROR:
                result.validation_errors.append(
                    f"Surface mismatch: calc={calc:.2f}m², "
                    f"declared={result.living_space:.2f}m², diff={diff:.2f}m² ({diff_pct*100:.1f}%)"
                )
            elif diff_pct > SUM_TOLERANCE_WARNING:
                result.validation_warnings.append(
                    f"Surface gap: {diff:.2f}m² ({diff_pct*100:.1f}%)"
                )

    def _validate_composites(self, result: ExtractionResult) -> None:
        for parent_name, child_names in result.composites.items():
            parent = next(
                (r for r in result.rooms if r.name_normalized == parent_name), None
            )
            if not parent:
                continue
            children_sum = sum(
                r.surface for r in result.rooms if r.name_normalized in child_names
            )
            diff = abs(parent.surface - children_sum)
            if diff > 1.0:
                result.validation_warnings.append(
                    f"Composite incohérent: {parent_name}={parent.surface:.2f} "
                    f"vs composants={children_sum:.2f}"
                )

    def _validate_typology(self, result: ExtractionResult) -> None:
        bedrooms = sum(1 for r in result.rooms if r.room_type == RoomType.BEDROOM)
        expected = f"T{bedrooms + 1}" if bedrooms > 0 else "Studio"
        if result.typology and result.typology != expected:
            result.validation_warnings.append(
                f"Typology: detected={result.typology}, expected={expected}"
            )

    def _validate_ranges(self, result: ExtractionResult) -> None:
        for room in result.rooms:
            if room.room_type in SURFACE_RANGES:
                mn, mx = SURFACE_RANGES[room.room_type]
                if room.surface < mn or room.surface > mx:
                    result.validation_warnings.append(
                        f"{room.name_normalized}: {room.surface}m² "
                        f"hors plage [{mn}-{mx}]"
                    )

    def _validate_basic(self, result: ExtractionResult) -> None:
        if not result.rooms:
            result.validation_errors.append("Aucune pièce détectée")