# Ściąga do Open Railway Data API

Skrócona dokumentacja do szybkiego odnajdywania endpointów. Pełna specyfikacja:
`references/openapi.json` (odśwież: `python scripts/fetch_openapi.py`).

API w wersji **1.1.4**, OpenAPI 3.0.4. Adres bazowy `https://pdp-api.plk-sa.pl`.
Dokumentacja Scalar: `https://pdp-api.plk-sa.pl/scalar/v1`.

## Uwierzytelnianie

Nagłówek `X-Api-Key: {klucz}` albo `Authorization: Bearer {klucz}`.

Bez klucza działają **tylko dwa** endpointy (sprawdzone 2026-10-18):

| Endpoint | Zwraca |
|---|---|
| `GET /api/version` | wersja i build API |
| `GET /api/v1/terms/version` | wersja regulaminu |

Wszystko inne zwraca `401` z `errorCode: "MissingKey"`.

## Limity

| Pakiet | na godzinę | na dobę |
|---|---|---|
| Basic | 100 | 1 000 |
| Standard | 500 | 5 000 |
| Premium | 2 000 | 20 000 |

`GET /api/v1/apikey/limit-request` działa **także po przekroczeniu limitu** —
to jedyny endpoint `/apikey/*`, z którego widać, że limit się wyczerpał.

## Grupy endpointów

### Schedules — rozkład planowy

| Endpoint | Uwagi |
|---|---|
| `GET /api/v1/schedules` | filtry, patrz niżej |
| `GET /api/v1/schedules/shortened` | 2–3× mniejszy transfer |
| `GET /api/v1/schedules/route/{scheduleId}/{orderId}` | jedna trasa + daty kursowania |
| `GET /api/v1/schedules/route/{scheduleId}/{orderId}/shortened` | |
| `GET /api/v1/schedules/routes/{date}` | lista ID tras kursujących w dniu |
| `GET /api/v1/schedules/routes/{date}/shortened` | |

Parametry `GET /api/v1/schedules`:

| Parametr | Typ | Uwagi |
|---|---|---|
| `dateFrom` | `RRRR-MM-dd` | domyślnie dziś |
| `dateTo` | `RRRR-MM-dd` | domyślnie dziś |
| `stations` | CSV | filtr „któkolwiek z tych". **Wyklucza** `from`/`to`/`fromStations`/`toStations` |
| `from` | tekst | nazwa miasta początkowego, **wymaga** `to` |
| `to` | tekst | nazwa miasta docelowego, **wymaga** `from` |
| `fromStations` | CSV | wymaga `toStations` |
| `toStations` | CSV | wymaga `fromStations` |
| `carriersInclude` | CSV | skróty przewoźników |
| `carriersExclude` | CSV | |
| `fullRoute` | bool | domyślnie `false` — **przycina do odcinka** |
| `dictionaries` | bool | domyślnie `true` — dołącza nazwy stacji |

### Operations — czas rzeczywisty

| Endpoint | Uwagi |
|---|---|
| `GET /api/v1/operations` | paginacja, `withPlanned` dla opóźnień |
| `GET /api/v1/operations/shortened` | |
| `GET /api/v1/operations/train/{scheduleId}/{orderId}/{operatingDate}` | jeden pociąg |
| `GET /api/v1/operations/statistics` | `?date=` — tani health-check |

| Parametr | Uwagi |
|---|---|
| `stations` | CSV, domyślnie **wszystkie** stacje |
| `page` / `pageSize` | `pageSize` maks. 5000 |
| `fullRoutes` | bool, domyślnie `false` |
| `withPlanned` | bool — **bez tego nie ma pól opóźnienia** |

### Disruptions — utrudnienia

| Endpoint | Uwagi |
|---|---|
| `GET /api/v1/disruptions` | `dateFrom`, `dateTo`, `stations`, `dictionaries` |
| `GET /api/v1/disruptions/shortened` | |

Typ utrudnienia to kod (`"utr_32"`); opis siedzi w słowniku `disruptionTypes`
dołączanym przy `dictionaries=true`.

