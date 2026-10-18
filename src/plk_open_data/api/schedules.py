"""Rozkład planowy (planned timetable).

Rozkład jest **roczny**: ``scheduleId`` to rok rocznego rozkładu (``25``
oznacza rozkład 2025/2026) i w parze z ``orderId`` tworzy jednoznaczny klucz
pociągu. Co roku identyfikatory się zmieniają, więc nie są warte hardkodowania.

Domyślnie trasy są **przycięte do odcinka** ``from`` → ``to``
(``full_route=False``, by przyspieszyć odpowiedź). Pełną trasę dostaniesz
przez ``full_route=True``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..client import PlkClient
from ..config import DEFAULT_DICTIONARY_TTL
from ..helpers import date_range, ids_param, validate_schedule_filters

#: Rozkład planowy jest statyczny w obrębie rocznego rozkładu, więc trzymamy
#: go w cache na dłuż niż dane real-time.
SCHEDULE_TTL = DEFAULT_DICTIONARY_TTL


def search(
    client: PlkClient,
    *,
    date_from: dt.date | str | None = None,
    date_to: dt.date | str | None = None,
    stations: str | int | list[Any] | None = None,
    from_city: str | None = None,
    to_city: str | None = None,
    from_stations: str | int | list[Any] | None = None,
    to_stations: str | int | list[Any] | None = None,
    carriers_include: str | list[str] | None = None,
    carriers_exclude: str | list[str] | None = None,
    full_route: bool = False,
    dictionaries: bool = True,
    compact: bool = False,
) -> dict[str, Any]:
    """Pobiera rozkład planowy dla zadanych filtrów.

    :param date_from: początek przedziału dat (``RRRR-MM-DD``), domyślnie dziś.
    :param date_to: koniec przedziału dat, domyślnie dziś.
    :param stations: identyfikatory stacji do objęcia — filtr „któkolwiek z tych".
        **Nie łączyć** z ``from_city``/``to_city`` ani ``from_stations``/``to_stations``.
    :param from_city: nazwa miasta początkowego, wymaga ``to_city``.
    :param to_city: nazwa miasta docelowego, wymaga ``from_city``.
    :param from_stations: identyfikatory stacji początkowych, wymaga ``to_stations``.
    :param to_stations: identyfikatory stacji docelowych, wymaga ``from_stations``.
    :param carriers_include: skróty przewoźników do włączenia, np. ``["IC", "KM"]``.
    :param carriers_exclude: skróty przewoźników do wykluczenia.
    :param full_route: ``True`` zwraca całą trasę pociągu zamiast odcinka między
        stacjami filtru. Domyślnie ``False`` — odpowiedź jest wtedy mniejsza.
    :param dictionaries: dołącz słowniki (nazwy stacji, przewoźników, kategorii).
        Przy dużych wynikach warto ustawić ``False``, bo to potrafi być połowa
        objętości odpowiedzi.
    :param compact: użyj wariantu ``/shortened`` (2–3× mniejszy transfer).
        UWAGA: nazwy pól są wtedy skrócone (``rt`` zamiast ``routes``) i **kolidują
        między endpointami** — ``ts`` oznacza ``trains`` w ``/operations``, a
        ``routes`` w ``/schedules``. Używaj wariantu skróconego tylko zaznaczając
        wprost, że operujesz na kluczach skróconych.

    :raises ValidationError: gdy filtry wykluczają się nawzajem lub daty są odwrócone.
    """
    validate_schedule_filters(
        stations=stations,
        **{"from": from_city, "to": to_city},
        fromStations=from_stations,
        toStations=to_stations,
    )

    start, end = date_range(date_from, date_to)

    params: dict[str, Any] = {
        "dateFrom": start,
        "dateTo": end,
        "fullRoute": full_route,
        "dictionaries": dictionaries,
    }
    if stations is not None:
        params["stations"] = ids_param(stations)
    if from_city:
        params["from"] = from_city
    if to_city:
        params["to"] = to_city
    if from_stations is not None:
        params["fromStations"] = ids_param(from_stations)
    if to_stations is not None:
        params["toStations"] = ids_param(to_stations)
    if carriers_include is not None:
        params["carriersInclude"] = (
            carriers_include if isinstance(carriers_include, str) else ",".join(carriers_include)
        )
    if carriers_exclude is not None:
        params["carriersExclude"] = (
            carriers_exclude if isinstance(carriers_exclude, str) else ",".join(carriers_exclude)
        )

    path = "/api/v1/schedules/shortened" if compact else "/api/v1/schedules"
    return client.get(path, params=params, cache_ttl=SCHEDULE_TTL)


def route(client: PlkClient, schedule_id: int, order_id: int) -> dict[str, Any]:
    """Szczegóły jednej trasy pociągu wraz z datami kursowania.

    :param schedule_id: rok rocznego rozkładu (``25`` = 2025/2026).
    :param order_id: identyfikator zamówienia trasy w obrębie rozkładu.
    :raises NotFoundError: gdy taki pociąg nie istnieje w danym rozkładzie.
    """
    return client.get(
        f"/api/v1/schedules/route/{int(schedule_id)}/{int(order_id)}",
        cache_ttl=SCHEDULE_TTL,
    )


def routes_on_date(client: PlkClient, date: dt.date | str) -> list[dict[str, Any]]:
    """Lista identyfikatorów wszystkich tras kursujących w podanym dniu.

    Odpowiedź bywa duża (tysiące pociągów), dlatego idzie do cache.
    """
    from ..helpers import parse_date

    day = parse_date(date)
    if day is None:
        from ..errors import ValidationError

        raise ValidationError("Nie podano daty dla routes_on_date")

    payload = client.get(f"/api/v1/schedules/routes/{day.isoformat()}", cache_ttl=SCHEDULE_TTL)
    if isinstance(payload, dict):
        routes = payload.get("routes")
        if isinstance(routes, list):
            return [r for r in routes if isinstance(r, dict)]
    return []


def route_ids_on_date(client: PlkClient, date: dt.date | str) -> list[tuple[int, int]]:
    """Zwraca pary ``(scheduleId, orderId)`` kursujące w dniu — do dalszego przetwarzania."""
    return [
        (int(r["scheduleId"]), int(r["orderId"]))
        for r in routes_on_date(client, date)
        if r.get("scheduleId") is not None and r.get("orderId") is not None
    ]