---
name: plk-open-data
description: Dane o kolejach w Polsce z API PKP PLK (Open Railway Data) — rozkład planowy, opóźnienia w czasie rzeczywistym, utrudnienia, słowniki stacji i przewoźników. Używaj przy pytaniach o trasy, rozkłady jazdy, opóźnienia pociągów, odwołania, dostępność stacji albo przy programowaniu integracji z API PLK.
---

# Open Railway Data API (PKP PLK)

Dostęp do danych o kolejach w Polsce: rozkład planowy, wykonanie w czasie
rzeczywistym, opóźnienia i utrudnienia. Oficjalne API PKP PLK,
adres bazowy `https://pdp-api.plk-sa.pl`.

## Kiedy się przyda

- „Jaki jest rozkład z Warszawy do Gdańska w sobotę?”
- „Które pociągi są opóźnione na stacji Wrocław Główny?”
- „Czy pociąg IC 141 jedzie jutro i o której dojedzie?”
- „Jakie są utrudnienia na linii na ten tydzień?”
- „Ile stacji ma Warszawa w słowniku API?”
- Budowanie integracji, skryptu albo raportu na danych kolejowych.

## Krok 1: klucz API

**Większość zapytań wymaga klucza.** Bez niego działają tylko wersja API
(`/api/version`) i wersja regulaminu. Klucz jest bezpłatny, ale trzeba go
jednorazowo aktywować i zatwierdzają go administratorzy PKP.

### 1.1. Sprawdź, czy już go masz

```bash
python -m plk_open_data doctor
```

Jeśli w wyniku widzisz `api_key_present: true` — przeskocz do Kroku 2.

### 1.2. Zarejestruj się

```bash
python -m plk_open_data register \
  --name "Nazwa twojego projektu" \
  --email twoj@email.pl
```

Pola: `--name` (wymagane, np. „Analiza opóźnień”), `--email` (wymagany — tam
trafi kod aktywacyjny). Opcjonalnie `--organization`, `--first-name`,
`--last-name`, `--usage` (opis do czego używasz API).

**Klucz wraca w odpowiedzi w polu `apiKey`. Zapisz go od razu w bezpiecznym
miejscu** — ze względów bezpieczeństwa nie da się go później odzyskać
z systemu, trzeba będzie wygenerować nowy.

### 1.3. Aktywuj klucz

Na podany adres przychodzi wiadomość z dwoma rzeczami: **linkiem aktywacyjnym**
oraz **8-cyfrowym kodem**. Użyj jednego z nich:

```bash
# wariant A: 8-cyfrowy kod z e-maila (wymaga klucza w środowisku, patrz Krok 2)
python -m plk_open_data verify 12345678

# wariant B: token z linku aktywacyjnego
python -m plk_open_data verify --token TOKEN_Z_LINKU
```

Wiadomość nie dotarła? Poproś o ponowne wysłanie (max 1/min, 5/godz.):

```bash
python -m plk_open_data resend
```

### 1.4. Zaakceptuj regulamin

```bash
python -m plk_open_data accept-terms
```

Bez tego API odmawia dostępu do danych.

### 1.5. Sprawdź, czy działa

```bash
python -m plk_open_data doctor
```

Powinno pokazać `name`, `accessLevel`, `hourly_limit`, `daily_limit`
i `active: true`. Pole `active: false` znaczy, że brakuje aktywacji e-maila
albo akceptacji regulaminu.

## Krok 2: ustaw klucz w środowisku

Klient czyta klucz z (w kolejności) jawnie przekazanego argumentu, zmiennej
`PLK_API_KEY`, pliku `.env`, a z fallbackiem zmiennej `PKP_API_KEY`.

**Zmienna środowiskowa (PowerShell, na czas sesji):**

```powershell
$env:PLK_API_KEY = "twoj-klucz"
```

**Plik `.env` (trwałe, lokalne — nigdy nie commituj):**

```
PLK_API_KEY=twoj-klucz
```

Plik `.env` jest już w `.gitignore`.

## Krok 3: pierwsze zapytanie

Najprościej — połączenia, jedno na wiersz:

```bash
python -m plk_open_data connections -f WARSZAWA -t ZAKOPANE -d 2026-11-09 --after 04:00 --before 11:00
```

```
Odjazd  Przyjazd  Czas  Pociąg          Trasa
------  --------  ----  --------------  -----------------------------
07:29   12:17     4h48  IC 01350 [EIC]  Warszawa Wschodnia → Zakopane

Wsiadanie także na innych stacjach w mieście:
  01350: Warszawa Centralna 07:40, Warszawa Zachodnia 07:45
```

