"""
Deduplication utilities for SuperExtractor.
Handles room deduplication and filtering.
"""

import logging
from typing import List, Optional
from itertools import combinations
from math import comb

from .models import RoomType, ExtractedRoom, ExtractionResult

logger = logging.getLogger(__name__)


class DeduplicationUtils:
    """Utilities for room deduplication and filtering."""
    
    def __init__(self):
        pass
    
    def final_dedup(self, rooms: List[ExtractedRoom]) -> List[ExtractedRoom]:
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
        
        # Debug: Log rooms before pass 3
        logger.info(f"  📋 After Pass 2: {[f'{r.name_normalized}({r.surface})' for r in deduped]}")

        if len(deduped) < len(rooms):
            logger.info(
                f"  🧹 Dedoublonnage: {len(rooms)} → {len(deduped)} pieces"
            )
        
        # Pass 3: Remove duplicate bedrooms (same surface, different room number suffix)
        bedrooms = [r for r in deduped if r.room_type == RoomType.BEDROOM]
        non_bedrooms = [r for r in deduped if r.room_type != RoomType.BEDROOM]
        
        if len(bedrooms) > 3:
            # Find duplicate bedrooms by surface (within 0.1 m2 tolerance)
            bedroom_surfaces = {}
            for b in bedrooms:
                surf = round(b.surface, 1)
                if surf not in bedroom_surfaces:
                    bedroom_surfaces[surf] = b
                else:
                    # Keep the one with simpler name (e.g., 'chambre_1' over 'chambre_1_2')
                    existing = bedroom_surfaces[surf]
                    if '_' in b.name_normalized and '_' not in existing.name_normalized:
                        bedroom_surfaces[surf] = b
            deduped = list(bedroom_surfaces.values()) + non_bedrooms
            logger.info(f"  🔄 Removed duplicate bedrooms: {len(bedrooms) - len(bedroom_surfaces)}")
        
        return deduped
    
    def remove_false_duplicates(self, rooms: List[ExtractedRoom]) -> List[ExtractedRoom]:
        """
        Remove false duplicates based on surface matching.
        If 'placard' has the same surface as 'salle_de_bain', remove the placard.
        """
        if not rooms:
            return rooms
        
        sdb_surfaces = {round(r.surface, 2) for r in rooms if 'salle_de_bain' in r.name_normalized}
        
        if not sdb_surfaces:
            return rooms
        
        filtered = [r for r in rooms 
                   if not (r.name_normalized == 'placard' and round(r.surface, 2) in sdb_surfaces)]
        
        if len(filtered) < len(rooms):
            logger.info(f"  🔧 Supprimé {len(rooms) - len(filtered)} doublons faux: placard avec même surface que salle_de_bain")
        
        return filtered
    
    def filter_by_reference(self, rooms: List[ExtractedRoom], reference: str, living_space: float) -> List[ExtractedRoom]:
        """Filter rooms based on declared living space."""
        if living_space <= 0 or len(rooms) < 3:
            return rooms

        interior = [r for r in rooms if not r.is_exterior and not r.is_composite]
        exterior = [r for r in rooms if r.is_exterior]

        calc = sum(r.surface for r in interior)
        diff = abs(calc - living_space)
        
        rooms = self.remove_false_duplicates(rooms)
        interior = [r for r in rooms if not r.is_exterior and not r.is_composite]
        exterior = [r for r in rooms if r.is_exterior]
        calc = sum(r.surface for r in interior)
        diff = abs(calc - living_space)

        if diff <= 1.0:
            return rooms

        # Skip multi-apartment filtering - it removes too many rooms
        return rooms

        # Original filtering disabled:
        essential_types = {"wc", "salle_de_bain", "salle_d_eau", "entree", "circulation", "storage", "cuisine", "reception",
                           "salle_de_bain_2", "salle_d_eau_2", "salle_de_bain_3",
                           "chambre", "chambre_1", "chambre_2", "chambre_3", "chambre_4",
                           "chambre_1_2", "chambre_2_2", "chambre_3_2"}
        essential_rooms = [r for r in interior if r.name_normalized.split("_")[0] in essential_types]
        
        spatial_rooms = {r.name_normalized for r in rooms if r.source == "spatial"}
        
        if calc > living_space * 1.10:
            logger.info(
                f"  🔍 Multi-appart détecté: calc={calc:.2f} >> "
                f"declared={living_space:.2f}. Filtrage..."
            )
            
            if spatial_rooms:
                spatial_priority = []
                other_rooms = []
                for r in interior:
                    base_name = r.name_normalized.split("_")[0]
                    if any(spatial_name.split("_")[0] == base_name for spatial_name in spatial_rooms):
                        spatial_priority.append(r)
                    else:
                        other_rooms.append(r)
                
                best = self.find_best_subset(spatial_priority + other_rooms, living_space)
            else:
                best = self.find_best_subset(interior, living_space)
                
            if best:
                essential_in_best = {r.name_normalized.split("_")[0] for r in best}
                missing_essential = [r for r in essential_rooms 
                                   if r.name_normalized.split("_")[0] not in essential_in_best]
                
                if missing_essential:
                    logger.info(f"  🔧 Ajout {len(missing_essential)} pièces essentielles: "
                              f"{[r.name_normalized for r in missing_essential]}")
                    best = best + missing_essential
                
                by_type_num = {}
                for r in best:
                    key = r.name_normalized
                    if key not in by_type_num:
                        by_type_num[key] = r
                    else:
                        existing = by_type_num[key]
                        if r.source == "spatial" and existing.source != "spatial":
                            by_type_num[key] = r
                        elif r.source == existing.source and r.surface > existing.surface:
                            by_type_num[key] = r
                best = list(by_type_num.values())
                
                sdb_surfaces = {round(r.surface, 2) for r in best if 'salle_de_bain' in r.name_normalized}
                if sdb_surfaces:
                    best = [r for r in best 
                            if not (r.name_normalized == 'placard' and round(r.surface, 2) in sdb_surfaces)]
                
                ext_by_type = {}
                for r in exterior:
                    base = r.name_normalized.split("_")[0]
                    if base not in ext_by_type or r.surface > ext_by_type[base].surface:
                        ext_by_type[base] = r
                exterior = list(ext_by_type.values())
                
                best = self.remove_false_duplicates(best)
                
                result = best + exterior
                logger.info(
                    f"  ✅ Filtré: {len(rooms)} → {len(result)} pièces"
                )
                return result

        return rooms
    
    def filter_exteriors(self, exterior_rooms: List[ExtractedRoom]) -> List[ExtractedRoom]:
        """Dédoublonne les extérieurs: garde 1 par type (le plus grand)"""
        by_type = {}
        for r in exterior_rooms:
            if r.room_type not in by_type or r.surface > by_type[r.room_type].surface:
                by_type[r.room_type] = r
        return list(by_type.values())

    def find_best_subset(self, rooms, target: float) -> Optional[List[ExtractedRoom]]:
        """
        Trouve le sous-ensemble cohérent dont la somme ≈ target.
        Priorise la cohérence (pas de doublons de type) avant la somme.
        """
        n = len(rooms)
        best_diff = float("inf")
        best_combo = None

        min_size = max(3, n // 2)
        max_size = min(n, n - 1) if n > 3 else n

        for size in range(min_size, max_size + 1):
            if self._comb_count(n, size) > 50000:
                continue

            for combo in combinations(rooms, size):
                total = sum(r.surface for r in combo)
                diff = abs(total - target)

                if diff >= best_diff:
                    continue

                if self._has_type_conflict(combo):
                    continue

                best_diff = diff
                best_combo = list(combo)

                if diff < 0.5:
                    return best_combo

        if best_combo and best_diff < 2.0:
            return best_combo
        return None

    def _has_type_conflict(self, combo) -> bool:
        """Vérifie qu'un sous-ensemble est cohérent."""
        unique_types = [
            RoomType.LIVING_KITCHEN,
            RoomType.LIVING_ROOM,
            RoomType.ENTRY,
            RoomType.RECEPTION,
        ]
        for rt in unique_types:
            count = sum(1 for r in combo if r.room_type == rt)
            if count > 1:
                return True
        return False

    def _comb_count(self, n: int, r: int) -> int:
        """Nombre de combinaisons C(n,r)"""
        return comb(n, r)
