"""
Room inference for SuperExtractor.
Handles inference of missing rooms based on surface gaps.
"""

import logging
from typing import List

from .models import RoomType, ExtractedRoom, ExtractionResult

logger = logging.getLogger(__name__)


class RoomInference:
    """Methods for inferring missing rooms."""
    
    def __init__(self):
        pass
    
    def infer_missing_living_room(self, result: ExtractionResult) -> ExtractionResult:
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
    
    def infer_missing_bedroom(self, result: ExtractionResult) -> ExtractionResult:
        """
        If OCR missed a bedroom, infer from surface gap.
        
        Rules (all must hold):
        - declared living_space > 0
        - gap is in bedroom range [5.0, 40.0] m²
        - we have at least one bedroom already
        - we do NOT already have a chambre_2 (or higher matching the gap)
        - gap matches no other room type already present
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

        # Make sure gap doesn't match a surface already present
        existing_surfaces = {round(r.surface, 2) for r in interior}
        if gap in existing_surfaces:
            return result
        
        # Don't infer bedroom if gap is too large (>15m²) - likely multiple rooms combined
        if gap > 15.0:
            logger.info(f"  ⏭️ Skip inference: gap={gap}m² too large for single bedroom")
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
    
    def detect_typology(self, rooms: List[ExtractedRoom]) -> str:
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
        return f"T{bedrooms + 1}"
    
    def detect_property_type(self, rooms: List[ExtractedRoom], floor: str = "") -> str:
        """Detect if property is a house or apartment."""
        has_garden = any(r.room_type == RoomType.GARDEN for r in rooms)
        has_cellar = any(r.room_type == RoomType.CELLAR for r in rooms)
        has_parking = any(r.room_type == RoomType.PARKING for r in rooms)
        
        is_multi_floor = floor and ("+" in floor or "R+1" in floor.upper() or "ETAGE" in floor.upper())
        
        return "house" if (has_garden or has_cellar or has_parking or is_multi_floor) else "appartment"
