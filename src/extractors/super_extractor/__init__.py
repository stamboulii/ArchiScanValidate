import logging
import sys

# Desactiver les logs pour super_extractor par defacf
# en supprimant tous les handlers et en设置 le niveau
def setup_super_extractor_logging():
    """Configure le logging pour super_extractor."""
    for name in [
        'super_extractor',
        'super_extractor.super_extractor',
        'super_extractor.spatial_extractor',
        'super_extractor.text_extractor',
        'super_extractor.models',
        'super_extractor.room_normalizer',
        'super_extractor.composite_resolver',
        'super_extractor.metadata_extractor',
        'super_extractor.plan_validator',
    ]:
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        # Supprimer tous les handlers existants
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        # Ajouter un handler null pour eviter la propagation
        logger.addHandler(logging.NullHandler())

# Appeler immediatement
setup_super_extractor_logging()

from .super_extractor import SuperExtractor, extract_plan_data, extract_plan_data_legacy, batch_extract, extract_all_plans
from .models import RoomType, ExtractedRoom, ExtractionResult

__all__ = [
    "SuperExtractor", "extract_plan_data", "extract_plan_data_legacy",
    "batch_extract", "extract_all_plans", "RoomType", "ExtractedRoom", "ExtractionResult",
]
