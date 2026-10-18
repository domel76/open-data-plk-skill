"""Warstwa HTTP: klucz API, limity zapytań, retry, cache, paginacja.

To jest serce biblioteki. Limity API są niskie (Basic: 100 zapytań/godz.,
1000/dobę), więc klient:

* **cache'uje** odpowiedzi w :mod:`plk_open_data.cache` (słowniki długo, dane
  real-time krótko),
* **ponawia** zapytanie przy 429 i 5xx z wykładniczym backoffem, szanując
  nagłówek ``Retry-After``,
* **liczy** zapytania w sesji, żeby dało się zobaczyć zbliżanie się do limitu,
* **sprawdza** parametry wykluczające się nawzajem, zanim zmarnuje zapytanie.

Podstawowy kontrakt: metody :meth:`PlkClient.get` i :meth:`PlkClient.post`
zwracają **zdeserializowany JSON** (albo ``None``, gdy odpowiedź jest pusta).
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx

from .cache import DiskCache, make_key
from .config import (
    DEFAULT_LIVE_TTL,
    Settings,
    build_settings,
)
from .errors import (
    ConfigurationError,
    RateLimitError,
    TransportError,
    parse_api_error,
)

logger = logging.getLogger(__name__)

#: Endpointy dostępne bez klucza API (sprawdzone 2026-10-18).
PUBLIC_PATHS = frozenset({"/api/version", "/api/v1/terms/version"})

#: Kody HTTP, przy których warto ponowić zapytanie.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

#: Klucze obiektu paginacji — różnią się między wariantem pełnym a ``/shortened``.
PAGINATION_KEYS = ("pagination", "pg")

#: Nagłówki, w których API może zwracać stan limitu.
_RATE_LIMIT_HEADERS = {
    "limit": ("x-ratelimit-limit", "x-rate-limit-limit"),
    "remaining": ("x-ratelimit-remaining", "x-rate-limit-remaining"),
    "reset": ("x-ratelimit-reset", "x-rate-limit-reset"),
}


@dataclass(slots=True)
class UsageCounter:
    """Licznik zapytań wykonanych przez bieżącą instancję klienta."""

    requests_made: int = 0
    cache_hits: int = 0
    retries: int = 0
    rate_limited: int = 0
    errors: int = 0
    started_at: float = field(default_factory=time.time)

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.started_at

    def as_dict(self) -> dict[str, Any]:
        minutes = max(self.elapsed_seconds / 60, 1e-9)
        return {
            "requests_made": self.requests_made,
            "cache_hits": self.cache_hits,
            "retries": self.retries,
            "rate_limited": self.rate_limited,
            "errors": self.errors,
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "requests_per_minute": round(self.requests_made / minutes, 2),
        }


class PlkClient:
    """Klient Open Railway Data API (PKP PLK).

    Użycie jako menedżer kontekstu gwarantuje zamknięcie połączenia::

        with PlkClient() as client:
            trains = client.get("/api/v1/operations", params={"stations": "38851"})

    :param api_key: klucz API. Gdy ``None``, brany z ``PLK_API_KEY`` / ``.env``.
    :param max_retries: ile ponowień przy 429 i 5xx (domyślnie 3).
    :param max_requests_per_hour: miękki limit — klient zwolni, zanim API odmówi.
        ``None`` wyłącza throttling (licznik i tak rośnie, patrz :attr:`usage`).
    :param offline: nie odpytuj API, tylko cache. Przydatne w testach i przy
        pracy bez klucza.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        cache_dir: Any = None,
        use_cache: bool = True,
        max_retries: int = 3,
        max_requests_per_hour: int | None = None,
        offline: bool = False,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or build_settings(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            cache_dir=cache_dir,
            use_cache=use_cache,
        )
        self.offline = offline
        self.max_retries = max(0, max_retries)
        self.max_requests_per_hour = max_requests_per_hour
        self.usage = UsageCounter()
        self.cache = DiskCache(self.settings.cache_dir, enabled=use_cache and not offline)

        headers = {
            "Accept": "application/json",
            "User-Agent": "plk-open-data/0.1.0 (+python-httpx)",
        }
        if self.settings.api_key:
            headers["X-Api-Key"] = self.settings.api_key

        self._client = httpx.Client(
            base_url=self.settings.base_url,
            headers=headers,
            timeout=self.settings.timeout,
            follow_redirects=True,
        )
        # Ostatni znany stan limitu, jaki API zwróciło w nagłówkach.
        self._rate_limit_state: dict[str, int | float | None] = {}

    # ------------------------------------------------------------------ #
    # Właściwości
    # ------------------------------------------------------------------ #

    @property
    def api_key(self) -> str | None:
        return self.settings.api_key

    @property
    def has_api_key(self) -> bool:
        return bool(self.settings.api_key)

    @property
    def rate_limit_state(self) -> dict[str, int | float | None]:
        """Ostatni odczytany z nagłówków limit, np. ``{"remaining": 97, ...}``."""
        return dict(self._rate_limit_state)

    # ------------------------------------------------------------------ #
    # Zarządzanie połączeniem
    # ------------------------------------------------------------------ #

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PlkClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Żądania
    # ------------------------------------------------------------------ #

    def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        cache_ttl: int | None = None,
        use_cache: bool | None = None,
    ) -> Any:
        """Wykonuje ``GET`` i zwraca zdeserializowany JSON.

        :param cache_ttl: czas życia wpisu w cache w sekundach. ``None`` wyłącza
            cache dla tego zapytania; ``0`` oznacza „użyj wartości domyślnej".
        """
        return self._request(
            "GET",
            path,
            params=params,
            cache_ttl=cache_ttl,
            use_cache=use_cache,
        )

    def post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Wykonuje ``POST`` i zwraca zdeserializowany JSON. Nie jest cache'owane."""
        return self._request("POST", path, params=params, json_body=json)

    def put(self, path: str, *, json: dict[str, Any] | None = None) -> Any:
        """Wykonuje ``PUT`` i zwraca zdeserializowany JSON."""
        return self._request("PUT", path, json_body=json)

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        items_key: str | None = None,
        max_pages: int | None = None,
        cache_ttl: int | None = DEFAULT_LIVE_TTL,
    ) -> Iterator[Any]:
        """Iteruje po kolejnych stronach wyniku, odpytując API tylko raz na stronę.

        :param items_key: pole z listą elementów (np. ``"trains"``). Gdy ``None``,
            wykrywane automatycznie — szukamy pierwszej listy obiektów w odpowiedzi.
        :param max_pages: bezpiecznik liczby stron, żeby pętla nie wyszła z domu.
        """
        page = 1
        pages_read = 0

        while True:
            page_params = {**(params or {}), "page": page}
            payload = self.get(path, params=page_params, cache_ttl=cache_ttl)
            pages_read += 1

            if not isinstance(payload, dict):
                return

            items, pagination = _extract_items(payload, items_key)
            yield from items

            if pagination is None:
                return
            if not _has_next_page(pagination):
                return
            page += 1
            if max_pages is not None and pages_read >= max_pages:
                logger.warning(
                    "Przerwano paginację po %d stronach (max_pages) dla %s",
                    pages_read,
                    path,
                )
                return

    # ------------------------------------------------------------------ #
    # Wnętrze
    # ------------------------------------------------------------------ #

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        cache_ttl: int | None = None,
        use_cache: bool | None = None,
    ) -> Any:
        clean_params = _clean_params(params)
        wants_cache = self.cache.enabled if use_cache is None else use_cache

        if wants_cache and cache_ttl:
            variant = "short" if "/shortened" in path else "full"
            key = make_key(path, clean_params, variant=variant)
            cached = self.cache.get(key)
            if cached is not None:
                self.usage.cache_hits += 1
                logger.debug("cache HIT %s %s", method, path)
                return cached

        if self.offline:
            raise TransportError(
                f"Klient jest w trybie offline, nie odpytuję API: {method} {path}. "
                "Wyczyszcz lub wyłącz tryb offline."
            )

        if method != "GET" and not self._path_is_public(path):
            self._require_api_key(path)

        if method == "GET":
            self._require_api_key(path)

        response = self._send_with_retry(method, path, clean_params, json_body)
        payload = _decode(response, method, path)

        if wants_cache and cache_ttl and isinstance(payload, (dict, list)):
            variant = "short" if "/shortened" in path else "full"
            self.cache.set(make_key(path, clean_params, variant=variant), payload, cache_ttl)

        return payload

    def _path_is_public(self, path: str) -> bool:
        return path.split("?")[0] in PUBLIC_PATHS

    def _require_api_key(self, path: str) -> None:
        if self.has_api_key or self._path_is_public(path):
            return
        raise ConfigurationError(
            f"Brak klucza API dla {path}. Ustaw zmienną środowiskową PLK_API_KEY "
            "albo zapisz ją w pliku .env. Klucz uzyskasz komendą: "
            "python -m plk_open_data register"
        )

    def _send_with_retry(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
    ) -> httpx.Response:
        self._throttle()
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            if attempt > 0:
                self.usage.retries += 1
            try:
                self.usage.requests_made += 1
                logger.debug("%s %s params=%s (próba %d)", method, path, params, attempt + 1)
                response = self._client.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                )
            except httpx.HTTPError as exc:
                last_error = TransportError(f"Błąd sieciowy przy {method} {path}: {exc}")
                if attempt >= self.max_retries:
                    self.usage.errors += 1
                    raise last_error from exc
                time.sleep(_backoff_delay(attempt))
                continue

            self._read_rate_limit_headers(response)

            if response.status_code < 400:
                return response

            error = parse_api_error(
                response.status_code,
                _safe_json(response),
                retry_after=_parse_retry_after(response),
            )

            if response.status_code in RETRYABLE_STATUS_CODES:
                if isinstance(error, RateLimitError):
                    self.usage.rate_limited += 1
                if attempt >= self.max_retries:
                    self.usage.errors += 1
                    raise error
                # Nagłówek Retry-After ma pierwszeństwo przed naszym backoffem —
                # serwer wie, kiedy znów wolno, lepiej niż zgadywanie.
                sleep_for = error.retry_after or _backoff_delay(attempt)
                logger.warning(
                    "%s przy %s (HTTP %d) — ponawiam za %.1fs (próba %d/%d)",
                    type(error).__name__,
                    path,
                    response.status_code,
                    sleep_for,
                    attempt + 1,
                    self.max_retries + 1,
                )
                time.sleep(sleep_for)
                continue

            self.usage.errors += 1
            raise error

        self.usage.errors += 1
        raise last_error or TransportError(f"Nieoczekiwane zakończenie ponowień dla {path}")

    def _throttle(self) -> None:
        """Zwalnia żądania, jeśli ustawiono miękki limit godzinowy."""
        if not self.max_requests_per_hour:
            return

        window_start = self.usage.started_at
        min_interval = 3600.0 / self.max_requests_per_hour
        elapsed = time.time() - window_start
        expected_requests = int(elapsed / min_interval) + 1

        if self.usage.requests_made >= min(self.max_requests_per_hour, expected_requests):
            sleep_for = min_interval - (elapsed % min_interval)
            if sleep_for > 0:
                logger.info("Throttling: czekam %.1fs, żeby nie przekroczyć limitu", sleep_for)
                time.sleep(sleep_for)

    def _read_rate_limit_headers(self, response: httpx.Response) -> None:
        for state_key, header_names in _RATE_LIMIT_HEADERS.items():
            for header in header_names:
                raw = response.headers.get(header)
                if raw is not None:
                    try:
                        self._rate_limit_state[state_key] = int(raw)
                    except ValueError:
                        try:
                            self._rate_limit_state[state_key] = float(raw)
                        except ValueError:
                            self._rate_limit_state[state_key] = None
                    break


