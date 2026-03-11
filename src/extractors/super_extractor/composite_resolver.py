"""
Composite Resolver - Détecte et résout:
  Réception 27.31 = Séjour 20.45 + Cuisine 6.86
"""

import logging
from typing import List, Dict, Tuple
from .config import COMPOSITE_TOLERANCE

logger = logging.getLogger(__name__)


class CompositeResolver:

    # (parent_base_name, [child_base_names], description)
    COMPOSITE_RULES = [
        ("reception", ["sejour", "cuisine"], "Réception = Séjour + Cuisine"),
        ("reception", ["sejour_cuisine"], "Réception = Séjour/Cuisine"),
        ("sejour_cuisine", ["sejour", "cuisine"], "Séjour/Cuisine = Séjour + Cuisine"),
    ]

    def resolve(self, rooms: List) -> Tuple[List, Dict[str, List[str]]]:
        """
        Args:
            rooms: Liste d'ExtractedRoom
        Returns:
            (rooms_updated, composites_dict)
        """
        composites = {}

        # Index par nom de base (sans suffixe numéro)
        rooms_by_base = {}
        for r in rooms:
            base = r.name_normalized.split("_")[0] if "_" in r.name_normalized else r.name_normalized
            rooms_by_base.setdefault(base, []).append(r)

        for parent_type, child_types, desc in self.COMPOSITE_RULES:
            if parent_type not in rooms_by_base:
                continue

            for parent in rooms_by_base[parent_type]:
                children = []
                children_sum = 0.0

                for ct in child_types:
                    for child in rooms_by_base.get(ct, []):
                        # Only include if child name has no suffix OR suffix is not a number
                        # This prevents "sejour_2" from matching as child of "reception"
                        if "_" not in child.name_normalized:
                            children.append(child)
                            children_sum += child.surface
                        else:
                            suffix = child.name_normalized.split("_", 1)[1]
                            # Only skip if suffix is purely numeric (like _2, _3)
                            if not suffix.isdigit():
                                children.append(child)
                                children_sum += child.surface

                if not children:
                    continue

                diff = abs(parent.surface - children_sum)

                if diff <= 2.0:
                    status = "confirmé" if diff <= COMPOSITE_TOLERANCE else "probable"
                    logger.info(
                        f"Composite {status}: {parent.name_normalized}="
                        f"{parent.surface:.2f} = "
                        f"{'+'.join(c.name_normalized for c in children)}="
                        f"{children_sum:.2f} (diff={diff:.2f})"
                    )
                    parent.is_composite = True
                    parent.children = [c.name_normalized for c in children]
                    composites[parent.name_normalized] = parent.children

        return rooms, composites