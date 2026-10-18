"""Utrudnienia w komunikacji kolejowej.

Zwracane są jako lista komunikatów z odkodowanym typem (``disruptionTypeCode``,
np. ``"utr_32"``) oraz odcinkiem, którego dotyczą (``startStationId`` →
``endStationId``). Pełny opis typu jest dostępny w słowniku ``DisruptionTypes``,
który API dołącza do odpowiedzi przy ``dictionaries=True``.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from ..client import PlkClient
from ..config import DEFAULT_LIVE_TTL
from ..helpers import date_range, ids_param


def list_disruptions(
    client: PlkClient,
    *,
    date_from: dt.date | str | None = None,
    date_to: dt.date | str | None = None,
    stations: str | int | list[Any] | None = None,
    carriers_include: str | list[str] | None = None,
    carriers_exclude: str | list[str] | None = None,
    dictionaries: bool = True,
    compact: bool = False,
    cache_ttl: int | None = DEFAULT_LIVE_TTL,
) -> dict[str, Any]:
    """Pobiera utrudnienia dla wskazanego zakresu dat i stacji.

    :param date_from: początek zakresu, domyślnie dziś.
    :param date_to: koniec zakresu, domyślnie dziś.
    :param stations: identyfikatory stacji. Domyślnie wszystkie.
    :param dictionaries: dołącz słowniki ``DisruptionTypes`` i stacji, dzięki
        którym ``disruptionTypeCode`` da się odczytać. Warto ustawić ``False``,
        gdy interesują Cię same komunikaty.
    :param compact: wariant ``/shortened`` (krótkie, **kolidujące** nazwy pól).
    """
    start, end = date_range(date_from, date_to)

    params: dict[str, Any] = {
        "dateFrom": start,
        "dateTo": end,
        "dictionaries": dictionaries,
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

    path = "/api/v1/disruptions/shortened" if compact else "/api/v1/disruptions"
    return client.get(path, params=params, cache_ttl=cache_ttl)


def disruptions_for_station(
    client: PlkClient,
    station: str | int | list[Any],
    *,
    date: dt.date | str | None = None,
    include_dictionaries: bool = True,
) -> list[dict[str, Any]]:
    """Utrudnienia dotyczące jednej stacji (lub listy stacji) w danym dniu.

    :param station: identyfikator albo lista identyfikatorów stacji.
    """
    day = date or dt.date.today()
    payload = list_disruptions(
        client,
        date_from=day,
        date_to=day,
        stations=station,
        dictionaries=include_dictionaries,
    )
    if isinstance(payload, dict):
        disruptions = payload.get("disruptions")
        if isinstance(disruptions, list):
            return [d for d in disruptions if isinstance(d, dict)]
    return []


def disruptions_with_types(
    client: PlkClient,
    *,
    date: dt.date | str | None = None,
    stations: str | int | list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Utrudnienia z doklejonym opisem typu (``disruptionTypeDescription``).

    API zwraca typ tylko jako kod (``"utr_32"``). Ta funkcja rozwiązuje kod na
    opis ze słownika ``disruptionTypes`` dołączonego do odpowiedzi.
    """
    payload = list_disruptions(
        client,
        date_from=date,
        date_to=date,
        stations=stations,
        dictionaries=True,
    )
    if not isinstance(payload, dict):
        return []

    disruptions = payload.get("disruptions")
    if not isinstance(disruptions, list):
        return []

    types = _build_type_lookup(payload)
    result: list[dict[str, Any]] = []
    for item in disruptions:
        if not isinstance(item, dict):
            continue
        enriched = dict(item)
        code = item.get("disruptionTypeCode")
        enriched["disruptionTypeDescription"] = types.get(code, code)
        result.append(enriched)
    return result


def _build_type_lookup(payload: dict[str, Any]) -> dict[str, str]:
    """Buduje mapę ``kod -> opis`` ze słownika dołączonego do odpowiedzi."""
    lookup: dict[str, str] = {}
    for key in ("disruptionTypes", "dt"):
        raw = payload.get(key)
        if not isinstance(raw, dict):
            continue
        # Słownik bywa mapą {"utr_32": "Opis"} albo listą obiektów.
        for code, value in raw.items():
            if isinstance(value, str):
                lookup[code] = value
            elif isinstance(value, dict):
                lookup[code] = str(value.get("description") or value.get("name") or code)
    return lookup