# ---------------------------------------------------------------------- #
# Funkcje pomocnicze (modułowe, żeby dało się je testować w izolacji)
# ---------------------------------------------------------------------- #


def _clean_params(params: dict[str, Any] | None) -> dict[str, Any] | None:
    """Odrzuca ``None`` i puste wartości, zamienia bool na ``true``/``false``."""
    if not params:
        return None
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            cleaned[key] = "true" if value else "false"
        elif isinstance(value, (list, tuple, set)):
            joined = ",".join(str(v) for v in value)
            if joined:
                cleaned[key] = joined
        elif value == "":
            continue
        else:
            cleaned[key] = value
    return cleaned or None


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"message": response.text[:500]}


def _decode(response: httpx.Response, method: str, path: str) -> Any:
    """Zamienia odpowiedź na JSON; pusta odpowiedź (204/205) to ``None``."""
    if response.status_code in (204, 205):
        return None
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            # deklarowany JSON, ale niepoprawny — to błąd, nie surowy tekst
            raise TransportError(
                f"API zwróciło niepoprawny JSON dla {method} {path}: {response.text[:300]}"
            ) from None
        # Endpointy /terms/html i /privacy/html zwracają po prostu tekst
        return {"text": response.text}


def _backoff_delay(attempt: int, *, base: float = 0.5, cap: float = 30.0) -> float:
    """Wykładniczy backoff z jitterem, żeby klienty nie uderzały równocześnie."""
    exponential = min(cap, base * (2**attempt))
    return exponential * (0.5 + random.random() * 0.5)


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Odczytuje ``Retry-After`` w sekundach (format HTTP lub data)."""
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    from email.utils import parsedate_to_datetime

    try:
        target = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    import datetime as _dt

    if target.tzinfo is None:
        target = target.replace(tzinfo=_dt.UTC)
    delta = (target - _dt.datetime.now(_dt.UTC)).total_seconds()
    return max(delta, 0.0)


def _extract_items(
    payload: dict[str, Any], items_key: str | None
) -> tuple[list[Any], dict[str, Any] | None]:
    """Wyciąga listę elementów i obiekt paginacji z odpowiedzi."""
    pagination: dict[str, Any] | None = None
    for key in PAGINATION_KEYS:
        value = payload.get(key)
        if isinstance(value, dict):
            pagination = value
            break

    if items_key is not None:
        items = payload.get(items_key)
        return (items if isinstance(items, list) else []), pagination

    # Automatyczne wykrywanie: pierwsza lista obiektów, która nie jest paginacją.
    for key, value in payload.items():
        if key in PAGINATION_KEYS:
            continue
        if isinstance(value, list) and all(isinstance(v, dict) for v in value):
            return value, pagination

    return [], pagination


def _has_next_page(pagination: dict[str, Any]) -> bool:
    """Sprawdza, czy jest następna strona (pełne nazwy lub skrócone)."""
    for key in ("hasNextPage", "hn"):
        if key in pagination:
            return bool(pagination[key])
    total_pages = pagination.get("totalPages", pagination.get("tp"))
    current = pagination.get("page", pagination.get("p"))
    if total_pages is not None and current is not None:
        return int(current) < int(total_pages)
    return False