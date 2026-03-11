"""
Room parsers for SuperExtractor.
This module provides pattern matching and parsing utilities.
"""

import re
import logging
from typing import List, Tuple

from .models import RoomType, ExtractedRoom

logger = logging.getLogger(__name__)


class RoomParsers:
    """Collection of room parsing methods and patterns."""
    
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
        "SURF", "LOT", "M00", "TYPE:", "N°",
    ]
    
    def __init__(self, normalizer):
        self.normalizer = normalizer
    
    def rooms_from_table(self, rows, source) -> List[ExtractedRoom]:
        """Convertit les lignes du tableau spatial en ExtractedRoom"""
        rooms = []
        for name_raw, surface_str in rows:
            try:
                surface = float(surface_str)
            except ValueError:
                continue
            
            # Skip invalid surfaces
            if surface < 0.5 or surface > 500:
                continue
            
            # Skip lines containing TOTAL (these are summary lines, not rooms)
            name_upper = name_raw.upper()
            if "TOTAL" in name_upper and surface > 50:
                continue
            
            # For exterior rooms (terrace, balcony), skip if surface > 100 (likely misdetection)
            if any(kw in name_upper for kw in ["TERRASSE", "TERRACE", "BALCON", "LOGGIA"]):
                if surface > 100:
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