### Dictionaries — słowniki

| Endpoint | Zwraca listę pod kluczem |
|---|---|
| `GET /api/v1/dictionaries/stations` | `stations` (`search`, `page`, `pageSize` maks. 10000) |
| `GET /api/v1/dictionaries/carriers` | `carriers` — skróty `IC`, `KM`, ... |
| `GET /api/v1/dictionaries/cities` | `cities` — nazwa UPPERCASE + `stationIds` |
| `GET /api/v1/dictionaries/commercial-categories` | `commercialCategories` |
| `GET /api/v1/dictionaries/stop-types` | `stopTypes` |

### Fields — słowniki nazw pól

`GET /api/v1/fields/{schedules\|operations\|disruptions}` oraz `/csv`.
Opisują skrócony wariant odpowiedzi.

### ApiKey — zarządzanie kluczem

| Endpoint | Klucz? | Opis |
|---|---|---|
| `POST /api/v1/apikey/generate` | **nie** | rejestracja, wysyła e-mail |
| `GET /api/v1/apikey/verify-email?token=` | **nie** | aktywacja linkiem |
| `POST /api/v1/apikey/verify-email` | tak | `{code}` — 8 cyfr |
| `POST /api/v1/apikey/resend-verification` | tak | 1/min, 5/godz. |
| `POST /api/v1/apikey/accept-terms` | tak | `{acceptTermsOfService: true}` |
| `GET /api/v1/apikey/info` | tak | stan klucza |
| `GET /api/v1/apikey/usage` | tak | `fromDate`, `toDate` |
| `GET /api/v1/apikey/limit-request` | tak | limity i pakiety |
| `POST /api/v1/apikey/limit-request` | tak | `{requestedTier, justification}` — 20–1000 znaków |
| `POST /api/v1/apikey/deactivate` | tak | wyłącza klucz |
| `PUT /api/v1/apikey/update` | admin | **pominięte w bibliotece** |

### Terms, Privacy, DataVersion

`GET /api/v1/terms`, `/api/v1/terms/html`, `/api/v1/terms/version`,
`/api/v1/privacy`, `/api/v1/privacy/html`, `/api/v1/data-version`.

## Statusy pociągów

| Kod | Znaczenie |
|---|---|
| `S` | nie rozpoczął |
| `P` | w trakcie kursu |
| `C` | zakończył kurs |
| `X` | odwołany |
| `Q` | częściowo odwołany |

## Format odpowiedzi `/apikey/*`

Swagger deklaruje dla tych endpointów samo `200: OK` bez schematu — kształt
trzeba było ustalić na żywo (2026-10-20). **Wszystkie zwracają envelope:**

```json
{
  "success": true,
  "data": {
    "id": 1775,
    "name": "Wyszukiwarka połączeń",
    "organization": "IT-impl",
    "contactEmail": "ktos@example.com",
    "accessLevel": "Basic",
    "hourlyRateLimit": 100,
    "dailyRateLimit": 1000,
    "totalRequests": 3,
    "isActive": true,
    "createdAt": "2026-10-18T08:11:39.63"
  }
}
```

Biblioteka zdejmuje envelope w `api.apikey._unwrap`, więc funkcje zwracają
zwartość `data`. **API nie zwraca** informacji o weryfikacji e-maila ani
akceptacji regulaminu — stan klucza poznaje się po `isActive`.

## Format błędu

```json
{
  "error": "Unauthorized",
  "errorCode": "MissingKey",
  "message": "Brak klucza API. ...",
  "messageEn": "Missing API key. ...",
  "timestamp": "2026-10-18T14:11:40.58Z",
  "path": "/api/v1/schedules",
  "traceId": "..."
}
```

Część endpointów zwraca zamiast tego `ProblemDetails` (RFC 9457).

