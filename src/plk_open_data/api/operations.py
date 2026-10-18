"""Wykonanie pociągów w czasie rzeczywistym: opóźnienia, odwołania, statusy.

Status pociągu (``trainStatus``):

==========  =========================================
``S``       nie rozpoczął (``NotStarted``)
``P``       w trakcie kursu (``InProgress``)
``C``       zakończył kurs (``Completed``)
``X``       odwołany (``Cancelled``)
``Q``       częściowo odwołany (``PartialCancelled``)
==========  =========================================

Dane real-time zmieniają się z minuty na minutę, więc cache jest krótki
(minuta domyślnie) albo wyłączony (``cache_ttl=None``).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from typing import Any

from ..client import PlkClient
from ..config import DEFAULT_LIVE_TTL
from ..helpers import ids_param, parse_date

#: Twardy limit ``pageSize`` narzucony przez API.
MAX_PAGE_SIZE = 5000


def list_operations(
    client: PlkClient,
    *,
    stations: str | int | list[Any] | None = None,
    carriers_include: str | list[str] | None = None,
    carriers_exclude: str | list[str] | None = None,
    full_routes: bool = False,
    with_planned: bool = False,
    page: int = 1,
    page_size: int = 1000,
    dictionaries: bool = True,
    compact: bool = False,
    cache_ttl: int | None = DEFAULT_LIVE_TTL,
) -> dict[str, Any]:
    """Pobiera wykonanie pociągów przechodzących przez wskazane stacje.

    :param stations: identyfikatory stacji. Domyślnie **wszystkie stacje** — przy
        limicie 100 zapytań na godzinę lepiej zawęzić, bo wynik bywa bardzo duży.
    :param full_routes: ``True`` zwraca całą trasę pociągu zamiast odcinka przy
        podanych stacjach.
    :param with_planned: ``True`` dołącza planowe czasy oraz **obliczone opóźnienia
        w minutach**. Bez tego flaga nie dostaniesz żadnych opóźnień.
    :param page: numer strony, od 1.
    :param page_size: rekordów na stronę, maksymalnie 5000.
    :param cache_ttl: czas życia cache. ``None`` wyłącza cache dla tego zapytania —
        sensowne, gdy interesują Cię dane sprzed chwili.
    :param compact: wariant ``/shortened`` (krótkie, **kolidujące** nazwy pól).
    """
    params: dict[str, Any] = {
        "page": page,
        "pageSize": min(page_size, MAX_PAGE_SIZE),
        "fullRoutes": full_routes,
        "withPlanned": with_planned,
    }
    if stations is not None:
        params["stations"] = ids_param(stations)
    if carriers_include is not None:
        params["carriersInclude"] = (
            carriers_include if isinstance(carriers_include, str) else ",".join(carriers_include)
        )
    if carriers_exclude is not None:
        params["carriersExclude"] = (
            carriers_exclude if isinstance(carriers_exclude, str) else ",".join(carriers_exclude)
        )

    path = "/api/v1/operations/shortened" if compact else "/api/v1/operations"
    return client.get(path, params=params, cache_ttl=cache_ttl)


def iter_operations(
    client: PlkClient,
    *,
    stations: str | int | list[Any] | None = None,
    carriers_include: str | list[str] | None = None,
    carriers_exclude: str | list[str] | None = None,
    full_routes: bool = False,
    with_planned: bool = True,
    page_size: int = 5000,
    max_pages: int | None = None,
    cache_ttl: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Iteruje po pociągach, automatycznie pobierając kolejne strony.

    Używaj zamiast ręcznej pętli po ``page``. Każda strona to jedno zapytanie,
    więc przy limicie 100/godz. ustaw ``page_size`` rozsądnie.

    :param with_planned: domyślnie ``True``, bo opóźnienia są głównym powodem
        sięgania po ten endpoint.
    :param max_pages: bezpiecznik liczby zapytań.
    """
    params: dict[str, Any] = {
        "pageSize": min(page_size, MAX_PAGE_SIZE),
        "fullRoutes": full_routes,
        "withPlanned": with_planned,
    }
    if stations is not None:
        params["stations"] = ids_param(stations)
    if carriers_include is not None:
        params["carriersInclude"] = (
            carriers_include if isinstance(carriers_include, str) else ",".join(carriers_include)
        )
    if carriers_exclude is not None:
        params["carriersExclude"] = (
            carriers_exclude if isinstance(carriers_exclude, str) else ",".join(carriers_exclude)
        )

    yield from client.paginate(
        "/api/v1/operations",
        params=params,
        items_key="trains",
        max_pages=max_pages,
        cache_ttl=cache_ttl,
    )


def train(
    client: PlkClient,
    schedule_id: int,
    order_id: int,
    operating_date: dt.date | str,
    *,
    cache_ttl: int | None = DEFAULT_LIVE_TTL,
) -> dict[str, Any]:
    """Szczegóły wykonania jednego pociągu w konkretnym dniu.

    :param schedule_id: rok rocznego rozkładu (``25`` = 2025/2026).
    :param order_id: identyfikator zamówienia trasy.
    :param operating_date: data kursowania (``RRRR-MM-DD``).
    :raises NotFoundError: gdy pociąg nie kursuje w tym dniu.
    """
    day = parse_date(operating_date)
    if day is None:
        from ..errors import ValidationError

        raise ValidationError("Nie podano daty kursowania dla train()")

    return client.get(
        f"/api/v1/operations/train/{int(schedule_id)}/{int(order_id)}/{day.isoformat()}",
        cache_ttl=cache_ttl,
    )


def statistics(client: PlkClient, date: dt.date | str | None = None) -> dict[str, Any]:
    """Statystyki statusów pociągów dla dnia (domyślnie dzisiejszego).

    Tanie zapytanie do health-checku: sprawdza, czy klucz działa i czy API
    odpowiada, nie zużywając przy tym dużo limitu.
    """
    params = {"date": parse_date(date).isoformat()} if date is not None else None
    return client.get("/api/v1/operations/statistics", params=params, cache_ttl=60)


def delayed_trains(
    client: PlkClient,
    *,
    min_delay_minutes: int = 5,
    stations: str | int | list[Any] | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Zwraca pociągi z opóźnieniem co najmniej ``min_delay_minutes``.

    Wymaga ``with_planned=True`` — bez tego API nie zwraca pól opóźnienia.
    """
    kwargs["with_planned"] = True
    delayed: list[dict[str, Any]] = []

    for train_data in iter_operations(client, stations=stations, **kwargs):
        worst = _worst_delay(train_data)
        if worst is not None and worst >= min_delay_minutes:
            delayed.append(train_data)

    return delayed


def _worst_delay(train_data: dict[str, Any]) -> int | None:
    """Największe opóźnienie wśród stacji pociągu (przyjazd lub odjazd)."""
    worst: int | None = None
    for station in train_data.get("stations", []) or []:
        if not isinstance(station, dict):
            continue
        for field in ("arrivalDelayMinutes", "departureDelayMinutes"):
            value = station.get(field)
            if isinstance(value, (int, float)):
                worst = value if worst is None else max(worst, value)
    return worst