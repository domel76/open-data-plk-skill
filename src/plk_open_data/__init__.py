"""plk-open-data — klient Pythona do Open Railway Data API (PKP PLK).

Szybki start::

    from plk_open_data import PlkClient, timetable

    with PlkClient() as client:
        for pociag in timetable(client, from_station="Warszawa Centralna",
                               to_station="Gdańsk Główny", date="2026-10-19"):
            print(pociag["train_number"], pociag["departure"], pociag["station_name"])

Warstwa niskopoziomowa (:mod:`plk_open_data.api`) zwraca surowe JSON-y w strukturze
API, warstwa wysokopoziomowa (:mod:`plk_open_data.highlevel`) — gotowe
``list[dict]``. Klucz API brany jest ze zmiennej ``PLK_API_KEY`` albo z pliku ``.env``.
"""

from __future__ import annotations

from . import api, highlevel
from .cache import DiskCache
from .client import PlkClient, UsageCounter
from .config import Settings, build_settings, load_api_key
from .errors import (
    AuthenticationError,
    ConfigurationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    PlkError,
    RateLimitError,
    ServerError,
    TransportError,
    ValidationError,
)
from .helpers import (
    compose_datetime,
    normalize_name,
    parse_date,
    parse_time,
    resolve_carrier,
    resolve_city,
    resolve_station,
    search_stations,
)
from .highlevel import connections, delays_at_station, timetable, train_realtime

__version__ = "0.1.0"

__all__ = [
    # Klient
    "PlkClient",
    "UsageCounter",
    "DiskCache",
    "Settings",
    "build_settings",
    "load_api_key",
    # Warstwa wysokopoziomowa
    "timetable",
    "connections",
    "train_realtime",
    "delays_at_station",
    # Pomocnicze
    "compose_datetime",
    "normalize_name",
    "parse_date",
    "parse_time",
    "resolve_station",
    "resolve_city",
    "resolve_carrier",
    "search_stations",
    # Błędy
    "PlkError",
    "ConfigurationError",
    "AuthenticationError",
    "PermissionDeniedError",
    "RateLimitError",
    "NotFoundError",
    "ValidationError",
    "ConflictError",
    "ServerError",
    "TransportError",
    # Moduły
    "api",
    "highlevel",
    "__version__",
]