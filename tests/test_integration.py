"""Testy integracyjne — uruchamiane tylko z prawdziwym kluczem API.

Domyślnie są pomijane, więc ``pytest`` działa bez klucza i bez internetu.
Aby je włączyć::

    PLK_API_KEY=twoj-klucz pytest -m integration

Testy sprawdzają kontrakt, którego nie da się zweryfikować na mockach: czy API
faktycznie zwraca pola, na których opiera się biblioteka. Każdy z nich zjada
zapytanie z limitu, więc jest ich świadomie mało.

Testy z tego pliku **nie** kasują cache — używają klienta z katalogiem tymczasowym,
żeby nie nadpisywać cache z katalogu projektu.
"""

from __future__ import annotations

import datetime as dt
import os

import pytest

from plk_open_data import compose_datetime, connections
from plk_open_data.api import dictionaries, operations, schedules
from plk_open_data.client import PlkClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("PLK_API_KEY"),
        reason="Wymaga ustawionej zmiennej PLK_API_KEY",
    ),
]


@pytest.fixture(scope="module")
def live_client(tmp_path_factory: pytest.TempPathFactory) -> PlkClient:
    cache_dir = tmp_path_factory.mktemp("plk-live-cache")
    with PlkClient(cache_dir=cache_dir) as client:
        yield client


def test_klucz_dziala(live_client: PlkClient) -> None:
    payload = dictionaries.api_version()
    assert payload.get("version")


def test_slownik_stacji_odpowiada(live_client: PlkClient) -> None:
    stations = dictionaries.list_stations(live_client, search="Warszawa", page_size=5)
    assert stations, "Słownik stacji powinien zwrócić wyniki dla 'Warszawa'"
    assert all("id" in s and "name" in s for s in stations)


def test_dane_slowika_maja_rok(live_client: PlkClient) -> None:
    data = dictionaries.data_version(live_client)
    assert data.get("dataVersion")


def test_liczenie_stacji_zwraca_liczbe(live_client: PlkClient) -> None:
    assert dictionaries.stations_count(live_client) > 0


def test_przewoznicy_maja_skroty(live_client: PlkClient) -> None:
    carriers = dictionaries.list_carriers(live_client)
    assert carriers
    assert all("code" in c and "name" in c for c in carriers)


def test_miasta_maja_id_stacji(live_client: PlkClient) -> None:
    cities = dictionaries.list_cities(live_client, search="WARSZAWA")
    assert cities
    assert any(c.get("stationIds") for c in cities)


def test_rozklad_ma_oczekiwany_ksztalt(live_client: PlkClient) -> None:
    """Sprawdza realną strukturę rozkładu — najważniejszy kontrakt biblioteki.

    Ustalone na żywo (2026-10-20): pola ``arrivalDay``/``departureDay`` są
    **pomijane, gdy offset wynosi 0**. Pojawiają się dopiero przy przejściu
    przez północ, więc nie można ich wymagać na konkretnym przystanku —
    trzeba sprawdzić, czy występują gdziekolwiek w trasie.
    """
    today = dt.date.today().isoformat()
    payload = schedules.search(
        live_client, from_city="WARSZAWA", to_city="GDAŃSK", date_from=today, date_to=today
    )

    assert "routes" in payload, payload.keys()
    if not payload["routes"]:
        pytest.skip("Brak pociągów na dziś w tym kierunku")

    route = payload["routes"][0]
    assert "scheduleId" in route
    assert "orderId" in route
    assert route.get("operatingDates"), "Trasa musi mieć daty kursowania"

    stop = route["stations"][0]
    assert any(key in stop for key in ("arrivalTime", "departureTime"))

    # Offset doby jest opcjonalny — sprawdzamy, że gdy występuje, jest liczbą.
    all_stops = [s for r in payload["routes"] for s in r.get("stations", [])]
    day_values = [
        s[key] for s in all_stops for key in ("arrivalDay", "departureDay") if key in s
    ]
    assert all(isinstance(v, int) for v in day_values)


def test_dictionaries_false_zmniejsza_odpowiedz(live_client: PlkClient) -> None:
    """Pokazuje realną oszczędność ``dictionaries=false``.

    Uwaga na zachowanie API ustalone na żywo (2026-10-20): serwer cache'uje
    odpowiedzi i **przy powtórzonym zapytaniu gubi słowniki**, mimo
    ``dictionaries=true``. Dlatego test nie może wymagać, żeby wariant ``true``
    zawsze zawierał słownik — sprawdza to, co jest gwarantowane.
    """
    import json

    # Inna data niż w pozostałych testach — zmniejsza szansę na trafienie
    # w serwerowy cache, który obcina słowniki.
    day = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    params = {"from_city": "KRAKÓW", "to_city": "WROCŁAW", "date_from": day, "date_to": day}

    with_dicts = schedules.search(live_client, dictionaries=True, **params)
    without_dicts = schedules.search(live_client, dictionaries=False, **params)

    # ``false`` zawsze daje pusty obiekt — API nie usuwa klucza.
    assert not without_dicts.get("dictionaries")

    if not with_dicts.get("dictionaries"):
        pytest.skip(
            "Serwerowy cache API oddał odpowiedź bez słowników mimo dictionaries=true "
            "(znane zachowanie z 2026-10-20) — nie da się zmierzyć oszczędności."
        )

    size_with = len(json.dumps(with_dicts, ensure_ascii=False).encode())
    size_without = len(json.dumps(without_dicts, ensure_ascii=False).encode())
    assert size_without < size_with
    # Na żywo wychodzi ok. 66% oszczędności — wymagamy co najmniej połowy.
    assert size_without < size_with * 0.6


