"""Zarządzanie kluczem API: rejestracja, weryfikacja, limity, statystyki zużycia.

Pełna ścieżka do działającego klucza (szczegóły w ``SKILL.md``):

1. :func:`register` — ``POST /apikey/generate``. **Jedyny endpoint, na który nie
   trzeba mieć klucza.** Wysyła wiadomość weryfikacyjną na podany adres.
2. :func:`verify_with_token` albo :func:`verify_with_code` — aktywacja na podstawie
   linku z e-maila (token) albo 8-cyfrowego kodu. Wariant z kodem wymaga już
   klucza w nagłówku.
3. :func:`accept_terms` — akceptacja regulaminu (``acceptTermsOfService: true``).
4. :func:`info` — potwierdzenie, że klucz działa i zobaczcie swój pakiet limitów.

Świadomie **nie** obejmuję ``PUT /apikey/update`` (wymaga klucza administratora)
ani ``POST /apikey/deactivate`` (nieodwracalne wyłączenie klucza).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..client import PlkClient
from ..config import build_settings
from ..errors import ValidationError
from ..helpers import parse_date

#: Dostępne pakiety limitów i ich wartości (zgodne z opisem w API).
LIMIT_TIERS: dict[str, dict[str, int]] = {
    "Basic": {"per_hour": 100, "per_day": 1_000},
    "Standard": {"per_hour": 500, "per_day": 5_000},
    "Premium": {"per_hour": 2_000, "per_day": 20_000},
}

#: Wymagana długość uzasadnienia przy wniosku o zmianę limitu.
JUSTIFICATION_MIN = 20
JUSTIFICATION_MAX = 1000


def _unwrap(payload: Any) -> Any:
    """Zdejmuje envelope ``{success, data}``, w który opakowane są odpowiedzi ``/apikey/*``.

    Swagger deklaruje dla tych endpointów samo ``200: OK`` bez schematu, więc
    kształtu odpowiedzi nie da się z niego odczytać — trzeba było sprawdzić na
    żywo. Wszystkie zwracają ``{"success": true, "data": {...}}``.

    Funkcja jest tolerancyjna: jeśli envelope zniknie w przyszłej wersji API,
    zwróci payload bez zmian.
    """
    if isinstance(payload, dict) and "data" in payload and "success" in payload:
        return payload["data"]
    return payload


# ---------------------------------------------------------------------- #
# Odczyt stanu klucza
# ---------------------------------------------------------------------- #


def info(client: PlkClient) -> dict[str, Any]:
    """Informacje o bieżącym kluczu: nazwa, organizacja, pakiet limitów, stan.

    Zwracane pola: ``id``, ``name``, ``organization``, ``contactEmail``,
    ``accessLevel`` (``Basic``/``Standard``/``Premium``), ``hourlyRateLimit``,
    ``dailyRateLimit``, ``totalRequests``, ``isActive``, ``createdAt``.

    Uwaga: API **nie zwraca** tu informacji o weryfikacji e-maila ani akceptacji
    regulaminu. Stan klucza poznasz po ``isActive`` — jeśli jest ``false``,
    najpewniej brakuje aktywacji albo akceptacji regulaminu.
    """
    return _unwrap(client.get("/api/v1/apikey/info"))


def usage(
    client: PlkClient,
    *,
    from_date: dt.date | str | None = None,
    to_date: dt.date | str | None = None,
) -> dict[str, Any]:
    """Statystyki wykorzystania klucza.

    Zwraca ``totalRequests``, ``dailyUsage`` (data, liczba zapytań, średni czas
    odpowiedzi, błędy), ``topEndpoints`` i ``period``.

    :param from_date: początek okresu (domyślnie 30 dni wstecz).
    :param to_date: koniec okresu (domyślnie teraz).
    """
    start = parse_date(from_date)
    end = parse_date(to_date)
    params = {}
    if start:
        params["fromDate"] = start.isoformat()
    if end:
        params["toDate"] = end.isoformat()
    return _unwrap(client.get("/api/v1/apikey/usage", params=params or None, cache_ttl=30))


def limits(client: PlkClient) -> dict[str, Any]:
    """Bieżące limity klucza, dostępne pakiety i historia wniosków o zmianę.

    Zwraca ``current`` (``accessLevel``, ``hourlyRateLimit``, ``dailyRateLimit``),
    ``availableTiers`` i ``requests``.

    Działa **także po przekroczeniu limitu zapytań** — to jedyny endpoint
    ``/apikey/*``, z którego można się dowiedzieć, że limit właśnie wyczerpałeś.
    """
    return _unwrap(client.get("/api/v1/apikey/limit-request", cache_ttl=60))


def request_limit_change(
    client: PlkClient,
    requested_tier: str,
    justification: str,
) -> dict[str, Any]:
    """Składa wniosek o zmianę pakietu limitów.

    Dla jednego klucza może istnieć **tylko jeden oczekujący wniosek** — kolejny
    dostanie HTTP 409 (:class:`~plk_open_data.errors.ConflictError`).

    :param requested_tier: ``"Basic"``, ``"Standard"`` albo ``"Premium"``.
    :param justification: opis zastosowania, 20–1000 znaków. O tym, czy wniosek
        zostanie rozpatrzony, decyduje administrator PKP PLK.
    :raises ValidationError: gdy tier jest nieprawidłowy albo uzasadnienie
        poza zakresem 20–1000 znaków.
    """
    tier = _validate_tier(requested_tier)
    _validate_justification(justification)

    return _unwrap(
        client.post(
            "/api/v1/apikey/limit-request",
            json={"requestedTier": tier, "justification": justification.strip()},
        )
    )


def _validate_tier(requested_tier: str) -> str:
    normalized = str(requested_tier).strip()
    match = next((t for t in LIMIT_TIERS if t.lower() == normalized.lower()), None)
    if match is None:
        raise ValidationError(
            f"Nieznany pakiet {requested_tier!r}. Dostępne: {', '.join(LIMIT_TIERS)}"
        )
    return match


def _validate_justification(justification: str) -> None:
    text = (justification or "").strip()
    if len(text) < JUSTIFICATION_MIN:
        raise ValidationError(
            f"Uzasadnienie musi mieć co najmniej {JUSTIFICATION_MIN} znaków "
            f"(ma {len(text)}). Opisz, do czego wykorzystujesz API i dlaczego "
            "obecny limit jest niewystarczający."
        )
    if len(text) > JUSTIFICATION_MAX:
        raise ValidationError(
            f"Uzasadnienie nie może przekraczać {JUSTIFICATION_MAX} znaków (ma {len(text)})."
        )


# ---------------------------------------------------------------------- #
# Rejestracja i aktywacja
# ---------------------------------------------------------------------- #


def register(
    name: str,
    *,
    contact_email: str | None = None,
    organization: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    expected_usage: str | None = None,
    duns_number: str | None = None,
    accept_terms: bool = True,
) -> dict[str, Any]:
    """Wysyła wniosek o nowy klucz API. **Nie wymaga istniejącego klucza.**

    API wysyła wiadomość weryfikacyjną na ``contact_email`` i przekazuje wniosek
    do akceptacji przez administratorów PKP PLK. Klucz zwracany w odpowiedzi
    jest **nieaktywny**, dopóki nie potwierdzisz adresu.

    :param name: nazwa/opis klucza — pole wymagane, np. ``"Analiza opóźnień"``.
    :param contact_email: adres, na który trafi link i kod weryfikacyjny.
        **Bez niego nie da się aktywować klucza.**
    :param organization: organizacja (opcjonalnie).
    :param first_name: imię osoby kontaktowej (opcjonalnie).
    :param last_name: nazwisko osoby kontaktowej (opcjonalnie).
    :param expected_usage: deklarowany sposób wykorzystania (opcjonalnie).
    :param duns_number: numer DUNS firmy (opcjonalnie).
    :param accept_terms: potwierdzenie akceptacji regulaminu. API wymaga ``true``.
    :raises ValidationError: gdy brakuje ``name`` albo ``contact_email``.
    """
    if not name or not name.strip():
        raise ValidationError("Pole 'name' jest wymagane — np. 'Analiza opóźnień'")
    if not contact_email or "@" not in contact_email:
        raise ValidationError(
            "Pole 'contact_email' jest wymagane do aktywacji klucza — "
            "podaj adres e-mail, na który trafi kod weryfikacyjny."
        )

    body: dict[str, Any] = {
        "name": name.strip(),
        "acceptTermsOfService": bool(accept_terms),
        "contactEmail": contact_email.strip(),
    }
    optional = {
        "organization": organization,
        "firstName": first_name,
        "lastName": last_name,
        "expectedUsage": expected_usage,
        "dunsNumber": duns_number,
    }
    body.update({k: v.strip() if isinstance(v, str) else v for k, v in optional.items() if v})

    # Klucz nie istnieje jeszcze, więc celowo nie używamy bieżącego PLK_API_KEY.
    settings = build_settings(api_key="")
    with PlkClient(settings=settings, use_cache=False) as client:
        return _unwrap(client.post("/api/v1/apikey/generate", json=body))


def verify_with_token(token: str) -> dict[str, Any]:
    """Aktywuje klucz linkiem z e-maila (``GET /apikey/verify-email?token=...``).

    Nie wymaga klucza w nagłówku — token z linku sam w sobie potwierdza tożsamość.
    """
    if not token or not token.strip():
        raise ValidationError("Nie podano tokenu weryfikacyjnego z linku")
    with PlkClient(use_cache=False) as client:
        return _unwrap(
            client.get("/api/v1/apikey/verify-email", params={"token": token.strip()})
        )


def verify_with_code(client: PlkClient, code: str) -> dict[str, Any]:
    """Aktywuje klucz 8-cyfrowym kodem z e-maila.

    :param code: 8 cyfr jako tekst albo liczba.
    :raises ValidationError: gdy kod nie ma 8 cyfr.
    """
    digits = str(code).strip().replace(" ", "")
    if not digits.isdigit() or len(digits) != 8:
        raise ValidationError(f"Kod weryfikacyjny musi mieć dokładnie 8 cyfr, a otrzymano {code!r}")
    return _unwrap(client.post("/api/v1/apikey/verify-email", json={"code": digits}))


def resend_verification(client: PlkClient) -> dict[str, Any]:
    """Prosi o ponowne wysłanie wiadomości weryfikacyjnej.

    Adres e-mail jest brany z kontekstu klucza, więc trzeba podać klucz, który
    go zarejestrował.

    API ogranicza tę operację: **1 na minutę i 5 na godzinę**.
    """
    return _unwrap(client.post("/api/v1/apikey/resend-verification"))


def accept_terms(client: PlkClient) -> dict[str, Any]:
    """Akceptuje aktualną wersję regulaminu dla istniejącego klucza."""
    return _unwrap(client.post("/api/v1/apikey/accept-terms", json={"acceptTermsOfService": True}))