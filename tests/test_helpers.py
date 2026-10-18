"""Testy logiki domenowej: daty, nazwy, walidacja filtrów.

Tu mieszczą się najważniejsze przypadki poprawności biblioteki — szczególnie
składanie dat z rozbitych pól, na którym opiera się cała reszta.
"""

from __future__ import annotations

import datetime as dt

import pytest

from plk_open_data.errors import ValidationError
from plk_open_data.helpers import (
    compose_datetime,
    date_range,
    ids_param,
    normalize_name,
    parse_date,
    parse_time,
    validate_schedule_filters,
)


class TestComposeDatetime:
    def test_zwykly_przystanek_w_ten_sam_dzien(self) -> None:
        assert compose_datetime("2026-10-18", 0, "12:34:00") == dt.datetime(2026, 10, 18, 12, 34)

    def test_pociag_nocny_offset_plus_jeden(self) -> None:
        """Pociąg odjeżdżający o 23:50 z doby bazowej wyjeżdża następnego dnia."""
        assert compose_datetime("2026-10-18", 1, "05:50:00") == dt.datetime(2026, 10, 19, 5, 50)

    def test_przyjazd_w_posesci_dob(self) -> None:
        result = compose_datetime("2026-10-18", -1, "22:15:00")
        assert result == dt.datetime(2026, 10, 17, 22, 15)

    def test_offset_przekraczajacy_miesiac(self) -> None:
        assert compose_datetime("2026-10-31", 1, "00:30:00") == dt.datetime(2026, 11, 1, 0, 30)

    def test_przekroczenie_roku(self) -> None:
        assert compose_datetime("2026-12-31", 1, "12:00:00") == dt.datetime(2027, 1, 1, 12, 0)

    def test_akceptuje_obiekt_date(self) -> None:
        assert compose_datetime(dt.date(2026, 10, 18), 0, "08:00:00") == dt.datetime(
            2026, 10, 18, 8, 0
        )

    def test_brak_godziny_zwraca_none(self) -> None:
        assert compose_datetime("2026-10-18", 0, None) is None

    def test_brak_daty_zwraca_none(self) -> None:
        assert compose_datetime(None, 0, "12:00:00") is None

    def test_brak_offsetu_traktowany_jako_zero(self) -> None:
        assert compose_datetime("2026-10-18", None, "12:00:00") == dt.datetime(2026, 10, 18, 12)

    def test_ulamek_sekund_jest_zaokraglany(self) -> None:
        assert compose_datetime("2026-10-18", 0, "12:34:56.000") == dt.datetime(
            2026, 10, 18, 12, 34, 56
        )


class TestParseTime:
    @pytest.mark.parametrize(
        ("wartosc", "oczekiwane"),
        [
            ("08:00:00", dt.time(8, 0)),
            ("08:00", dt.time(8, 0)),
            ("23:59:59", dt.time(23, 59, 59)),
        ],
    )
    def test_poprawne_formaty(self, wartosc: str, oczekiwane: dt.time) -> None:
        assert parse_time(wartosc) == oczekiwane

    def test_date_time_z_sufiksem_z(self) -> None:
        assert parse_time("2026-10-18T08:30:00Z") == dt.time(8, 30)

    def test_sekundy_zaokraglone_do_minuty(self) -> None:
        """59,6 sekundy zaokrągla się do 60, co musi przejść do następnej minuty."""
        assert parse_time("12:34:59.6") == dt.time(12, 35)

    def test_niepoprawna_godzina_rzuca_wyjatek(self) -> None:
        with pytest.raises(ValidationError, match="Niepoprawna godzina"):
            parse_time("abc")


class TestNormalizeName:
    @pytest.mark.parametrize(
        ("wejscie", "oczekiwane"),
        [
            ("Kraków Główny", "KRAKOW GLOWNY"),
            ("KRAKÓW GŁÓWNY", "KRAKOW GLOWNY"),
            ("krakow glowny", "KRAKOW GLOWNY"),
            ("  Warszawa Centralna  ", "WARSZAWA CENTRALNA"),
            # Myślnik i spacja to ta sama stacja — normalizacja je zjednuje.
            ("Gdańsk–Główny", "GDANSK GLOWNY"),
            ("Gdańsk Główny", "GDANSK GLOWNY"),
        ],
    )
    def test_normalizacja_nazw(self, wejscie: str, oczekiwane: str) -> None:
        assert normalize_name(wejscie) == oczekiwane

    def test_dwa_zapisy_tej_samej_stacji_daja_identyczny_wynik(self) -> None:
        assert normalize_name("Kraków Główny") == normalize_name("KRAKOW GLOWNY")
        assert normalize_name("Gdańsk–Główny") == normalize_name("Gdańsk Główny")


class TestValidateScheduleFilters:
    def test_poprawna_para_miast_przechodzi(self) -> None:
        validate_schedule_filters(**{"from": "WARSZAWA", "to": "GDAŃSK"})

    def test_poprawna_lista_stacji_przechodzi(self) -> None:
        validate_schedule_filters(stations="38851,38844")

    def test_stations_i_from_conflict(self) -> None:
        with pytest.raises(ValidationError, match="wykluczają się nawzajem"):
            validate_schedule_filters(stations="38851", **{"from": "WARSZAWA", "to": "GDAŃSK"})

    def test_stations_i_from_stations_conflict(self) -> None:
        with pytest.raises(ValidationError, match="wykluczają się nawzajem"):
            validate_schedule_filters(
                stations="38851", fromStations="1", toStations="2"
            )

    def test_from_bez_to_jest_bledem(self) -> None:
        with pytest.raises(ValidationError, match="wymaga podania"):
            validate_schedule_filters(**{"from": "WARSZAWA"})

    def test_to_bez_from_jest_bledem(self) -> None:
        with pytest.raises(ValidationError, match="wymaga podania"):
            validate_schedule_filters(**{"to": "GDAŃSK"})

    def test_from_stations_bez_to_stations_jest_bledem(self) -> None:
        with pytest.raises(ValidationError, match="wymaga podania"):
            validate_schedule_filters(fromStations="38851")

    def test_puste_wartosci_sa_pominiete(self) -> None:
        validate_schedule_filters(stations=None, **{"from": "", "to": None})


class TestDates:
    def test_date_range_domyślnie_dzisiaj(self) -> None:
        start, end = date_range(None, None)
        assert start == end == dt.date.today().isoformat()

    def test_odwrocony_zakres_jest_bledem(self) -> None:
        with pytest.raises(ValidationError, match="Zakres dat jest odwrócony"):
            date_range("2026-10-20", "2026-10-18")

    def test_parse_date_odrzuca_bezsensowny_tekst(self) -> None:
        with pytest.raises(ValidationError, match="Niepoprawna data"):
            parse_date("jutro")

    def test_parse_date_obcina_dlugosc_do_dziesieciu_znakow(self) -> None:
        assert parse_date("2026-10-18T00:00:00Z") == dt.date(2026, 10, 18)


class TestIdsParam:
    def test_skalarna_liczba(self) -> None:
        assert ids_param(38851) == "38851"

    def test_tekstowy_identyfikator(self) -> None:
        assert ids_param("38851") == "38851"

    def test_lista_identyfikatorow(self) -> None:
        assert ids_param([38851, 38844]) == "38851,38844"

    def test_none_daje_none(self) -> None:
        assert ids_param(None) is None