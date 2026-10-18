"""Słowniki i dane referencyjne: stacje, miasta, przewoźnicy, kategorie, typy postoju.

Te endpointy zmieniają się rzadko, więc klient trzyma je w cache na 24 godziny.
Wyszukiwanie stacji po nazwie jest najczęstszym pierwszym krokiem przy analizie
rozkładu — bez cache jeden skrypt wyczerpałby limit kilkoma zapytaniami.
"""

from __future__ import annotations

from typing import Any

from ..cache import make_key
from ..client import PlkClient
from ..config import DEFAULT_DICTIONARY_TTL

DICTIONARY_TTL = DEFAULT_DICTIONARY_TTL


def _items(payload: Any, key: str) -> list[dict[str, Any]]:
    """Wyciąga listę z odpowiedzi słownika, tolerując krótsze nazwy klucza."""
    if isinstance(payload, list):
        return [i for i in payload if isinstance(i, dict)]
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, list):
            return [i for i in value if isinstance(i, dict)]
    return []


def list_stations(
    client: PlkClient,
    *,
    search: str | None = None,
    page: int = 1,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    """Zwraca stronę słownika stacji.

    :param search: filtr po początku nazwy, np. ``"Warszawa"``.
    :param page_size: maksymalnie 10000. Wartość 10000 mieści wszystkie stacje
        w jednym zapytaniu, co przy limicie 100/h bywa istotne.
    """
    payload = client.get(
        "/api/v1/dictionaries/stations",
        params={"search": search, "page": page, "pageSize": min(page_size, 10000)},
        cache_ttl=DICTIONARY_TTL,
    )
    return _items(payload, "stations")


def list_all_stations(client: PlkClient) -> list[dict[str, Any]]:
    """Pobiera komplet stacji, iterując po stronach aż do wyczerpania wyników."""
    first = client.get(
        "/api/v1/dictionaries/stations",
        params={"page": 1, "pageSize": 10000},
        cache_ttl=DICTIONARY_TTL,
    )
    stations = _items(first, "stations")
    total_pages = first.get("totalPages", 1) if isinstance(first, dict) else 1

    for page in range(2, int(total_pages) + 1):
        payload = client.get(
            "/api/v1/dictionaries/stations",
            params={"page": page, "pageSize": 10000},
            cache_ttl=DICTIONARY_TTL,
        )
        stations.extend(_items(payload, "stations"))

    return stations


def stations_count(client: PlkClient) -> int:
    """Liczba stacji w słowniku — bez pobierania całej listy."""
    payload = client.get(
        "/api/v1/dictionaries/stations",
        params={"page": 1, "pageSize": 1},
        cache_ttl=DICTIONARY_TTL,
    )
    return int(payload.get("totalCount", 0)) if isinstance(payload, dict) else 0


def list_carriers(client: PlkClient) -> list[dict[str, Any]]:
    """Zwraca przewoźników ze skrótami używanymi w filtrach (``IC``, ``KM``...).

    :raises ConfigurationError: gdy słownik jest pusty.
    """
    payload = client.get("/api/v1/dictionaries/carriers", cache_ttl=DICTIONARY_TTL)
    carriers = _items(payload, "carriers")
    if not carriers:
        from ..errors import ConfigurationError

        raise ConfigurationError(
            "Słownik przewoźników jest pusty lub nie udało się go odczytać — "
            "sprawdź klucz API komendą: python -m plk_open_data cli info"
        )
    return carriers


def list_cities(client: PlkClient, *, search: str | None = None) -> list[dict[str, Any]]:
    """Zwraca miasta wraz z identyfikatorami ich stacji.

    Użyteczne zamiast wpisywania nazwy w ``from``/``to``: miasto to agregacja,
    więc np. Warszawa zwraca kilkanaście stacji.
    """
    payload = client.get(
        "/api/v1/dictionaries/cities",
        params={"search": search},
        cache_ttl=DICTIONARY_TTL,
    )
    return _items(payload, "cities")


def list_commercial_categories(client: PlkClient) -> list[dict[str, Any]]:
    """Kategorie handlowe pociągów (``IC``, ``IR``, ``R``, ``E``...)."""
    payload = client.get(
        "/api/v1/dictionaries/commercial-categories", cache_ttl=DICTIONARY_TTL
    )
    return _items(payload, "commercialCategories")


def list_stop_types(client: PlkClient) -> list[dict[str, Any]]:
    """Typy postoju na trasie (przystanek, postój techniczny, zmiana pasażera...)."""
    payload = client.get("/api/v1/dictionaries/stop-types", cache_ttl=DICTIONARY_TTL)
    return _items(payload, "stopTypes")


def data_version(client: PlkClient) -> dict[str, Any]:
    """Zwraca wersję danych — GUID zmieniający się przy każdej aktualizacji cache po stronie API.

    Przydatne do wykrywania, czy lokalny cache odpowiedzi jest nieaktualny.
    """
    return client.get("/api/v1/data-version", cache_ttl=300)


def field_dictionary(client: PlkClient, endpoint: str) -> dict[str, Any]:
    """Słownik skrótów pól JSON dla wariantu ``/shortened``.

    :param endpoint: ``"schedules"``, ``"operations"`` lub ``"disruptions"``.
    """
    return client.get(f"/api/v1/fields/{endpoint}", cache_ttl=DICTIONARY_TTL)


def field_dictionary_csv(client: PlkClient, endpoint: str) -> str:
    """Ten sam słownik, ale jako tekst CSV — wygodne do wklejenia w arkusz."""
    payload = client.get(f"/api/v1/fields/{endpoint}/csv")
    if isinstance(payload, dict) and "text" in payload:
        return str(payload["text"])
    return str(payload or "")


# ---------------------------------------------------------------------- #
# Endpointy publiczne (bez klucza API)
# ---------------------------------------------------------------------- #


def api_version() -> dict[str, Any]:
    """Wersja API. Działa **bez klucza** — przydatna do sprawdzenia dostępności."""
    with PlkClient(use_cache=False) as client:
        return client.get("/api/version")


def terms_version() -> dict[str, Any]:
    """Wersja regulaminu. Działa **bez klucza**."""
    with PlkClient(use_cache=False) as client:
        return client.get("/api/v1/terms/version")


def terms(client: PlkClient | None = None) -> Any:
    """Pełny tekst regulaminu."""
    if client is not None:
        return client.get("/api/v1/terms")
    with PlkClient(use_cache=False) as temp:
        return temp.get("/api/v1/terms")


def privacy(client: PlkClient | None = None) -> Any:
    """Treść klauzuli informacyjnej (RODO)."""
    if client is not None:
        return client.get("/api/v1/privacy")
    with PlkClient(use_cache=False) as temp:
        return temp.get("/api/v1/privacy")


def cache_key_for(path: str, params: dict[str, Any] | None = None) -> str:
    """Klucz cache dla danego zapytania — używany w diagnostyce (``cli cache``)."""
    return make_key(path, params)