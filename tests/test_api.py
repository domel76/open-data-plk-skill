"""Testy warstwy wysokopoziomowej i modułów API na atrapowanych odpowiedziach.

Sprawdzamy przede wszystkim to, co jest wartością dodaną biblioteki: spłaszczenie
rozkładu do rekordów „pociąg × stacja", rozwiązanie nazw na identyfikatory
i składanie rozbitych dat w poprawne znaczniki czasu.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.conftest import FakeTransport

from plk_open_data import highlevel
from plk_open_data.api import disruptions, operations, schedules
from plk_open_data.errors import ValidationError

#: Odpowiedź `/dictionaries/stations` — pełny słownik pobierany raz na dobę.
STATION_SEARCH_RESPONSE = {
    "stations": [
        {"id": 38851, "name": "Warszawa Centralna"},
        {"id": 247482, "name": "Gdańsk Główny"},
    ],
    "totalCount": 2,
    "totalPages": 1,
}

#: Odpowiedź `/dictionaries/cities` — dokładne nazwy miast rozwijają się na stacje.
CITIES_RESPONSE = {
    "cities": [
        {"name": "WARSZAWA", "stationIds": [38851, 38852], "stationCount": 2},
        {"name": "ZAKOPANE", "stationIds": [247482], "stationCount": 1},
    ],
    "totalPages": 1,
}

#: Słownik stacji odpowiadający powyższym miastom.
CITY_STATIONS_RESPONSE = {
    "stations": [
        {"id": 38851, "name": "Warszawa Centralna"},
        {"id": 38852, "name": "Warszawa Wschodnia"},
        {"id": 247482, "name": "Zakopane"},
    ],
    "totalCount": 3,
    "totalPages": 1,
}

#: Dwa pociągi: dzienny i nocny (przejście przez północ).
CONNECTIONS_RESPONSE: dict[str, Any] = {
    "routes": [
        {
            "scheduleId": 2026,
            "orderId": 1,
            "trainOrderId": 11,
            "carrierCode": "IC",
            "nationalNumber": "1350",
            "commercialCategorySymbol": "EIC",
            "operatingDates": ["2026-11-09"],
            "stations": [
                {
                    "stationId": 38851,
                    "departureTrainNumber": "1350",
                    "departureTime": "07:35:00",
                    "departurePlatform": "4",
                },
                {
                    "stationId": 247482,
                    "arrivalTrainNumber": "1350",
                    "arrivalTime": "12:17:00",
                },
            ],
        },
        {
            "scheduleId": 2026,
            "orderId": 2,
            "trainOrderId": 12,
            "carrierCode": "IC",
            "nationalNumber": "1170",
            "commercialCategorySymbol": "TLK",
            "operatingDates": ["2026-11-09"],
            "stations": [
                {
                    "stationId": 38851,
                    "departureTrainNumber": "1170",
                    "departureTime": "23:13:00",
                },
                {
                    "stationId": 247482,
                    "arrivalTrainNumber": "1170",
                    "arrivalTime": "06:04:00",
                    "arrivalDay": 1,
                },
            ],
        },
    ],
}

#: Fragment rzeczywistego rozkładu: pociąg nocny przekraczający północ.
SCHEDULE_RESPONSE: dict[str, Any] = {
    "generatedAt": "2026-10-18T10:00:00Z",
    "routes": [
        {
            "scheduleId": 25,
            "orderId": 12345,
            "trainOrderId": 12345,
            "name": "IC 141 GDAŃSK",
            "carrierCode": "IC",
            "nationalNumber": "41141",
            "commercialCategorySymbol": "IC",
            "operatingDates": ["2026-10-19"],
            "stations": [
                {
                    "stationId": 38851,
                    "orderNumber": 1,
                    "departureTrainNumber": "141",
                    "departureTime": "23:50:00",
                    "departureDay": 0,
                    "departurePlatform": "9",
                    "departureTrack": "4",
                    "departureCommercialCategory": "IC",
                },
                {
                    "stationId": 247482,
                    "orderNumber": 2,
                    "arrivalTrainNumber": "141",
                    "arrivalTime": "05:50:00",
                    "arrivalDay": 1,
                    "arrivalPlatform": "3",
                    "arrivalTrack": "2",
                    "arrivalCommercialCategory": "IC",
                },
            ],
            "connections": [],
        }
    ],
    "dictionaries": {
        "stations": {
            "38851": {"id": 38851, "name": "Warszawa Centralna"},
            "247482": {"id": 247482, "name": "Gdańsk Główny"},
        },
        "stopTypes": {"1": "przystanek", "2": "postój techniczny"},
    },
}


class TestTymczasowyRozklad:
    """``timetable()`` — od rozkładu do płaskich rekordów."""

    def test_zwraca_rekord_pociagn_stacja(
        self, client_no_cache: Any, transport: FakeTransport
    ) -> None:
        transport.add(json_body=STATION_SEARCH_RESPONSE)  # słownik stacji (oba nazwiska)
        transport.add(json_body=SCHEDULE_RESPONSE)

        rows = highlevel.timetable(
            client_no_cache, from_station="Warszawa Centralna", to_station="Gdańsk Główny"
        )

        assert len(rows) == 2
        assert rows[0]["train_number"] == "141"
        assert rows[0]["station_name"] == "Warszawa Centralna"
        assert rows[1]["station_name"] == "Gdańsk Główny"

    def test_sklada_date_przy_przekroczeniu_polnocy(
        self, client_no_cache: Any, transport: FakeTransport
    ) -> None:
        transport.add(json_body=SCHEDULE_RESPONSE)

        rows = highlevel.timetable(client_no_cache, from_station=1, to_station=2)

        # Odjazd 23:50 dnia bazowego
        assert rows[0]["departure"].isoformat() == "2026-10-19T23:50:00"
        assert rows[0]["departure_day_offset"] == 0
        # Przyjazd 05:50 następnego dnia — offset 1 składa poprawnie
        assert rows[1]["arrival"].isoformat() == "2026-10-20T05:50:00"
        assert rows[1]["arrival_day_offset"] == 1

    def test_zachowuje_peron_i_tor(
        self, client_no_cache: Any, transport: FakeTransport
    ) -> None:
        transport.add(json_body=SCHEDULE_RESPONSE)

        rows = highlevel.timetable(client_no_cache, from_station=1, to_station=2)

        assert rows[0]["departure_platform"] == "9"
        assert rows[0]["departure_track"] == "4"

    def test_przenosi_id_do_dalszego_przetwarzania(
        self, client_no_cache: Any, transport: FakeTransport
    ) -> None:
        transport.add(json_body=SCHEDULE_RESPONSE)

        rows = highlevel.timetable(client_no_cache, from_station=1, to_station=2)

        assert rows[0]["schedule_id"] == 25
        assert rows[0]["order_id"] == 12345

    def test_numer_stacji_przechodzi_bez_wyszukiwania(
        self, client_no_cache: Any, transport: FakeTransport
    ) -> None:
        """Podanie ID zamiast nazwy nie powinno odpytywać słownika stacji."""
        transport.add(json_body=SCHEDULE_RESPONSE)

        rows = highlevel.timetable(client_no_cache, from_station=38851, to_station=247482)

        assert len(rows) == 2
        assert not any("dictionaries/stations" in url for url in transport.called_urls)

    def test_tylko_jedna_stacja_jest_bledem(self, client_no_cache: Any) -> None:
        with pytest.raises(ValidationError, match="brakuje"):
            highlevel.timetable(client_no_cache, from_station="Warszawa Centralna")

    def test_brak_stacji_jest_bledem(self, client_no_cache: Any) -> None:
        with pytest.raises(ValidationError, match="Podaj stację"):
            highlevel.timetable(client_no_cache)

    def test_pusta_odpowiedz_daje_pusta_liste(
        self, client_no_cache: Any, transport: FakeTransport
    ) -> None:
        transport.add(json_body={"routes": []})
        assert highlevel.timetable(client_no_cache, from_station=1, to_station=2) == []

    def test_bez_slownikow_nazwy_pochodza_z_cache(
        self, client_no_cache: Any, transport: FakeTransport
    ) -> None:
        """``include_dictionaries=False`` nie może kosztować utraty nazw stacji."""
        transport.add(json_body=SCHEDULE_RESPONSE)
        # Drugie zapytanie: pełny słownik, żeby uzupełnić nazwy.
        transport.add(json_body=STATION_SEARCH_RESPONSE)

        rows = highlevel.timetable(
            client_no_cache, from_station=1, to_station=2, include_dictionaries=False
        )

        assert rows[0]["station_name"] == "Warszawa Centralna"
        assert rows[1]["station_name"] == "Gdańsk Główny"


class TestPolaczenia:
    """``connections()`` — jedno połączenie na rekord, filtr godzin, miasta."""

    def test_jedno_polaczenie_na_pociag(
        self, client_no_cache: Any, transport: FakeTransport
    ) -> None:
        transport.add(json_body=CITIES_RESPONSE)
        transport.add(json_body=CITY_STATIONS_RESPONSE)
        transport.add(json_body=CONNECTIONS_RESPONSE)

        rows = highlevel.connections(
            client_no_cache, from_station="Warszawa Centralna", to_station="Zakopane",
            date="2026-11-09",
        )

        assert len(rows) == 2, "każdy pociąg to jeden rekord, a nie wpis na przystanek"
        fast = next(r for r in rows if r["train_number"] == "1350")
        assert fast["from_station"] == "Warszawa Centralna"
        assert fast["to_station"] == "Zakopane"
        assert fast["departure"].strftime("%H:%M") == "07:35"
        assert fast["arrival"].strftime("%H:%M") == "12:17"
        assert fast["duration_minutes"] == 282

    def test_nocny_ma_date_nastepnego_dnia(
        self, client_no_cache: Any, transport: FakeTransport
    ) -> None:
        transport.add(json_body=CITIES_RESPONSE)
        transport.add(json_body=CITY_STATIONS_RESPONSE)
        transport.add(json_body=CONNECTIONS_RESPONSE)

        rows = highlevel.connections(
            client_no_cache, from_station="Warszawa Centralna", to_station="Zakopane",
            after="23:00",
        )

        assert len(rows) == 1
        assert rows[0]["departure"].day == 9
        assert rows[0]["arrival"].day == 10
        assert rows[0]["duration_minutes"] == 411

    def test_filtr_godzin_dziala_na_odjezdzie(
        self, client_no_cache: Any, transport: FakeTransport
    ) -> None:
        transport.add(json_body=CITIES_RESPONSE)
        transport.add(json_body=CITY_STATIONS_RESPONSE)
        transport.add(json_body=CONNECTIONS_RESPONSE)
        # Drugie zapytanie korzysta już z memo słowników — tylko rozkład.
        transport.add(json_body=CONNECTIONS_RESPONSE)

        morning = highlevel.connections(
            client_no_cache, from_station="Warszawa Centralna", to_station="Zakopane",
            after="06:00", before="09:00",
        )
        evening = highlevel.connections(
            client_no_cache, from_station="Warszawa Centralna", to_station="Zakopane",
            after="22:00",
        )

        assert [r["train_number"] for r in morning] == ["1350"]
        assert [r["train_number"] for r in evening] == ["1170"]

    def test_miasto_obejmuje_wszystkie_stacje(
        self, client_no_cache: Any, transport: FakeTransport
    ) -> None:
        transport.add(json_body=CITIES_RESPONSE)
        transport.add(json_body=CONNECTIONS_RESPONSE)
        transport.add(json_body=CITY_STATIONS_RESPONSE)

        highlevel.connections(
            client_no_cache, from_station="WARSZAWA", to_station="ZAKOPANE",
            date="2026-11-09",
        )

        schedule_url = transport.called_urls[1]
        assert "fromStations=38851%2C38852" in schedule_url
        assert "toStations=247482" in schedule_url

    def test_godziny_sprzeczne_odrzucone_przed_zapytaniem(self, client_no_cache: Any) -> None:
        with pytest.raises(ValidationError, match="wcześniejsza"):
            highlevel.connections(
                client_no_cache, from_station=1, to_station=2, after="12:00", before="08:00"
            )

    def test_wsiadanie_na_wielu_stacjach_miasta(
        self, client_no_cache: Any, transport: FakeTransport
    ) -> None:
        """Miasto ma kilka stacji — rekord musi pokazać wszystkie warianty wsiadania."""
        payload = {
            "routes": [
                {
                    "scheduleId": 2026,
                    "orderId": 3,
                    "carrierCode": "IC",
                    "commercialCategorySymbol": "EIC",
                    "operatingDates": ["2026-11-09"],
                    "stations": [
                        {
                            "stationId": 38852,
                            "departureTrainNumber": "1350",
                            "departureTime": "07:29:00",
                        },
                        {
                            "stationId": 38851,
                            "departureTrainNumber": "1350",
                            "departureTime": "07:35:00",
                        },
                        {
                            "stationId": 247482,
                            "arrivalTrainNumber": "1350",
                            "arrivalTime": "12:17:00",
                        },
                    ],
                }
            ]
        }
        transport.add(json_body=CITIES_RESPONSE)
        transport.add(json_body=payload)
        transport.add(json_body=CITY_STATIONS_RESPONSE)

        rows = highlevel.connections(
            client_no_cache, from_station="WARSZAWA", to_station="ZAKOPANE"
        )

        options = rows[0]["departure_options"]
        assert [o["station_name"] for o in options] == [
            "Warszawa Wschodnia",
            "Warszawa Centralna",
        ]
        assert [o["departure"].strftime("%H:%M") for o in options] == ["07:29", "07:35"]
        # Rekord główny liczy się od pierwszej stacji w mieście.
        assert rows[0]["from_station"] == "Warszawa Wschodnia"
        assert rows[0]["departure"].strftime("%H:%M") == "07:29"

    def test_brak_danych_jest_bledem(self, client_no_cache: Any) -> None:
        with pytest.raises(ValidationError, match="Podaj stację"):
            highlevel.connections(client_no_cache, from_station="Warszawa Centralna")


class TestTrainRealtime:
    def test_zwraca_opoznienia_i_status(
        self, client: Any, transport: FakeTransport
    ) -> None:
        transport.add(
            json_body={
                "trains": [
                    {
                        "scheduleId": 25,
                        "orderId": 12345,
                        "operatingDate": "2026-10-19",
                        "trainStatus": "P",
                        "stations": [
                            {
                                "stationId": 38851,
                                "plannedArrival": "2026-10-19T10:00:00",
                                "actualArrival": "2026-10-19T10:07:00",
                                "arrivalDelayMinutes": 7,
                                "departureDelayMinutes": 5,
                                "isCancelled": False,
                            }
                        ],
                    }
                ],
                "stations": {"38851": {"id": 38851, "name": "Warszawa Centralna"}},
            }
        )

        result = highlevel.train_realtime(client, 25, 12345, "2026-10-19")

        assert result["train_status"] == "P"
        assert result["train_status_description"] == "w trakcie kursu"
        assert result["max_delay_minutes"] == 7
        assert result["stops"][0]["station_name"] == "Warszawa Centralna"

    def test_status_odwolania_ma_czytelny_opis(
        self, client: Any, transport: FakeTransport
    ) -> None:
        transport.add(json_body={"trains": [{"scheduleId": 1, "orderId": 1, "trainStatus": "X", "stations": []}]})
        result = highlevel.train_realtime(client, 1, 1, "2026-10-19")
        assert result["train_status_description"] == "odwołany"


class TestDelaysAtStation:
    def test_filtruje_ponizej_progu(self, client: Any, transport: FakeTransport) -> None:
        transport.add(
            json_body={
                "trains": [
                    {
                        "scheduleId": 25,
                        "orderId": 1,
                        "operatingDate": "2026-10-19",
                        "trainStatus": "P",
                        "stations": [
                            {"stationId": 38851, "arrivalDelayMinutes": 3},
                            {"stationId": 99999, "arrivalDelayMinutes": 90},
                        ],
                    },
                    {
                        "scheduleId": 25,
                        "orderId": 2,
                        "operatingDate": "2026-10-19",
                        "trainStatus": "P",
                        "stations": [{"stationId": 38851, "arrivalDelayMinutes": 12}],
                    },
                ],
                "pagination": {"page": 1, "totalPages": 1, "hasNextPage": False},
            }
        )

        rows = highlevel.delays_at_station(client, 38851, min_delay_minutes=5)

        assert len(rows) == 1
        assert rows[0]["delay_minutes"] == 12
        assert rows[0]["order_id"] == 2

    def test_odwolany_pociag_nie_traja_jako_opozniony(
        self, client: Any, transport: FakeTransport
    ) -> None:
        transport.add(
            json_body={
                "trains": [
                    {
                        "scheduleId": 25,
                        "orderId": 1,
                        "trainStatus": "X",
                        "stations": [
                            {"stationId": 38851, "arrivalDelayMinutes": 40, "isCancelled": True}
                        ],
                    }
                ],
                "pagination": {"hasNextPage": False},
            }
        )

        assert highlevel.delays_at_station(client, 38851) == []


class TestModulyApi:
    def test_search_waliduje_sprzeczne_filtry(self, client: Any) -> None:
        with pytest.raises(ValidationError, match="wykluczają się"):
            schedules.search(client, stations="38851", from_city="WARSZAWA", to_city="GDAŃSK")

    def test_search_domyślnie_dzisiaj(self, client: Any, transport: FakeTransport) -> None:
        transport.add(json_body={"routes": []})
        schedules.search(client, from_city="WARSZAWA", to_city="GDAŃSK")
        assert "dateFrom=" in transport.called_urls[0]

    def test_search_full_route_wlacza_sciezke(
        self, client: Any, transport: FakeTransport
    ) -> None:
        transport.add(json_body={"routes": []})
        schedules.search(client, from_city="A", to_city="B", full_route=True)
        assert "fullRoute=true" in transport.called_urls[0]

    def test_compact_wybiera_wariant_skrocony(
        self, client: Any, transport: FakeTransport
    ) -> None:
        transport.add(json_body={"routes": []})
        schedules.search(client, from_city="A", to_city="B", compact=True)
        assert "/schedules/shortened" in transport.called_urls[0]

    def test_route_ids_zrzesza_pary(self, client: Any, transport: FakeTransport) -> None:
        transport.add(
            json_body={"routes": [{"scheduleId": 25, "orderId": 1, "name": "x"}]}
        )
        assert schedules.route_ids_on_date(client, "2026-10-19") == [(25, 1)]

    def test_operations_iteruje_po_stronach(
        self, client_no_cache: Any, transport: FakeTransport
    ) -> None:
        transport.add(json_body={"trains": [{"id": 1}], "pagination": {"hasNextPage": True}})
        transport.add(json_body={"trains": [{"id": 2}], "pagination": {"hasNextPage": False}})

        trains = list(operations.iter_operations(client_no_cache, stations="38851"))
        assert [t["id"] for t in trains] == [1, 2]

    def test_page_size_jest_ograzany_do_5000(
        self, client_no_cache: Any, transport: FakeTransport
    ) -> None:
        transport.add(json_body={"trains": []})
        list(operations.iter_operations(client_no_cache, stations="1", page_size=99999))
        assert "pageSize=5000" in transport.called_urls[0]

    def test_disruptions_doklejaja_opisy_typow(
        self, client: Any, transport: FakeTransport
    ) -> None:
        transport.add(
            json_body={
                "disruptions": [
                    {"disruptionId": 1, "disruptionTypeCode": "utr_32", "message": "Remont toru"}
                ],
                "disruptionTypes": {"utr_32": "Prace remontowe"},
            }
        )

        rows = disruptions.disruptions_with_types(client)
        assert rows[0]["disruptionTypeDescription"] == "Prace remontowe"

    def test_statistics_domyślnie_bez_daty(
        self, client: Any, transport: FakeTransport
    ) -> None:
        transport.add(json_body={"totalTrains": 1})
        operations.statistics(client)
        assert "date=" not in transport.called_urls[0]


class TestApikeyEnvelope:
    """Odpowiedzi ``/apikey/*`` są opakowane w ``{success, data}``.

    Swagger deklaruje tylko ``200: OK`` bez schematu, więc kształt trzeba było
    ustalić na żywo (2026-10-20). Te testy pilnują, żeby ``_unwrap`` nadal
    zdejmował envelope, ale nie zjadał zwykłego payloadu.
    """

    def test_info_zwraca_wnetrze_envelope(self, client: Any, transport: FakeTransport) -> None:
        from plk_open_data.api import apikey

        transport.add(
            json_body={
                "success": True,
                "data": {
                    "id": 1775,
                    "name": "Wyszukiwarka",
                    "accessLevel": "Basic",
                    "hourlyRateLimit": 100,
                    "dailyRateLimit": 1000,
                    "isActive": True,
                },
            }
        )

        info = apikey.info(client)

        assert info["accessLevel"] == "Basic"
        assert info["isActive"] is True
        assert "data" not in info

    def test_limits_zwraca_wnetrze_envelope(
        self, client: Any, transport: FakeTransport
    ) -> None:
        from plk_open_data.api import apikey

        transport.add(
            json_body={
                "success": True,
                "data": {
                    "current": {"accessLevel": "Basic", "hourlyRateLimit": 100},
                    "availableTiers": [{"tier": "Premium"}],
                },
            }
        )

        result = apikey.limits(client)

        assert result["current"]["accessLevel"] == "Basic"
        assert result["availableTiers"][0]["tier"] == "Premium"

    def test_usage_zwraca_wnetrze_envelope(
        self, client: Any, transport: FakeTransport
    ) -> None:
        from plk_open_data.api import apikey

        transport.add(
            json_body={
                "success": True,
                "data": {"totalRequests": 3, "dailyUsage": [{"date": "2026-10-20"}]},
            }
        )

        assert apikey.usage(client)["totalRequests"] == 3

    def test_payload_bez_envelope_przechodzi_bez_zmian(
        self, client: Any, transport: FakeTransport
    ) -> None:
        """Gdyby API przestało opakowywać odpowiedzi, wynik ma zostać czytelny."""
        from plk_open_data.api import apikey

        transport.add(json_body={"accessLevel": "Basic", "isActive": True})

        assert apikey.info(client)["accessLevel"] == "Basic"


class TestApikeyWalidacja:
    def test_tier_musi_byc_znany(self, client: Any) -> None:
        from plk_open_data.api import apikey

        with pytest.raises(ValidationError, match="Nieznany pakiet"):
            apikey.request_limit_change(client, "Złoto", "x" * 30)

    def test_uzasadnienie_za_krotkie(self, client: Any) -> None:
        from plk_open_data.api import apikey

        with pytest.raises(ValidationError, match="co najmniej 20 znaków"):
            apikey.request_limit_change(client, "Standard", "za krótko")

    def test_uzasadnienie_za_dlugie(self, client: Any) -> None:
        from plk_open_data.api import apikey

        with pytest.raises(ValidationError, match="nie może przekraczać"):
            apikey.request_limit_change(client, "Standard", "x" * 1001)

    def test_uzasadnienie_akceptuje_dolne_granice(self, client: Any, transport: FakeTransport) -> None:
        from plk_open_data.api import apikey

        transport.add(status_code=201, json_body={"status": "pending"})
        result = apikey.request_limit_change(client, "standard", "x" * 20)
        assert result["status"] == "pending"

    def test_register_wymaga_emaila(self) -> None:
        from plk_open_data.api import apikey

        with pytest.raises(ValidationError, match="contact_email"):
            apikey.register("Mój klucz")

    def test_register_wymaga_nazwy(self) -> None:
        from plk_open_data.api import apikey

        with pytest.raises(ValidationError, match="'name' jest wymagane"):
            apikey.register("  ", contact_email="a@b.pl")

    def test_kod_weryfikacyjny_musi_miec_8_cyfr(self, client: Any) -> None:
        from plk_open_data.api import apikey

        with pytest.raises(ValidationError, match="8 cyfr"):
            apikey.verify_with_code(client, "123")