Nazwa miasta obejmuje wszystkie jego stacje. Podaj konkretną stację
(`-f "Warszawa Centralna"`), jeśli chcesz tylko jedną.

To samo z Pythona:

```python
from plk_open_data import PlkClient, connections, timetable

with PlkClient() as client:
    for p in connections(client, from_station="WARSZAWA", to_station="ZAKOPANE",
                         date="2026-11-09", after="04:00", before="11:00"):
        print(p["departure"].strftime("%H:%M"), p["train_number"], p["duration_minutes"])

    # Pełny rozkład (pociąg × stacja) — gdy potrzebny każdy przystanek:
    for przystanek in timetable(client,
                                from_station="Warszawa Centralna",
                                to_station="Gdańsk Główny",
                                date="2026-10-19"):
        print(przystanek["train_number"], przystanek["departure"], przystanek["station_name"])
```

## Najważniejsze zasady

**Limity są niskie.** Pakiet Basic to 100 zapytań na godzinę i 1000 na dobę;
Standard 500/5000, Premium 2000/20000. Każde zapytanie się liczy. Klient
cache'uje słowniki na 24 godziny, a dane real-time na minutę, i ponawia
zapytanie po 429 — ale nie marnuj limitu na zapytania o to samo.

**Filtry wykluczają się nawzajem.** `stations` nie łączyć z `from`/`to` ani
`fromStations`/`toStations`. `from` wymaga `to`. Klient pilnuje tego przed
zapytaniem, więc błąd wychodzi czytelny, a nie jako HTTP 400.

**Daty w rozkładzie są rozbite.** API zwraca `arrivalDay` (offset doby: `0` =
dzień bazowy, `1` = następny) osobno od `arrivalTime` (`GG:MM:SS`). **Pole
offsetu jest pomijane, gdy wynosi 0** — pojawia się dopiero przy przejściu przez
północ. Dlatego brak pola znaczy „offset 0", a nie „brak danych". Nie składaj
tego samodzielnie — użyj `compose_datetime()`. Sprawdzone na żywo: pociąg 1519
z 20.10 ma przyjazd `00:04` + `arrivalDay: 1`, czyli realnie 21.10 o 00:04.

**Nazwy miast muszą mieć polskie znaki — na surowym API.** `to_city="GDANSK"`
w `schedules.search()` kończy się błędem HTTP 400 „Nie znaleziono stacji dla
podanych nazw miast". Poprawnie jest `to_city="GDAŃSK"`. Warstwa wyżej radzi
sobie sama: `connections()` i `resolve_city()` porównują nazwy lokalnie po
normalizacji, więc `"gdansk"`, `"GDANSK"` i `"Gdańsk"` działają tak samo.

**Słowniki to większość transferu.** Parametr `dictionaries=true` (domyślny)
dołącza do odpowiedzi 3272 stacje, przewoźników i kategorie. Zmierzone na
relacji Warszawa → Gdańsk: **246 KB z słownikami, 84 KB bez (66% mniej)**.
Gdy robisz wiele zapytań w jednej sesji, ustaw `include_dictionaries=False`.

**API bywa kapryśne ze słownikami.** Serwer cache'uje odpowiedzi i przy
powtórzonym zapytaniu potrafi oddać pusty `dictionaries` **mimo
`dictionaries=true`** (sprawdzone 2026-10-20: 10 z 10 powtórzeń bez słowników,
świeże zapytania ze słownikami). Biblioteka radzi sobie z tym sama: brakujące
nazwy stacji uzupełnia z lokalnego słownika pobieranego raz na dobę, więc
`station_name` jest wypełnione niezależnie od tego, co odda serwer.

**Rozkład jest roczny.** `scheduleId` to rok rocznego rozkładu (`25` =
2025/2026) w parze z `orderId`. Co roku identyfikatory się zmieniają —
nie hardcoduj ich w kodzie ani w skryptach.

**Nazwy miast pisane są wielkimi literami.** Słownik zwraca `WARSZAWA`, nie
`Warszawa`. Do filtrów po mieście lepiej użyć `resolve_city()`, która zwraca
listę ID wszystkich stacji aglomeracji.

**Wariant `/shortened`** daje 2–3× mniejszy transfer, ale nazwy pól **kolidują
między endpointami**: `ts` oznacza `trains` w `/operations`, a `routes` w
`/schedules`. Domyślnie używaj wariantu pełnego. Słownik skrótów:
`python -m plk_open_data fields operations`.

