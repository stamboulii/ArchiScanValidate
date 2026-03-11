"""
Exceptions personnalisées pour ArchiExtract.
Toutes les exceptions specifiques au projet sont definies ici.

Usage:
    from exceptions import (
        ExtractionError,
        ConfigurationError,
        APIError,
        ValidationError
    )
"""


# =============================================================================
# Exceptions de Base (Hierarchie)
# =============================================================================

class ArchiExtractError(Exception):
    """Exception de base pour ArchiExtract."""
    
    def __init__(self, message: str, details: str = None):
        super().__init__(message)
        self.message = message
        self.details = details or ""
    
    def __str__(self):
        if self.details:
            return f"{self.message}: {self.details}"
        return self.message


# =============================================================================
# Exceptions de Configuration
# =============================================================================

class ConfigurationError(ArchiExtractError):
    """Erreur liee a la configuration de l'application."""
    pass


class MissingAPIKeyError(ConfigurationError):
    """Erreur lorsqu'une cle API est manquante."""
    
    def __init__(self, api_name: str, suggestion: str = None):
        suggestion = suggestion or (
            f"Definissez la variable d'environnement ARCHI_{api_name.upper()}_API_KEY "
            "ou creez un fichier .env a la racine du projet."
        )
        super().__init__(
            message=f"La cle API {api_name} n'est pas configuree.",
            details=suggestion
        )
        self.api_name = api_name


class InvalidConfigurationError(ConfigurationError):
    """Erreur de validation de configuration."""
    pass


# =============================================================================
# Exceptions d'Extraction
# =============================================================================

class ExtractionError(ArchiExtractError):
    """Erreur generale lors de l'extraction de donnees."""
    pass


class ImageLoadError(ExtractionError):
    """Erreur lors du chargement d'une image."""
    
    def __init__(self, image_path: str, reason: str = None):
        super().__init__(
            message=f"Impossible de charger l'image: {image_path}",
            details=reason
        )
        self.image_path = image_path


class EmptyImageError(ExtractionError):
    """Erreur lorsqu'une image est vide."""
    
    def __init__(self, image_path: str):
        super().__init__(
            message=f"Fichier image vide: {image_path}"
        )
        self.image_path = image_path


class InvalidImageDimensionsError(ExtractionError):
    """Erreur lors de dimensions d'image invalides."""
    
    def __init__(self, width: int, height: int):
        super().__init__(
            message=f"Dimensions d'image invalides: {width}x{height}"
        )
        self.width = width
        self.height = height


class PDFConversionError(ExtractionError):
    """Erreur lors de la conversion PDF en images."""
    
    def __init__(self, pdf_path: str, reason: str = None):
        super().__init__(
            message=f"Impossible d'extraire les pages du PDF: {pdf_path}",
            details=reason
        )
        self.pdf_path = pdf_path


# =============================================================================
# Exceptions d'API
# =============================================================================

class APIError(ArchiExtractError):
    """Erreur generale liee a un appel API externe."""
    pass


class ClaudeAPIError(APIError):
    """Erreur liee a l'API Claude d'Anthropic."""
    
    def __init__(self, message: str, status_code: int = None):
        super().__init__(message=message)
        self.status_code = status_code


class APIResponseError(APIError):
    """Erreur lors du parsing de la reponse API."""
    
    def __init__(self, raw_response: str = None):
        super().__init__(
            message="Erreur lors du parsing de la reponse API",
            details=raw_response[:200] if raw_response else None
        )
        self.raw_response = raw_response


class APIRateLimitError(APIError):
    """Erreur de rate limit API."""
    pass


class APITimeoutError(APIError):
    """Erreur de timeout API."""
    pass


# =============================================================================
# Exceptions de Parsing
# =============================================================================

class ParsingError(ArchiExtractError):
    """Erreur lors du parsing des donnees extraites."""
    pass