def test_polaczenia_jeden_rekord_na_pociag(live_client: PlkClient) -> None:
    """``connections()`` musi zwracać połączenia, nie przystanki."""
    day = (dt.date.today() + dt.timedelta(days=25)).isoformat()
    rows = connections(
        live_client,
        from_station="WARSZAWA",
        to_station="ZAKOPANE",
        date=day,
        after="04:00",
        before="11:00",
    )
    if not rows:
        pytest.skip("Brak połączeń w tym dniu w podanym oknie godzinowym")

    assert len({r["train_number"] for r in rows}) == len(rows), "każdy pociąg tylko raz"
    assert rows == sorted(rows, key=lambda r: r["departure"])
    assert all(r["duration_minutes"] > 0 for r in rows)
    assert all(r["departure"].hour < 11 for r in rows)
    assert all(r["from_station"] for r in rows)
    # Miasto źródłowe ma wiele stacji — rekord powinien je wymieniać.
    assert any(len(r["departure_options"]) > 1 for r in rows)


def test_składanie_danych_dziala_na_prawdziwej_odpowiedzi(live_client: PlkClient) -> None:
    """Kompozycja daty z rozbitych pól musi dać wynik zgodny z oczekiwaniem."""
    today = dt.date.today().isoformat()
    payload = schedules.search(
        live_client, from_city="WARSZAWA", to_city="GDAŃSK", date_from=today, date_to=today
    )

    routes = payload.get("routes") or []
    if not routes:
        pytest.skip("Brak pociągów na dziś w tym kierunku")

    route = routes[0]
    operating_dates = route.get("operatingDates") or []
    if not operating_dates:
        pytest.skip("Pociąg bez dat kursowania")

    for stop in route.get("stations", []):
        arrival = compose_datetime(
            operating_dates[0], stop.get("arrivalDay"), stop.get("arrivalTime")
        )
        departure = compose_datetime(
            operating_dates[0], stop.get("departureDay"), stop.get("departureTime")
        )
        # Godzina po składaniu musi mieścić się w dobie
        assert arrival is None or 0 <= arrival.hour <= 23
        assert departure is None or 0 <= departure.hour <= 23
        # Offset nie powinien przesunąć daty bardziej niż o kilka dni
        assert arrival is None or abs((arrival.date() - dt.date.fromisoformat(operating_dates[0])).days) <= 3
        break


def test_statystyki_odpowiadają(live_client: PlkClient) -> None:
    stats = operations.statistics(live_client)
    assert "totalTrains" in stats


def test_operacje_z_opoznieniami(live_client: PlkClient) -> None:
    station_ids = dictionaries.list_stations(live_client, search="Warszawa Centralna", page_size=1)
    if not station_ids:
        pytest.skip("Nie znaleziono stacji")

    station_id = station_ids[0]["id"]
    payload = operations.list_operations(
        live_client, stations=station_id, with_planned=True, page_size=10, cache_ttl=None
    )
    assert "trains" in payload or payload.get("trains") == []
    # Strona odpowiedzi zgadza się z tym, czego oczekujemy po paginacji
    pagination = payload.get("pagination")
    if pagination:
        assert "page" in pagination and "hasNextPage" in pagination


def test_utrudnienia_maja_kody_typow(live_client: PlkClient) -> None:
    from plk_open_data.api import disruptions

    payload = disruptions.list_disruptions(live_client, dictionaries=True, cache_ttl=None)
    assert "disruptions" in payload
    for item in payload.get("disruptions", [])[:5]:
        assert "message" in item


def test_cache_zapisuje_i_uzywa_wolniej(live_client: PlkClient) -> None:
    """Drugie identyczne zapytanie nie powinno wykonać zapytania do API."""
    first = dictionaries.list_carriers(live_client)
    requests_before = live_client.usage.requests_made

    second = dictionaries.list_carriers(live_client)

    assert len(first) == len(second)
    assert live_client.usage.requests_made == requests_before, "Powinien trafić w cache"


def test_cache_nie_zawiera_klucza(live_client: PlkClient) -> None:
    """W plikach cache nie może być śladu klucza API."""
    entries = live_client.cache.entries()
    assert entries, "Cache powinien zawierać wpisy po poprzednich zapytaniach"
    key = live_client.settings.api_key
    for entry in entries[:20]:
        assert key not in entry.get("key", "")