# plk-open-data

Klient Pythona do **Open Railway Data API** (PKP PLK) — rozkład planowy, wykonanie
pociągów w czasie rzeczywistym i utrudnienia w komunikacji.

API jest wygodne, ale surowe: daty rozbite na trzy pola, filtry wykluczające się
nawzajem, limity 100 zapytań na godzinę i nazwy miast pisane wielkimi literami.
Ta biblioteka zamyka te problemy w jednym miejscu.

```python
from plk_open_data import PlkClient, timetable

with PlkClient() as client:
    for pociag in timetable(client, from_station="Warszawa Centralna",
                           to_station="Gdańsk Główny", date="2026-10-19"):
        print(f"{pociag['train_number']:>6}  {pociag['departure']}  ->  {pociag['station_name']}")
```

## Instalacja

```bash
uv venv
uv pip install -e .
```

Wymagany Python 3.11+. Zależności: `httpx`, `pydantic`, `typer`.

## Klucz API

Wszystkie endpointy poza `/api/version` i `/api/v1/terms/version` wymagają klucza.
Uzyskanie go opisuje [SKILL.md](SKILL.md) — krótko: `register`, potem aktywacja
kodem z e-maila, potem `accept-terms`.

Klucz ustawiamy w zmiennej środowiskowej `PLK_API_KEY` (alternatywnie w `.env`):

```bash
PLK_API_KEY=twoj-klucz
```

## Limity zapytań

| Pakiet | na godzinę | na dobę |
|---|---|---|
| Basic | 100 | 1 000 |
| Standard | 500 | 5 000 |
| Premium | 2 000 | 20 000 |

Przy limicie 100/godz. każde zapytanie się liczy — dlatego klient:

- **cache'uje** słowniki na 24 h, a dane real-time na minutę (`PLK_CACHE_DIR`, domyślnie `.plk-cache`),
- **ponawia** zapytania przy 429 i 5xx z wykładniczym backoffem, szanując `Retry-After`,
- **liczy** zapytania w sesji — sprawdź to komendą `doctor`,
- **iteruje po stronach** za Ciebie (`iter_operations`, `paginate`).

Wniosek o wyższy pakiet: `python -m plk_open_data request-limit Standard -j "opis zastosowania..."`.

## Warstwa wysokopoziomowa

Funkcje zwracają `list[dict]` z gotowymi znacznikami `datetime` i nazwami stacji.

| Funkcja | Zwraca |
|---|---|
| `connections(from_station, to_station, date, after, before)` | połączenia — jeden rekord na pociąg |
| `timetable(from_station, to_station, date)` | rekordy „pociąg × stacja" z rozkładu |
| `train_realtime(schedule_id, order_id, date)` | wykonanie pociągu z opóźnieniami |
| `delays_at_station(station, min_delay_minutes)` | opóźnione pociągi na stacji |

`from_station`/`to_station` przyjmują nazwę stacji **albo miasta** — miasto
obejmuje wszystkie swoje stacje, a `departure_options`/`arrival_options`
w wyniku podają godziny dla każdej z nich.

Pomocnicze: `resolve_station`, `resolve_city`, `resolve_carrier`, `compose_datetime`.

## Warstwa niskopoziomowa

Moduły w `plk_open_data.api` zwracają surowe JSON-y w strukturze API, gdy potrzebujesz
pola, którego warstwa wysokopoziomowa nie wystawia:

```python
from plk_open_data import PlkClient
from plk_open_data.api import schedules

with PlkClient() as client:
    payload = schedules.search(client, from_city="WARSZAWA", to_city="GDAŃSK",
                               date_from="2026-10-19", full_route=True)
```

Dostępne moduły: `dictionaries`, `schedules`, `operations`, `disruptions`, `apikey`.

## Wiersz poleceń

```bash
python -m plk_open_data doctor                    # diagnostyka: klucz, limity, cache
python -m plk_open_data connections -f WARSZAWA -t ZAKOPANE -d 2026-11-09 --after 04:00
python -m plk_open_data timetable -f "Warszawa Centralna" -t "Gdańsk Główny" --csv
python -m plk_open_data delays --station "Wrocław Główny" --min-delay 10
python -m plk_open_data find-station "Kraków"    # identyfikatory do filtrów
python -m plk_open_data version                  # działa bez klucza
```

`connections` domyślnie wypisuje czytelną tabelę; `--json`, `--csv` albo
`--output plik.json` przełączają format.

Komenda `--help` na każdej z nich podaje szczegóły.

## Pułapki, które biblioteka zamyka

- **Daty rozbite.** `arrivalDay` to offset doby (`0` = dzień bazowy, `1` = następny),
  godzina leży osobno w `arrivalTime`. `compose_datetime` składa to w jeden `datetime`.
  Bez tego pociąg odjeżdżający o 23:50 wygląda na nocny, choć wyjeżdża następnego dnia.
- **Filtry wykluczają się.** `stations` nie łączyć z `from`/`to` ani
  `fromStations`/`toStations`; `from` wymaga `to`. Walidacja leci przed zapytaniem,
  nie po HTTP 400.
- **Miasto to nie stacja.** `/dictionaries/cities` zwraca `WARSZAWA` → listę ID.
  `resolve_city` obsługuje aglomerację, `normalize_name` zapewnia, że `"krakow glowny"`
  znajdzie `"Kraków Główny"`.
- **Rozkład jest roczny.** `scheduleId` = rok (`25` = 2025/2026) w parze z `orderId`.
  Co roku identyfikatory się zmieniają — nie hardcoduj.
- **Wariant `/shortened`** ma 2–3× mniejszy transfer, ale klucze **kolidują**:
  `ts` oznacza `trains` w `/operations`, a `routes` w `/schedules`. Domyślnie używamy
  pełnych nazw; wariant skrócony jest jawną opcją `compact=True`.
- **Pola deprecated.** `plannedArrivalTime` i `plannedDepartureTime` znikną w API 2.0.

## Testy

```bash
uv run pytest                      # testy na mockach, bez klucza
PLK_API_KEY=... uv run pytest -m integration   # dodatkowo prawdziwe zapytania
uv run ruff check .
```

## Licencja

MIT