class JSONParseError(ParsingError):
    """Erreur lors du parsing JSON."""
    
    def __init__(self, raw_text: str, reason: str = None):
        super().__init__(
            message=f"Impossible de parser le JSON: {raw_text[:100]}...",
            details=reason
        )
        self.raw_text = raw_text


class NoJSONFoundError(ParsingError):
    """Erreur lorsqu'aucun JSON n'est trouve dans la reponse."""
    
    def __init__(self, raw_text: str):
        super().__init__(
            message=f"Aucun JSON trouve dans la reponse: {raw_text[:100]}..."
        )
        self.raw_text = raw_text


# =============================================================================
# Exceptions de Machine Learning
# =============================================================================

class MLError(ArchiExtractError):
    """Erreur liee au machine learning."""
    pass


class MissingMLDependencyError(MLError):
    """Erreur lorsqu'une dependance ML est manquante."""
    
    def __init__(self, dependency: str, install_cmd: str = None):
        suggestion = install_cmd or f"pip install {dependency}"
        super().__init__(
            message=f"Les dependances ML ne sont pas installees: {dependency}",
            details=f"Exécutez: {suggestion}"
        )
        self.dependency = dependency


class NoTrainedModelError(MLError):
    """Erreur lorsqu'aucun modele entraine n'est disponible."""
    
    def __init__(self, model_dir: str):
        super().__init__(
            message=f"Aucun modele entraine trouve dans {model_dir}",
            details="Entrainez d'abord un modele avec MLExtractor.train()"
        )
        self.model_dir = model_dir


class InsufficientTrainingDataError(MLError):
    """Erreur lorsqu'il n'y a pas assez de donnees pour l'entrainement."""
    
    def __init__(self, current_count: int, required_count: int):
        super().__init__(
            message=f"Pas assez de donnees valides pour l'entrainement: {current_count}",
            details=f"Minimum requis: {required_count} echantillons"
        )
        self.current_count = current_count
        self.required_count = required_count


# =============================================================================
# Exceptions de Validation
# =============================================================================

class ValidationError(ArchiExtractError):
    """Erreur de validation des donnees."""
    pass


class ConfidenceThresholdError(ValidationError):
    """Erreur lorsque la confiance est en dessous du seuil."""
    
    def __init__(self, confidence: float, threshold: float):
        super().__init__(
            message=f"Confiance ({confidence:.2f}) inferieure au seuil ({threshold:.2f})"
        )
        self.confidence = confidence
        self.threshold = threshold


# =============================================================================
# Exceptions de Donnees d'Entrainement
# =============================================================================

class TrainingDataError(ArchiExtractError):
    """Erreur liee aux donnees d'entrainement."""
    pass


class EmptyTrainingDataError(TrainingDataError):
    """Erreur lorsque les donnees d'entrainement sont vides."""
    
    def __init__(self, details: str = None):
        super().__init__(
            message="Aucune donnee d'entrainement fournie (liste vide)",
            details=details
        )


class ExportTrainingDataError(TrainingDataError):
    """Erreur lors de l'export des donnees d'entrainement."""
    pass


# =============================================================================
# Helper Functions
# =============================================================================

def wrap_extraction_error(error: Exception, context: str = None) -> ExtractionError:
    """
    Enveloppe une exception existante dans une ExtractionError.
    
    Args:
        error: L'exception originale
        context: Contexte supplementaire
        
    Returns:
        ExtractionError avec les informations de l'exception originale
    """
    if isinstance(error, ArchiExtractError):
        return error
    
    return ExtractionError(
        message=f"Erreur d'extraction: {str(error)}",
        details=context
    )


def wrap_api_error(error: Exception, api_name: str = "Claude") -> APIError:
    """
    Enveloppe une exception d'API avec des details contextuels.
    
    Args:
        error: L'exception originale
        api_name: Nom de l'API pour le message d'erreur
        
    Returns:
        APIError avec les informations de l'exception originale
    """
    if isinstance(error, ArchiExtractError):
        return error
    
    return APIError(
        message=f"Erreur {api_name} API: {str(error)}"
    )
