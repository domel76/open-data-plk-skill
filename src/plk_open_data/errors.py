"""Wyjątki i mapowanie odpowiedzi błędnej na wyjątki Pythona.

API zwraca błędy w dwujęzycznym formacie:

.. code-block:: json

    {
      "error": "Unauthorized",
      "errorCode": "MissingKey",
      "message": "Brak klucza API. ...",
      "messageEn": "Missing API key. ...",
      "timestamp": "2026-10-18T14:11:40.58Z",
      "path": "/api/v1/schedules"
    }

Część endpointów zwraca zamiast tego standardowy ``ProblemDetails`` (RFC 9457),
dlatego :func:`parse_api_error` obsługuje oba kształty.
"""

from __future__ import annotations

from typing import Any


class PlkError(Exception):
    """Bazowy wyjątek biblioteki."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        path: str | None = None,
        payload: Any = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.path = path
        self.payload = payload
        #: Ile sekund czekać przed ponowieniem — wartość nagłówka ``Retry-After``,
        #: jeśli serwer go podał. Dotyczy też 5xx, nie tylko 429.
        self.retry_after = retry_after

    def __str__(self) -> str:
        parts = [self.message]
        if self.error_code:
            parts.append(f"[{self.error_code}]")
        if self.status_code:
            parts.append(f"HTTP {self.status_code}")
        return " | ".join(parts)


class ConfigurationError(PlkError):
    """Brak klucza API albo niepoprawna konfiguracja klienta."""


class AuthenticationError(PlkError):
    """Klucz API nieprawidłowy, nieaktywny lub nieprzesłany (HTTP 401)."""


class PermissionDeniedError(PlkError):
    """Klucz działa, ale nie ma uprawnień do zasobu (HTTP 403).

    Np. ``PUT /api/v1/apikey/update`` wymaga klucza administratora.
    """


class RateLimitError(PlkError):
    """Przekroczono limit zapytań (HTTP 429)."""


class NotFoundError(PlkError):
    """Nie znaleziono zasobu (HTTP 404) — np. nieistniejący rozkład lub pociąg."""


class ValidationError(PlkError):
    """API odrzuciło parametry (HTTP 400).

    Najczęściej: pomieszanie filtrów, które wykluczają się nawzajem
    (``stations`` razem z ``from``/``to``), albo ``from`` bez ``to``.
    """


class ConflictError(PlkError):
    """Konflikt ze stanem serwera (HTTP 409) — np. drugi oczekujący wniosek o limit."""


class ServerError(PlkError):
    """Błąd po stronie API (HTTP 5xx)."""


class TransportError(PlkError):
    """Błąd sieciowy: timeout, brak połączenia, niepoprawna odpowiedź."""


#: Mapowanie ``errorCode`` z odpowiedzi API na klasę wyjątku.
#: Uzupełnia mapowanie po kodzie HTTP — na wypadek nieznanego kodu błędu.
ERROR_CODE_MAP: dict[str, type[PlkError]] = {
    "MissingKey": AuthenticationError,
    "InvalidKey": AuthenticationError,
    "InvalidApiKey": AuthenticationError,
    "KeyNotActive": AuthenticationError,
    "KeyNotVerified": AuthenticationError,
    "KeyDeactivated": AuthenticationError,
    "TermsNotAccepted": PermissionDeniedError,
    "Forbidden": PermissionDeniedError,
    "RateLimitExceeded": RateLimitError,
    "TooManyRequests": RateLimitError,
    "NotFound": NotFoundError,
    "InvalidRequest": ValidationError,
    "InvalidParameter": ValidationError,
    "Conflict": ConflictError,
    "RequestAlreadyPending": ConflictError,
}

#: Mapowanie kodu HTTP na klasę wyjątku.
STATUS_MAP: dict[int, type[PlkError]] = {
    400: ValidationError,
    401: AuthenticationError,
    403: PermissionDeniedError,
    404: NotFoundError,
    409: ConflictError,
    429: RateLimitError,
}


def _extract_error_fields(payload: Any) -> tuple[str | None, str | None, str | None, str | None]:
    """Wydziel ``(message, error_code, path, title)`` z dwóch formatów błędu."""
    if not isinstance(payload, dict):
        return (None, None, None, None)

    error_code = payload.get("errorCode") or payload.get("error_code")
    path = payload.get("path")
    title = payload.get("title")

    # Preferuj angielski komunikat — jest stabilniejszy niż polski,
    # a komunikaty polskie bywają zniekształcone przez escapowanie Unicode.
    message = (
        payload.get("messageEn")
        or payload.get("message")
        or payload.get("detail")
        or title
    )

    return (message, error_code, path, title)


def parse_api_error(status_code: int, payload: Any, *, retry_after: float | None = None) -> PlkError:
    """Zamienia odpowiedź błędną na właściwy wyjątek.

    Kolejność: ``errorCode`` (najdokładniejsze) → kod HTTP → ``ServerError``.
    """
    message, error_code, path, title = _extract_error_fields(payload)
    fallback = message or title or f"API zwróciło HTTP {status_code}"

    exc_type = STATUS_MAP.get(status_code, ServerError)
    if error_code and error_code in ERROR_CODE_MAP:
        exc_type = ERROR_CODE_MAP[error_code]

    kwargs: dict[str, Any] = {
        "status_code": status_code,
        "error_code": error_code,
        "path": path,
        "payload": payload,
        "retry_after": retry_after,
    }

    return exc_type(fallback, **kwargs)