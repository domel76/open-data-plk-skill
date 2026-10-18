"""Warstwa wysokopoziomowa: gotowe odpowiedzi na typowe pytania.

Moduły w :mod:`plk_open_data.api` zwracają surowe JSON-y w strukturze API.
Ta warstwa odpowiada na pytania w formie, w której normalnie zadaje się je
człowiekowi:

* *„Pokaż rozkład z Warszawy do Gdańska na jutro"*
* *„Które pociągi są opóźnione na stacji X"*
* *„Jaki jest przebieg pociągu 123 w czasie rzeczywistym"*

Wynik to ``list[dict]`` — z gotowymi znacznikami ``datetime`` zamiast
rozbitych na ``arrivalDay`` + ``arrivalTime``, oraz z nazwami stacji zamiast
identyfikatorów.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .api import operations, schedules
from .client import PlkClient
from .errors import ValidationError
from .helpers import (
    _station_catalog,
    compose_datetime,
    find_city_stations,
    parse_date,
    resolve_city,
    resolve_station,
)

#: Statusy pociągów — czytelne opisy do dołączenia w wynikach.
TRAIN_STATUSES: dict[str, str] = {
    "S": "nie rozpoczął",
    "P": "w trakcie kursu",
    "C": "zakończył kurs",
    "X": "odwołany",
    "Q": "częściowo odwołany",
}


def timetable(
    client: PlkClient,
    *,
    from_station: str | int | None = None,
    to_station: str | int | None = None,
    date: dt.date | str | None = None,
    stations: str | int | list[Any] | None = None,
    carriers: str | list[str] | None = None,
    exclude_carriers: str | list[str] | None = None,
    full_route: bool = False,
    include_dictionaries: bool = True,
) -> list[dict[str, Any]]:
    """Rozkład planowy jako płaska lista rekordów „pociąg × stacja".

    Obsługuje zarówno parę ``from_station``/``to_station``, jak i samo
    ``stations``. Nazwy stacji i miast rozpoznaje automatycznie —
    ``from_station="Warszawa Centralna"`` zadziała tak samo jak ``from_station=38851``.

    :param date: data kursowania (``RRRR-MM-DD``), domyślnie dzisiaj.
    :param full_route: ``True`` zwraca całą trasę pociągu zamiast odcinka.
        Przy dużych miastach i przejazdach nocnych to istotne, bo pociąg
        z Warszawy do Gdańska często wyjeżdża o 23:50 i dociera nad ranem.
    :param include_dictionaries: dołączenie słowników do zapytania. Wyłączenie
        oszczędza **66% transferu** (zmierzone: 246 KB → 84 KB), ale w odpowiedzi
        ``dictionaries`` jest wtedy pusty obiekt. Dlatego przy ``False`` nazwy
        stacji uzupełniamy z lokalnego cache słownika (pobierany raz na dobę),
        żeby pole ``station_name`` nadal było wypełnione.

    :returns: lista rekordów ze składowymi ``train_number``, ``station_name``,
        ``arrival`` i ``departure`` (obiekty :class:`datetime.datetime` lub ``None``).

    :raises ValidationError: gdy nie podano stacji albo parametry są sprzeczne.
    """
    day = parse_date(date) or dt.date.today()

    from_ids: str | int | list[Any] | None = None
    to_ids: str | int | list[Any] | None = None

    if from_station and to_station:
        from_ids = _resolve_any(client, from_station)
        to_ids = _resolve_any(client, to_station)
    elif stations is not None:
        stations = _resolve_any(client, stations)
    elif from_station or to_station:
        from .errors import ValidationError

        missing = "to_station" if from_station else "from_station"
        raise ValidationError(
            f"Podano tylko jedną stronę relacji — brakuje {missing!r}. "
            "Podaj obie stacje albo użyj parametru stations=."
        )
    else:
        from .errors import ValidationError

        raise ValidationError(
            "Podaj stację początkową i docelową (from_station, to_station) "
            "albo listę stacji (stations)."
        )

    payload = schedules.search(
        client,
        date_from=day,
        date_to=day,
        stations=stations,
        from_stations=from_ids,
        to_stations=to_ids,
        carriers_include=carriers,
        carriers_exclude=exclude_carriers,
        full_route=full_route,
        dictionaries=include_dictionaries,
    )

    # Klient przekazujemy zawsze: API potrafi pominąć słowniki w odpowiedzi
    # (cache serwera gubi je przy powtórzonym zapytaniu), a wtedy nazwy
    # uzupełniamy z lokalnego słownika. Kosztuje to jedno zapytanie na dobę.
    return _flatten_schedules(payload, client=client)


def connections(
    client: PlkClient,
    *,
    from_station: str | int | None = None,
    to_station: str | int | None = None,
    date: dt.date | str | None = None,
    carriers: str | list[str] | None = None,
    exclude_carriers: str | list[str] | None = None,
    after: str | dt.time | None = None,
    before: str | dt.time | None = None,
) -> list[dict[str, Any]]:
    """Połączenia między dwoma miejscami — **jeden rekord na pociąg**.

    To odpowiedź na pytanie „co mam do wyboru”, a nie „jak wygląda rozkład”.
    :func:`timetable` zwraca rekord dla każdego przystanku (pociąg × stacja),
    więc jeden pociąg potrafi dać kilkanaście wierszy. Tutaj dostajesz listę
    połączeń: skąd, dokąd, kiedy i ile to trwa.

    :param date: data kursowania (``RRRR-MM-DD``), domyślnie dzisiaj.
    :param from_station: stacja albo miasto początkowe. Miasto (``"Warszawa"``)
        obejmuje wszystkie jego stacje, więc pociąg odjeżdżający z Wschodniej
        też zostanie znaleziony.
    :param to_station: stacja albo miasto docelowe.
    :param after: najwcześniejsza godzina odjazdu, ``"GG:MM"`` lub obiekt
        :class:`datetime.time`. Włącznie.
    :param before: najpóźniejsza godzina odjazdu. Wyłącznie.

    :returns: lista połączeń — **jedno połączenie to jeden rekord**, a nie
        „pociąg × stacja” jak w :func:`timetable`. Składowe: ``train_number``,
        ``carrier_code``, ``commercial_category``, ``from_station``,
        ``to_station``, ``departure``, ``arrival``, ``duration_minutes``,
        ``stops_count``, ``departure_platform``, ``schedule_id``, ``order_id``.
        Gdy miejscowość ma kilka stacji, ``departure_options`` i
        ``arrival_options`` zawierają wszystkie warianty wsiadania i wysiadania
        (np. Wschodnia, Centralna, Zachodnia) wraz z godzinami.

    :raises ValidationError: gdy brakuje stacji albo godziny są sprzeczne.
    """
    if not from_station or not to_station:
        raise ValidationError(
            "Podaj stację początkową i docelową (from_station, to_station) — "
            "nazwę stacji albo nazwę miasta."
        )

    day = parse_date(date) or dt.date.today()
    start = _parse_clock(after, "after")
    end = _parse_clock(before, "before")
    if start and end and start >= end:
        raise ValidationError(
            f"Godzina 'after' ({start:%H:%M}) musi być wcześniejsza niż "
            f"'before' ({end:%H:%M})."
        )

    from_ids = _resolve_place(client, from_station)
    to_ids = _resolve_place(client, to_station)

    payload = schedules.search(
        client,
        date_from=day,
        date_to=day,
        from_stations=from_ids,
        to_stations=to_ids,
        carriers_include=carriers,
        carriers_exclude=exclude_carriers,
        full_route=False,
        # Słowniki to 2/3 transferu, a nazwy i tak mamy z lokalnego słownika.
        dictionaries=False,
    )
    rows = _flatten_schedules(payload, client=client)
    from_set = _id_set(from_ids)
    to_set = _id_set(to_ids)

    # Jeden pociąg = jedna trasa; przystanki trzeba złożyć z powrotem w całość.
    grouped: dict[tuple[Any, Any], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault((row["schedule_id"], row["order_id"]), []).append(row)

    result: list[dict[str, Any]] = []
    for stops in grouped.values():
        stops = [s for s in stops if s.get("station_name")]
        if len(stops) < 2:
            continue

        # W obrębie miasta pociąg może zatrzymać się kilka razy (Wschodnia,
        # Centralna, Zachodnia) — pokazujemy wszystkie warianty wsiadania,
        # a nie tylko pierwszy z brzegu.
        boarding = [s for s in stops if s.get("station_id") in from_set] or [stops[0]]
        alighting = [s for s in stops if s.get("station_id") in to_set] or [stops[-1]]

        first, last = boarding[0], alighting[-1]
        departure = first.get("departure") or first.get("arrival")
        arrival = last.get("arrival") or last.get("departure")
        if not departure or not arrival or arrival < departure:
            continue

        # Filtr godziny patrzy na odjazd z pierwszej stacji w mieście źródłowym.
        clock = departure.time()
        if start and clock < start:
            continue
        if end and clock >= end:
            continue

        result.append(
            {
                "train_number": first.get("train_number"),
                "train_name": first.get("train_name"),
                "carrier_code": first.get("carrier_code"),
                "commercial_category": first.get("commercial_category"),
                "from_station": first.get("station_name"),
                "to_station": last.get("station_name"),
                "departure": departure,
                "arrival": arrival,
                "duration_minutes": int((arrival - departure).total_seconds() // 60),
                "stops_count": len(stops),
                "departure_platform": first.get("departure_platform"),
                "departure_options": [
                    {
                        "station_name": stop.get("station_name"),
                        "departure": stop.get("departure") or stop.get("arrival"),
                    }
                    for stop in boarding
                ],
                "arrival_options": [
                    {
                        "station_name": stop.get("station_name"),
                        "arrival": stop.get("arrival") or stop.get("departure"),
                    }
                    for stop in alighting
                ],
                "schedule_id": first.get("schedule_id"),
                "order_id": first.get("order_id"),
                "operating_dates": first.get("operating_dates"),
            }
        )

    result.sort(key=lambda r: r["departure"])
    return result


def _id_set(value: int | list[int]) -> set[int]:
    """Normalizuje identyfikator lub ich listę do zbioru ``int``."""
    if isinstance(value, int):
        return {value}
    return {int(item) for item in value}


def _parse_clock(value: str | dt.time | None, param_name: str) -> dt.time | None:
    """Zamienia ``"GG:MM"`` (albo ``"GG:MM:SS"``) na :class:`datetime.time`."""
    if value is None:
        return None
    if isinstance(value, dt.time):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return dt.time.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(
            f"Nieprawidłowa godzina w {param_name}: {value!r}. Użyj formatu GG:MM."
        ) from exc


def _resolve_place(client: PlkClient, value: str | int) -> int | list[int]:
    """Zamienia nazwę stacji **albo miasta** na identyfikatory.

    Kolejność jest istotna:

    1. cyfry — już rozwiązany identyfikator,
    2. **dokładna** nazwa miasta — rozwija się na wszystkie jego stacje,
    3. nazwa stacji — jednoznaczna lub najlepiej dopasowana.

    Dzięki temu ``"Warszawa"`` obejmuje Wschodnią, Centralną i Zachodnią,
    a ``"Warszawa Centralna"`` pozostaje jedną stacją.
    """
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)

    city_stations = find_city_stations(client, text)
    if city_stations:
        return city_stations

    try:
        return resolve_station(client, text)
    except ValidationError as exc:
        raise ValidationError(
            f"Nie znaleziono stacji ani miasta {value!r}. "
            f"Sprawdź pisownię: python -m plk_open_data find-station \"{value}\" "
            f"albo find-city \"{value}\"."
        ) from exc


def train_realtime(
    client: PlkClient,
    schedule_id: int,
    order_id: int,
    date: dt.date | str | None = None,
) -> dict[str, Any]:
    """Wykonanie pociągu w czasie rzeczywistym, z opóźnieniami przy każdej stacji.

    :param schedule_id: rok rocznego rozkładu (``25`` = 2025/2026).
    :param order_id: identyfikator zamówienia trasy — oba pochodzą z rozkładu
        (patrz :func:`timetable`, pole ``schedule_id`` w rekordzie).
    """
    day = parse_date(date) or dt.date.today()
    payload = operations.train(client, schedule_id, order_id, day, cache_ttl=None)

    trains = payload.get("trains") if isinstance(payload, dict) else None
    train_data = _first_train(trains) or (payload if isinstance(payload, dict) else {})

    stations_map = _stations_lookup(payload)

    result: dict[str, Any] = {
        "schedule_id": train_data.get("scheduleId"),
        "order_id": train_data.get("orderId"),
        "operating_date": train_data.get("operatingDate"),
        "train_status": train_data.get("trainStatus"),
        "train_status_description": TRAIN_STATUSES.get(
            str(train_data.get("trainStatus")), "nieznany"
        ),
        "stops": [],
    }

    for stop in train_data.get("stations", []) or []:
        if not isinstance(stop, dict):
            continue
        station_id = stop.get("stationId")
        result["stops"].append(
            {
                "station_id": station_id,
                "station_name": stations_map.get(station_id, {}).get("name"),
                "planned_arrival": stop.get("plannedArrival"),
                "actual_arrival": stop.get("actualArrival"),
                "planned_departure": stop.get("plannedDeparture"),
                "actual_departure": stop.get("actualDeparture"),
                "arrival_delay_minutes": stop.get("arrivalDelayMinutes"),
                "departure_delay_minutes": stop.get("departureDelayMinutes"),
                "cancelled": stop.get("isCancelled"),
                "confirmed": stop.get("isConfirmed"),
            }
        )

    result["max_delay_minutes"] = _max_delay(result["stops"])
    return result


def delays_at_station(
    client: PlkClient,
    station: str | int,
    *,
    min_delay_minutes: int = 5,
    date: dt.date | str | None = None,
    carriers: str | list[str] | None = None,
) -> list[dict[str, Any]]:
    """Pociągi przechodzące przez stację wraz z opóźnieniem i czasem przejazdu.

    Zwraca rekordy posortowane od najbardziej opóźnionego. Przekroczenie progu
    (``min_delay_minutes``) działa jako filtr — opóźnione pociągi na stacji są
    zwykle tym, czego szukasz.

    :param station: nazwa albo identyfikator stacji.
    :param min_delay_minutes: próg odrzucający pociągi poniżej opóźnienia.
    """
    station_id = _resolve_any(client, station)

    records: list[dict[str, Any]] = []
    for train_data in operations.iter_operations(
        client,
        stations=station_id,
        carriers_include=carriers,
        with_planned=True,
        cache_ttl=None,
    ):
        for stop in train_data.get("stations", []) or []:
            if not isinstance(stop, dict) or stop.get("stationId") != station_id:
                continue

            delay = stop.get("arrivalDelayMinutes")
            if stop.get("isCancelled"):
                delay = None
            if delay is None or delay < min_delay_minutes:
                continue

            records.append(
                {
                    "train_number": _train_number(train_data, stop),
                    "carrier_code": train_data.get("carrierCode"),
                    "operating_date": train_data.get("operatingDate"),
                    "train_status": train_data.get("trainStatus"),
                    "train_status_description": TRAIN_STATUSES.get(
                        str(train_data.get("trainStatus")), "nieznany"
                    ),
                    "delay_minutes": delay,
                    "planned_arrival": stop.get("plannedArrival"),
                    "actual_arrival": stop.get("actualArrival"),
                    "actual_departure": stop.get("actualDeparture"),
                    "cancelled": stop.get("isCancelled"),
                    "schedule_id": train_data.get("scheduleId"),
                    "order_id": train_data.get("orderId"),
                }
            )
            break  # jedna stacja = jeden rekord na pociąg

    records.sort(key=lambda r: r["delay_minutes"], reverse=True)
    return records


def _flatten_schedules(
    payload: dict[str, Any], *, client: PlkClient | None = None
) -> list[dict[str, Any]]:
    """Spłaszcza ``routes[]`` do rekordów „pociąg × stacja” z gotowymi datami.

    :param client: gdy podany, a odpowiedź nie zawiera słownika stacji, nazwy
        są uzupełniane z lokalnego słownika (pobierany raz na dobę). Dzięki temu
        ``include_dictionaries=False`` nie kosztuje utraty nazw stacji, a kaprysy
        serwerowego cache — który potrafi oddać odpowiedź bez słowników mimo
        ``dictionaries=true`` — nie psują wyniku.
    """
    if not isinstance(payload, dict):
        return []

    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes:
        # Pusta lista: nie ma czego wzbogacać, więc nie ciągniemy słownika.
        return []

    stations_map = _stations_lookup(payload)
    if not stations_map and client is not None:
        stations_map = {s["id"]: s for s in _station_catalog(client) if s.get("id")}
    stop_types = _stop_types_lookup(payload)

    records: list[dict[str, Any]] = []
    for route in routes:
        if not isinstance(route, dict):
            continue

        schedule_id = route.get("scheduleId")
        order_id = route.get("orderId")
        operating_dates = route.get("operatingDates") or []

        for stop in route.get("stations", []) or []:
            if not isinstance(stop, dict):
                continue

            station_id = stop.get("stationId")
            station_info = stations_map.get(station_id, {})
            stop_type_id = stop.get("stopTypeId")

            records.append(
                {
                    "schedule_id": schedule_id,
                    "order_id": order_id,
                    "train_order_id": route.get("trainOrderId"),
                    "train_number": stop.get("arrivalTrainNumber")
                    or stop.get("departureTrainNumber"),
                    "train_name": route.get("name"),
                    "carrier_code": route.get("carrierCode"),
                    "commercial_category": route.get("commercialCategorySymbol"),
                    "national_number": route.get("nationalNumber"),
                    "international_arrival_number": route.get("internationalArrivalNumber"),
                    "international_departure_number": route.get("internationalDepartureNumber"),
                    "operating_dates": operating_dates,
                    "station_id": station_id,
                    "station_name": station_info.get("name"),
                    "arrival": compose_datetime(
                        _operating_date(operating_dates),
                        stop.get("arrivalDay"),
                        stop.get("arrivalTime"),
                    ),
                    "departure": compose_datetime(
                        _operating_date(operating_dates),
                        stop.get("departureDay"),
                        stop.get("departureTime"),
                    ),
                    "arrival_day_offset": stop.get("arrivalDay"),
                    "departure_day_offset": stop.get("departureDay"),
                    "arrival_platform": stop.get("arrivalPlatform"),
                    "departure_platform": stop.get("departurePlatform"),
                    "arrival_track": stop.get("arrivalTrack"),
                    "departure_track": stop.get("departureTrack"),
                    "arrival_commercial_category": stop.get("arrivalCommercialCategory"),
                    "departure_commercial_category": stop.get("departureCommercialCategory"),
                    "stop_type_id": stop_type_id,
                    "stop_type": stop_types.get(stop_type_id)
                    or stop.get("stopTypeName"),
                }
            )

    return records


# ---------------------------------------------------------------------- #
# Pomocnicze
# ---------------------------------------------------------------------- #


def _resolve_any(client: PlkClient, value: str | int | list[Any]) -> str | int | list[Any]:
    """Identyfikator przechodzi bez zmian; nazwa stacji jest rozwiązywana na numer.

    Cyfry uznajemy za już rozwiązany identyfikator, więc ``"38851"`` i ``38851``
    dają to samo. Pozostałe wartości traktujemy jako nazwę stacji i rozpoznajemy
    ją przez :func:`~plk_open_data.helpers.resolve_station`, która ignoruje
    wielkość liter i polskie znaki. Miasta (aglomeracje) mają osobną ścieżkę —
    :func:`resolve_city_stations`.
    """
    if isinstance(value, int):
        return value
    if isinstance(value, (list, tuple)):
        return [_resolve_any(client, v) for v in value]
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
        return resolve_station(client, text)
    return value


def _operating_date(operating_dates: list[str]) -> str | None:
    """Pierwsza data kursowania — baza do składania znaczników czasu."""
    return operating_dates[0] if operating_dates else None


def _stations_lookup(payload: dict[str, Any]) -> dict[Any, dict[str, Any]]:
    """Buduje mapę ``id stacji -> {id, name}`` ze słownika w odpowiedzi.

    Uwaga na dwa różne miejsca: rozkład planowy trzyma nazwy w
    ``dictionaries.stations``, a odpowiedź ``/operations`` — na najwyższym
    poziomie jako ``stations``. Sprawdzamy oba.
    """
    candidates: list[Any] = []
    if isinstance(payload, dict):
        dictionaries = payload.get("dictionaries")
        if isinstance(dictionaries, dict):
            candidates.append(dictionaries.get("stations"))
        candidates.append(payload.get("stations"))

    lookup: dict[Any, dict[str, Any]] = {}
    for raw in candidates:
        if isinstance(raw, dict):
            for key, value in raw.items():
                if isinstance(value, dict):
                    lookup[value.get("id", key)] = value
                elif isinstance(value, str):
                    lookup[key] = {"id": key, "name": value}
        elif isinstance(raw, list):
            for value in raw:
                if isinstance(value, dict) and value.get("id") is not None:
                    lookup[value["id"]] = value
    return lookup


def _stop_types_lookup(payload: dict[str, Any]) -> dict[Any, str]:
    """Buduje mapę ``id typu postoju -> nazwa`` ze słownika w odpowiedzi."""
    raw = payload.get("dictionaries", {}).get("stopTypes") if isinstance(payload, dict) else None
    lookup: dict[Any, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            lookup[key] = value if isinstance(value, str) else str(
                (value or {}).get("name") or (value or {}).get("description") or key
            )
    elif isinstance(raw, list):
        for value in raw:
            if isinstance(value, dict) and value.get("id") is not None:
                lookup[value["id"]] = str(value.get("name") or value.get("description") or "")
    return lookup


def _first_train(trains: Any) -> dict[str, Any] | None:
    if isinstance(trains, list):
        return next((t for t in trains if isinstance(t, dict)), None)
    return None


def _train_number(train_data: dict[str, Any], stop: dict[str, Any]) -> str | None:
    """Numer pociągu — z danego przystanku, bo zmienia się na trasie.

    Pociąg oznaczenie „12345" może mieć jako numer krajowy, a na różnych
    odcinkach występować pod różnym numerem międzynarodowym.
    """
    return (
        stop.get("arrivalTrainNumber")
        or stop.get("departureTrainNumber")
        or train_data.get("nationalNumber")
    )


def _max_delay(stops: list[dict[str, Any]]) -> int | None:
    delays = [
        s[field]
        for s in stops
        for field in ("arrival_delay_minutes", "departure_delay_minutes")
        if isinstance(s.get(field), (int, float))
    ]
    return max(delays) if delays else None


def resolve_city_stations(client: PlkClient, city: str) -> list[int]:
    """Publicznie eksponowany skrót na :func:`plk_open_data.helpers.resolve_city`."""
    return resolve_city(client, city)