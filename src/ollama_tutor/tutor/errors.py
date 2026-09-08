"""Taxonomie d'erreurs applicatives (010 P2-Robustesse, T039).

Adapté de ``autreprojet/OpenTutor-main`` (``apps/api/libs/exceptions.py`` :
``AppError`` + sous-classes à ``code``/``status`` fixes, ``to_dict`` pour
la réponse JSON). Ici : stdlib seul, aucun import UI (ni fastapi ni
textual) — ``web/server.py`` mappe ces erreurs vers HTTP.

Ajout local : ``PayloadTooLargeError`` (413, durcissement upload T041).
"""

from __future__ import annotations


class AppError(Exception):
    """Erreur applicative de base — toutes les erreurs métier en héritent."""

    code: str = "internal_error"
    status: int = 500

    def __init__(self, message: str = "Internal server error") -> None:
        self.message = message
        super().__init__(message)

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "status": self.status}


class NotFoundError(AppError):
    """Ressource introuvable → 404."""

    code = "not_found"
    status = 404

    def __init__(self, resource: str = "Resource", resource_id: object = None) -> None:
        detail = f"{resource} not found"
        if resource_id is not None:
            detail = f"{resource} {resource_id} not found"
        super().__init__(detail)


class ConflictError(AppError):
    """Conflit d'état (doublon, transition refusée) → 409."""

    code = "conflict"
    status = 409

    def __init__(self, message: str = "Conflict") -> None:
        super().__init__(message)


class ValidationError(AppError):
    """Entrée invalide → 422."""

    code = "validation_error"
    status = 422

    def __init__(self, message: str = "Validation error") -> None:
        super().__init__(message)


class PayloadTooLargeError(AppError):
    """Corps trop volumineux (413 précoce, T041)."""

    code = "payload_too_large"
    status = 413

    def __init__(self, message: str = "Payload too large") -> None:
        super().__init__(message)


class LLMUnavailableError(AppError):
    """Moteur LLM injoignable → 503 (jamais 500 masqué)."""

    code = "llm_unavailable"
    status = 503

    def __init__(self, message: str = "LLM service is unavailable") -> None:
        super().__init__(message)


class IngestionError(AppError):
    """Échec d'ingestion/parsing de document → 500."""

    code = "ingestion_error"
    status = 500

    def __init__(self, message: str = "Ingestion failed", source: str | None = None) -> None:
        self.source = source
        detail = f"Ingestion failed for {source}: {message}" if source else message
        super().__init__(detail)


__all__ = [
    "AppError",
    "ConflictError",
    "IngestionError",
    "LLMUnavailableError",
    "NotFoundError",
    "PayloadTooLargeError",
    "ValidationError",
]
