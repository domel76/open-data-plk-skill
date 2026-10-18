#!/usr/bin/env python
"""Diagnostyka w jednym poleceniu — dla kogoś, kto nie zna Pythona.

Uruchomienie:

    python scripts/doctor.py

Sprawdza po kolei: czy API odpowiada, czy jest klucz, jaki ma limit, ile
wykorzystano i co siedzi w cache. Nie wymaga klucza — bez niego po prostu
powiadomi, że trzeba go zdobyć, i podpowiedzi dokładną komendę.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Pozwala uruchomić skrypt bez instalowania pakietu (dodaje src/ do ścieżki).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plk_open_data import __version__  # noqa: E402
from plk_open_data.api import apikey, dictionaries  # noqa: E402
from plk_open_data.client import PlkClient  # noqa: E402
from plk_open_data.errors import PlkError  # noqa: E402

OK = "[ OK ]"
BLAD = "[BLAD]"
UWAGA = "[UWAGA]"


def main() -> int:
    print("=" * 62)
    print(f"Diagnostyka plk-open-data {__version__}")
    print("=" * 62)

    problems = 0

    with PlkClient() as client:
        # 1. Czy API w ogóle odpowiada (endpoint dostępny bez klucza).
        try:
            version = dictionaries.api_version()
            print(f"\n{OK} API odpowiada")
            print(f"       wersja:  {version.get('version')}")
            print(f"       build:   {version.get('buildDate')}")
        except PlkError as exc:
            print(f"\n{BLAD} API nie odpowiada: {exc}")
            print("       Sprawdź internet i adres https://pdp-api.plk-sa.pl")
            return 1

        # 2. Klucz API.
        if client.has_api_key:
            masked = client.settings.api_key[:6] + "..." + client.settings.api_key[-4:]
            print(f"\n{OK} Klucz API jest ustawiony ({masked})")

            try:
                info = apikey.info(client)
                print(f"       nazwa:    {info.get('name')}")
                print(f"       organizacja: {info.get('organization') or '-'}")
                print(
                    f"       pakiet:   {info.get('accessLevel')} "
                    f"({info.get('hourlyRateLimit')}/godz, {info.get('dailyRateLimit')}/doba)"
                )
                print(f"       aktywny:  {info.get('isActive')}")
                print(f"       zapytania razem: {info.get('totalRequests')}")
                if not info.get("isActive"):
                    problems += 1
                    print(f"\n{BLAD} Klucz nie jest aktywny.")
                    print("       Najczęściej brakuje akceptacji regulaminu:")
                    print("       python -m plk_open_data accept-terms")
                    print("       Albo weryfikacji e-maila: python -m plk_open_data verify KOD")
            except PlkError as exc:
                problems += 1
                print(f"\n{BLAD} Klucz jest, ale API go nie przyjmuje: {exc}")
                print("       Klucz mógł nie zostać aktywowany albo wygasł.")
                print("       Sprawdź aktywację: python -m plk_open_data verify KOD")

            try:
                version_data = dictionaries.data_version(client)
                print(f"\n{OK} Wersja danych u źródła")
                print(f"       {version_data.get('dataVersion')}")
                print(f"       od: {version_data.get('timestamp')}")
            except PlkError as exc:
                problems += 1
                print(f"\n{BLAD} Nie udało się pobrać wersji danych: {exc}")
        else:
            print(f"\n{UWAGA} Brak klucza API — dane o rozkładach są niedostępne")
            print("       1. Zarejestruj się:")
            print("          python -m plk_open_data register --name 'Nazwa' --email twoj@email.pl")
            print("       2. Wejdź na e-mail i aktywuj klucz kodem:")
            print("          python -m plk_open_data verify 12345678")
            print("       3. Zaakceptuj regulamin:")
            print("          python -m plk_open_data accept-terms")
            print("       4. Klucz zapisz w zmiennej PLK_API_KEY albo w pliku .env")

        # 3. Zużycie i cache.
        usage = client.usage.as_dict()
        cache = client.cache.stats.as_dict()
        print(f"\n{OK} Sesja")
        print(f"       zapytania: {usage['requests_made']} "
              f"(z cache: {usage['cache_hits']}, ponowienia: {usage['retries']})")
        print(f"       cache:    trafienia={cache['hits']} zapis={cache['writes']}")

    print()
    if problems == 0:
        print("Diagnostyka zakończona bez błędów.")
        return 0
    print(f"Diagnostyka zakończona: {problems} problem(ów) do rozwiązania.")
    return 1


if __name__ == "__main__":
    sys.exit(main())