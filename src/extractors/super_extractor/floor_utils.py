"""
Floor utilities for SuperExtractor.
Handles floor normalization, floor splitting for multi-page PDFs.
"""

import re
import copy
from typing import Dict, List, Any


class FloorUtils:
    """Utilities for floor label normalization and multi-floor splitting."""
    
    def __init__(self, normalizer):
        self.normalizer = normalizer
    
    def normalize_floor_label(self, floor: str) -> str:
        """
        Normalize floor label: '001' -> 'R+1', '002' -> 'R+2', 'RDC' stays.
        Also handles comma-separated multi-floor values like 'RDC,R+1'.
        """
        if not floor:
            return ""
        
        # Handle comma-separated multi-floor values (e.g., 'RDC,R+1')
        if ',' in floor:
            floors = [self.normalize_floor_label(f.strip()) for f in floor.split(',')]
            floors = [f for f in floors if f]  # Remove empty
            if len(floors) > 1:
                return ",".join(floors)
            return floors[0] if floors else ""
        
        m = re.match(r'^0*(\d+)$', floor)
        if m:
            n = int(m.group(1))
            return f"R+{n}" if n > 0 else "RDC"
        return floor.strip()
    
    def build_floor_split(self, ref: str, page_results: list) -> dict:
        """
        From a list of pages for the same ref, build per-floor results.

        Returns:
            - {"A18_R+1": result1, "A18_R+2": result2} if distinct floors found
            - {} if all pages have the same floor (caller will combine)
        """
        # Group pages by normalized floor
        by_floor = {}
        for r in page_results:
            floor = self.normalize_floor_label(r.floor or "")
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

        declared_total = max((r.living_space for r in page_results if r.living_space > 0), default=0)
        declared_annexe = max((r.annex_space for r in page_results if r.annex_space > 0), default=0)

        split = {}
        assigned_norms = set()  # track which rooms have been assigned

        for floor in sorted(known.keys()):
            pages = known[floor]

            # Get floor plan labels for this floor
            # (room names printed on the drawing, not in the table)
            labels = self._get_floor_plan_labels(pages, all_rooms_by_norm)

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
            # Use declared living_space from page_results if available (>40), otherwise calculate
            # This preserves LOGEMENT value (87.79) over calculated room sum (84.99)
            # Lower threshold to >40 to handle smaller apartments (T2 can have 47m²)
            if declared_total > 40:
                base.living_space = declared_total
            else:
                # Calculate habitable as sum of NON-CIRCULATION interior rooms
                # (circulation like degagement/palier are not counted as habitable per French convention)
                # ENTRY rooms ARE counted in habitable surface
                base.living_space = round(
                    sum(r.surface for r in floor_rooms 
                        if not r.is_exterior and r.room_type.name not in ['CIRCULATION', 'HALL']), 2)
            # Annex = exterior spaces (jardin, porche, etc.)
            base.annex_space = round(
                sum(r.surface for r in floor_rooms if r.is_exterior), 2)
            base.typology = self._detect_typology(floor_rooms)
            base.validation_errors = []
            base.validation_warnings = []

            floor_key = f"{ref}_{floor}"
            split[floor_key] = base
            import logging
            logging.getLogger(__name__).info(
                f"    🏢 {floor_key}: {len(floor_rooms)} pièces, "
                f"habitable={base.living_space}m²")

        return split
    
    def _detect_typology(self, rooms):
        """Detect typology: T1, T2, T3, T4, T5, etc."""
        from .models import RoomType
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
    
    def _get_floor_plan_labels(self, pages: list, all_rooms_by_norm: dict) -> list:
        """
        Extract room names that appear as labels on the floor plan drawing.
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
            
            surface_positions = [i for i, l in enumerate(lines) if SURF_RE.match(l)]
            
            if not surface_positions:
                continue
            
            first_surf = surface_positions[0]
            
            table_name_start = first_surf
            for i in range(first_surf - 1, -1, -1):
                line = lines[i]
                if SURF_RE.match(line):
                    continue
                norm, rtype, _, _, _ = self.normalizer.normalize(line)
                if rtype and norm in all_rooms_by_norm:
                    table_name_start = i
                else:
                    break
            
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