## Gotowe zapytania

```python
from plk_open_data import (
    PlkClient, connections, timetable, train_realtime, delays_at_station,
)

with PlkClient() as client:
    # Połączenia — jeden rekord na pociąg, z czasem przejazdu i filtrem godzin
    opcje = connections(client, from_station="WARSZAWA", to_station="ZAKOPANE",
                        date="2026-11-09", after="04:00", before="11:00")

    # Rozkład między stacjami — rekordy „pociąg × stacja" z gotowymi datami
    rozklad = timetable(client, from_station="Warszawa Centralna",
                        to_station="Gdańsk Główny", date="2026-10-19")

    # Wykonanie jednego pociągu — opóźnienia przy każdej stacji
    # (schedule_id i order_id bierz z rozkładu — pola schedule_id/order_id)
    pociag = train_realtime(client, schedule_id=25, order_id=12345, date="2026-10-19")

    # Opóźnione pociągi na stacji, od najbardziej opóźnionego
    opoznione = delays_at_station(client, "Wrocław Główny", min_delay_minutes=10)
```

Różnica w skrócie: `connections()` odpowiada na „co mam do wyboru” (jedna
pozycja na pociąg), a `timetable()` na „jak wygląda rozkład” (pozycja na każdy
przystanek). Do planowania podróży prawie zawsze chcesz to pierwsze.

Niskopoziomowo, gdy potrzebujesz pola, którego wyżej nie ma:

```python
from plk_open_data.api import schedules, operations, disruptions, dictionaries

payload = schedules.search(client, from_city="WARSZAWA", to_city="GDAŃSK",
                           date_from="2026-10-19", full_route=True)
for pociag in operations.iter_operations(client, stations="38851", with_planned=True):
    ...
```

## Słowniki i rozwiązywanie nazw

```python
from plk_open_data import resolve_station, resolve_city, resolve_carrier, search_stations

id_warszawy = resolve_station(client, "warszaw centralna")  # 38851
id_miasta   = resolve_city(client, "warszawa")              # [38851, 38853, ...]
skrot       = resolve_carrier(client, "PKP Intercity")      # "IC"
skrot       = resolve_carrier(client, "IC")                 # też "IC"
```

Nazwy możesz pisać bez polskich znaków i w dowolnej wielkości liter —
`"gdansk glowny"` znajdzie `Gdańsk Główny`. Działa to dlatego, że biblioteka
porównuje lokalnie na pełnym słowniku stacji (pobierany raz na dobę), a nie
przez parametr `search` API, który wymaga dokładnej pisowni.

Wiersz poleceń:

```bash
python -m plk_open_data find-station "Kraków"    # -> identyfikatory do filtrów
python -m plk_open_data find-city "Poznań"
python -m plk_open_data carriers
```

## Diagnostyka

```bash
python -m plk_open_data doctor      # klucz, limity, dostępność, zużycie, cache
python -m plk_open_data info        # stan klucza
python -m plk_open_data limits      # limity (działa nawet po przekroczeniu)
python -m plk_open_data usage       # statystyki zużycia
python -m plk_open_data version     # wersja API (bez klucza)
python -m plk_open_data cache       # list | clear | purge
```

Wynik błędu zawsze zawiera `errorCode` i ścieżkę, więc łatwo zgłosić problem
do PKP PLK.

## Wniosek o wyższy limit

```bash
python -m plk_open_data request-limit Standard \
  -j "Przetwarzam rozkłady dla 30 stacji dziennie w projekcie analitycznym, obecny limit 100/godz. jest niewystarczający."
```

Uzasadnienie 20–1000 znaków. Wniosek zatwierdza administrator PKP PLK.
Dla jednego klucza może istnieć tylko jeden oczekujący wniosek.

## Instalacja

```bash
uv venv
uv pip install -e .
```

Zależności: `httpx`, `pydantic`, `typer`. Python 3.11+.

## Czego ta biblioteka nie obejmuje

Celowo pominięte, bo wymaga uprawnień administratora albo jest nieodwracalne:
`PUT /api/v1/apikey/update` (admin-only) i `POST /api/v1/apikey/deactivate`.

## Więcej szczegółów

- `README.md` — instalacja, architektura, pełna lista funkcji
- `references/api-reference.md` — wszystkie endpointy, parametry, pułapki
- `references/openapi.json` — snapshot specyfikacji (odśwież: `scripts/fetch_openapi.py`)