| HTTP | `errorCode` | Wyjątek w bibliotece |
|---|---|---|
| 400 | `InvalidRequest` | `ValidationError` |
| 401 | `MissingKey`, `InvalidKey`, `KeyNotActive` | `AuthenticationError` |
| 403 | `TermsNotAccepted` | `PermissionDeniedError` |
| 404 | `NotFound` | `NotFoundError` |
| 409 | `RequestAlreadyPending` | `ConflictError` |
| 429 | `RateLimitExceeded` | `RateLimitError` |
| 5xx | — | `ServerError` |

## Pułapki

**Daty rozbite na trzy części.** W rozkładzie `operatingDates[0]` to data bazowa,
`arrivalDay`/`departureDay` to offset (`0` = baza, `1` = następny dzień,
`-1` = poprzedni), a `arrivalTime`/`departureTime` to `GG:MM:SS`.

```
operatingDate 2026-10-19 + departureDay 1 + departureTime 05:50:00
  = 2026-10-20 05:50   ← pociąg wyjeżdża dzień po dniu kursowania
```

**Pole offsetu jest pomijane przy zerze.** Sprawdzone na żywo: na 492
przystanki w jednej relacji `arrivalDay` wystąpił 64 razy, `departureDay` 62 —
wyłącznie tam, gdzie offset ≠ 0. Brak pola znaczy więc `0`, nie „brak danych".

**Nazwy miast wymagają polskich znaków.** `to=GDANSK` → HTTP 400
„Nie znaleziono stacji dla podanych nazw miast". Poprawnie: `to=GDAŃSK`.

**`dictionaries=true` to większość transferu.** Zmierzone na Warszawa → Gdańsk:
246 KB ze słownikami (3272 stacje), 84 KB bez — **66% mniej**. Przy wielu
zapytaniach ustaw `dictionaries=false`.

**Serwer gubi słowniki przy powtórzonym zapytaniu.** Ten sam URL z
`dictionaries=true` zwrócił 224 KB i pełny słownik przy pierwszym wywołaniu,
a przy dziesięciu kolejnych — 78 KB i `"dictionaries": {}`. Wygląda na
serwerowy cache, który przechowuje wariant bez słowników. Nie zakładaj więc,
że `dictionaries=true` cokolwiek gwarantuje. Biblioteka uzupełnia nazwy
stacji z lokalnego słownika (`_station_catalog`, cache 24 h + memo w pamięci),
więc `station_name` jest zawsze wypełnione.

**Rozkład roczny.** `scheduleId` = rok rocznego rozkładu (`25` = 2025/2026).
W parze z `orderId` tworzy klucz `(scheduleId, orderId)`. Co roku się zmienia.

**Skrócone nazwy kolidują.** Ten sam klucz znaczy różne rzeczy w zależności
od endpointu:

| | `/operations` | `/schedules` | `/disruptions` |
|---|---|---|---|
| `ts` | `trains` | `routes` | — |
| `st` | `stations` | — | `stations` |
| `id` | — | — | `stationId` |

Pełny słownik: `GET /api/v1/fields/operations` albo `python -m plk_open_data fields operations`.

**Nazwy w dwóch miejscach.** Rozkład trzyma nazwy stacji w `dictionaries.stations`,
a `/operations` na najwyższym poziomie w `stations`.

**Pola deprecated.** `plannedArrivalTime` i `plannedDepartureTime` w
`OperationStationDto` zostaną usunięte w wersji 2.0 — zastępują je
`plannedArrival` i `plannedDeparture` (pełne znaczniki czasu).

**Powiązania to osobna struktura.** `RouteDto.connections[]` opisuje przesiadki
i podziały pociągów, z kodami typów ze słownika `connectionTypes`
(`KONTY`, `PODZI`, `POLAC`, `WYMWA`) i własnymi offsetami doby.

## Wymagania wobec klienta

- Nazwy miast wielkimi literami (`WARSZAWA`, `GDAŃSK`) — `normalize_name()`.
- Identyfikatory stacji jako CSV bez spacji: `stations=38851,38844`.
- Daty jako `RRRR-MM-DD` bez czasu.
- Odpowiedzi mogą być duże — `/operations` dla wszystkich stacji zwraca tysiące
  pociągów. Zawężaj `stations`; `dictionaries=false` zmniejsza objętość.