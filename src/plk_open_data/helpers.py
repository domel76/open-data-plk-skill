"""Logika domenowa: składanie dat, rozwiązywanie nazw na identyfikatory, walidacja.

Trzy rzeczy, które w surowym API są niewygodne, a tu są rozwiązaane:

**Daty są rozbite.** API nie zwraca daty kursowania pociągu jako jednej wartości,
tylko jako trzy części: ``operatingDate`` (data bazowa), ``arrivalDay`` /
``departureDay`` (offset doby: ``0`` = doba bazowa, ``1`` = następna) oraz
``arrivalTime`` / ``departureTime`` (godzina jako ``HH:mm:ss``). Pociąg
nocny odjeżdżający o 23:50 z daty bazowej ma ``departureDay == 1`` i realnie
wyjeżdża następnego dnia o 05:50. :func:`compose_datetime` składa to w jeden
obiekt :class:`datetime.datetime`.

**Nazwy nie są identyfikatorami.** Filtry przyjmują albo numery stacji, albo
nazwy miast zapisane wielkimi literami (``WARSZAWA``, nie ``Warszawa``).
:func:`normalize_name` i :func:`resolve_station` / :func:`resolve_city` zajmują się
tym. Wyniki są cache'owane, bo wyszukiwanie stacji zjada zapytania.

**Filtry wykluczają się nawzajem.** ``stations`` nie wolno łączyć z ``from``/``to``
ani z ``fromStations``/``toStations``, a ``from`` bez ``to`` jest błędem. Zamiast
czekać na HTTP 400, :func:`validate_schedule_filters` sprawdza to przed zapytaniem.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import unicodedata
from typing import Any

from .client import PlkClient
from .errors import ValidationError

#: Polskie litery, których Unicode **nie rozkłada** na cząstki (w odróżnieniu
#: od np. ``ó``), więc sama normalizacja NFKD ich nie obsłuży.
_POLISH_LETTERS = str.maketrans(
    {
        "ł": "l",
        "Ł": "L",
        "đ": "d",
        "Đ": "D",
        "ø": "o",
        "Ø": "O",
        "æ": "ae",
        "Æ": "AE",
        "ß": "ss",
        "þ": "th",
        "Þ": "TH",
        "ð": "d",
        "Ð": "D",
    }
)

#: Znaki, które w nazwach stacji bywają zapisane różnie w różnych słownikach.
_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-", "‐": "-"})

#: Mapowanie parametrów ``/schedules`` na ich wzajemne wykluczenia i wymagania.
_FILTER_GROUPS: tuple[tuple[str, str], ...] = (
    ("stations", "from"),
    ("stations", "to"),
    ("stations", "fromStations"),
    ("stations", "toStations"),
)

_PAIRED_PARAMS: tuple[tuple[str, str], ...] = (
    ("from", "to"),
    ("to", "from"),
    ("fromStations", "toStations"),
    ("toStations", "fromStations"),
)


# ---------------------------------------------------------------------- #
# Normalizacja nazw
# ---------------------------------------------------------------------- #


def normalize_name(value: str) -> str:
    """Zamienia dowolną pisownię nazwy na postać porównywalną.

    ``"Kraków Główny"``, ``"KRAKÓW GŁÓWNY"`` i ``"krakow glowny"`` dają ten sam
    wynik: wielkie litery bez polskich znaków i typografii. Polskie znaki są
    rozwijane do wersji ASCII (``ł`` → ``l``), bo różne źródła danych
    zapisują je niespójnie.

    :param value: nazwa stacji, miasta lub przewoźnika.
    """
    text = unicodedata.normalize("NFKD", value.strip())
    text = text.translate(_POLISH_LETTERS)
    text = text.translate(_DASHES)
    without_accents = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re_sub_non_alnum(without_accents).upper()


def re_sub_non_alnum(text: str) -> str:
    """Zamienia wszystko poza literami i cyframi na pojedynczą spację."""
    result: list[str] = []
    for char in text:
        if char.isalnum():
            result.append(char)
        elif result and result[-1] != " ":
            result.append(" ")
    return "".join(result).strip()


def normalize_id_list(value: str | int | list[Any] | tuple[Any, ...] | None) -> list[str]:
    """Zamienia identyfikatory na listę stringów gotową do wysłania w parametrze."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else [value]
    cleaned = [str(item).strip() for item in items if str(item).strip()]
    return cleaned


def ids_param(value: str | int | list[Any] | tuple[Any, ...] | None) -> str | None:
    """Buduje wartość parametru ``stations`` z ID stacji albo zwraca ``None``."""
    ids = normalize_id_list(value)
    return ",".join(ids) if ids else None


# ---------------------------------------------------------------------- #
# Daty i czasy
# ---------------------------------------------------------------------- #


def parse_date(value: dt.date | dt.datetime | str | None) -> dt.date | None:
    """Zamienia ``str`` / ``datetime`` na ``date``. Obsługuje ISO 8601."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValidationError(f"Niepoprawna data {value!r}. Oczekiwany format: RRRR-MM-DD") from exc


def parse_time(value: str | dt.time | None) -> dt.time | None:
    """Parsuje ``HH:mm:ss`` lub ``HH:mm`` na :class:`datetime.time`.

    API zwraca czas jako tekst bez daty, czasem z ułamkiem sekund
    (``12:34:56.000``) — oba warianty są obsłużone.
    """
    if value is None or value == "":
        return None
    if isinstance(value, dt.time):
        return value

    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1]
    # Obetnij część po 'T', jeśli ktoś podał pełny date-time.
    if "T" in text:
        text = text.split("T", 1)[1]

    parts = text.split(":")
    if len(parts) < 2:
        raise ValidationError(f"Niepoprawna godzina {value!r}. Oczekiwany format: GG:MM:SS")

    try:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(round(float(parts[2]))) if len(parts) > 2 and parts[2] else 0
    except ValueError as exc:
        raise ValidationError(f"Niepoprawna godzina {value!r}. Oczekiwany format: GG:MM:SS") from exc

    # Zaokrąglenie sekund mogło dać 60 (np. 59.6) — przenosimy do minuty.
    if second >= 60:
        second = 0
        minute += 1
    if minute >= 60:
        minute = 0
        hour += 1
    return dt.time(hour % 24, minute % 60, second % 60)


def compose_datetime(
    operating_date: dt.date | dt.datetime | str | None,
    day_offset: int | None,
    time_value: str | dt.time | None,
) -> dt.datetime | None:
    """Składa ``data bazowa + offset doby + godzina`` w jeden znacznik czasu.

    To jest sedno poprawności dla pociągów nocnych i przekraczających północ.

    :param operating_date: data kursowania (``RRRR-MM-DD`` albo ``date``).
    :param day_offset: ``arrivalDay`` / ``departureDay`` — ``0`` = doba bazowa,
        ``1`` = następna, ``-1`` = poprzednia.
    :param time_value: ``arrivalTime`` / ``departureTime`` jako ``GG:MM:SS``.
    :returns: znacznik czasu albo ``None``, gdy brakuje daty bazowej lub godziny.

    >>> compose_datetime("2026-10-18", 1, "05:50:00")
    datetime.datetime(2026, 10, 19, 5, 50)
    """
    base = parse_date(operating_date)
    if base is None:
        return None

    moment = parse_time(time_value)
    if moment is None:
        return None

    return dt.datetime.combine(base, moment) + dt.timedelta(days=int(day_offset or 0))


def today() -> dt.date:
    """Dzisiejsza data (lokalna). Skrót używany w domyślnych zakresach dat."""
    return dt.date.today()


def date_range(
    date_from: dt.date | str | None,
    date_to: dt.date | str | None,
    *,
    default: dt.date | None = None,
) -> tuple[str, str]:
    """Buduje parę parametrów ``dateFrom``/``dateTo`` w formacie ``RRRR-MM-DD``."""
    fallback = default or today()
    start = parse_date(date_from) or fallback
    end = parse_date(date_to) or fallback
    if end < start:
        raise ValidationError(
            f"Zakres dat jest odwrócony: dateFrom={start} jest późniejsze niż dateTo={end}"
        )
    return start.isoformat(), end.isoformat()


# ---------------------------------------------------------------------- #
# Walidacja filtrów
# ---------------------------------------------------------------------- #


def validate_schedule_filters(**filters: Any) -> None:
    """Sprawdza, czy filtry ``/schedules`` nie wykluczają się nawzajem.

    API zwraca wtedy HTTP 400, ale komunikat jest enigmatyczny. Tutaj dostajesz
    od razu konkretną przyczynę.

    :raises ValidationError: gdy podano sprzeczne filtry albo parametr bez pary.
    """
    active = {name for name, value in filters.items() if value not in (None, "", [], ())}

    for left, right in _FILTER_GROUPS:
        if left in active and right in active:
            raise ValidationError(
                f"Parametry {left!r} i {right!r} wykluczają się nawzajem. "
                f"Użyj albo {left!r}, albo pary from/to oraz fromStations/toStations."
            )

    for param, required in _PAIRED_PARAMS:
        if param in active and required not in active:
            raise ValidationError(
                f"Parametr {param!r} wymaga podania {required!r}. "
                f"Dodaj {required!r} albo usuń {param!r}."
            )


# ---------------------------------------------------------------------- #
# Rozwiązywanie nazw na identyfikatory
# ---------------------------------------------------------------------- #


def search_stations(
    client: PlkClient,
    query: str,
    *,
    limit: int = 20,
    fresh: bool = False,
) -> list[dict[str, Any]]:
    """Szuka stacji po nazwie. Zwraca listę ``{"id", "name"}``.

    Porównanie odbywa się **lokalnie**, na pełnym słowniku stacji pobranym raz
    i trzymanym w cache. To celowe: parametr ``search`` API jest wrażliwy na
    polskie znaki — ``search=Gdansk`` zwraca zero wyników, a ``search=Gdańsk``
    działa. Pobranie całego słownika (3272 stacje, ~100 KB, jedno zapytanie)
    pozwala przyjąć dowolną pisownię i nie zjada limitu przy każdym wyszukiwaniu.

    Dopasowanie jest trzystopniowe: dokładne, prefiks, zawieranie. Dzięki temu
    ``"Gdansk"`` znajduje ``"Gdańsk Główny"``, a ``"glowny"`` też coś zwraca.

    :param limit: maksymalna liczba wyników.
    :param fresh: pomiń cache i pobierz słownik od nowa.
    """
    normalized_query = normalize_name(query)
    if not normalized_query:
        return []

    catalog = _station_catalog(client, fresh=fresh)

    exact: list[dict[str, Any]] = []
    prefix: list[dict[str, Any]] = []
    contains: list[dict[str, Any]] = []

    for station in catalog:
        name = str(station.get("name", ""))
        normalized_name = normalize_name(name)
        entry = {"id": station.get("id"), "name": name}
        if normalized_name == normalized_query:
            exact.append(entry)
        elif normalized_name.startswith(normalized_query):
            prefix.append(entry)
        elif normalized_query in normalized_name:
            contains.append(entry)

    results = exact + prefix + contains
    return results[:limit]


def _station_catalog(client: PlkClient, *, fresh: bool = False) -> list[dict[str, Any]]:
    """Pełny słownik stacji — baza do lokalnego wyszukiwania i uzupełniania nazw.

    Słownik ma ~3300 pozycji, więc trzymamy go także **w pamięci klienta**:
    bez tego każde ``resolve_station`` parsowałoby ~100 KB JSON-a od nowa,
    a przy wyłączonym cache dyskowym pobierałoby go z API za każdym razem.

    :param fresh: pomiń oba poziomy cache i pobierz słownik od nowa.
    """
    if not fresh:
        memo = getattr(client, "_station_catalog_memo", None)
        if memo is not None:
            return memo

    payload = client.get(
        "/api/v1/dictionaries/stations",
        params={"page": 1, "pageSize": 10000},
        cache_ttl=0 if fresh else 86400,
    )
    stations = _as_list(payload, "stations")

    # Gdyby słownik był większy niż jedna strona, dociągamy resztę.
    if isinstance(payload, dict):
        total_pages = payload.get("totalPages") or 1
        for page in range(2, int(total_pages) + 1):
            extra = client.get(
                "/api/v1/dictionaries/stations",
                params={"page": page, "pageSize": 10000},
                cache_ttl=86400,
            )
            stations.extend(_as_list(extra, "stations"))

    if stations:
        # Klient zwykle pozwala dopisać atrybut; jeśli nie, zostaje cache dyskowy.
        with contextlib.suppress(AttributeError):
            client._station_catalog_memo = stations  # type: ignore[attr-defined]

    return stations


def resolve_station(
    client: PlkClient,
    name: str,
    *,
    exact: bool = False,
    search_limit: int = 50,
) -> int:
    """Zamienia nazwę stacji na jej identyfikator.

    Porównanie ignoruje wielkość liter i polskie znaki, więc ``"krakow glowny"``
    znajdzie ``"Kraków Główny"``.

    :param exact: wymaga dokładnego dopasowania po normalizacji. Domyślnie
        dopuszczamy dopasowanie częściowe i wybieramy najlepsze.
    :raises ValidationError: gdy nie znaleziono stacji albo nazwa jest niejednoznaczna
        i nie wskazano ``exact``.
    """
    normalized = normalize_name(name)
    if not normalized:
        raise ValidationError("Nazwa stacji jest pusta")

    candidates = search_stations(client, name, limit=search_limit)
    if not candidates:
        raise ValidationError(
            f"Nie znaleziono stacji o nazwie {name!r}. "
            "Sprawdź pisownię komendą: python -m plk_open_data find-station \"" + name + "\""
        )

    exact_matches = [c for c in candidates if normalize_name(str(c["name"])) == normalized]
    if exact_matches:
        return int(exact_matches[0]["id"])

    if exact:
        raise ValidationError(
            f"Brak dokładnego dopasowania dla {name!r}. "
            f"Najbliższe: {[c['name'] for c in candidates[:5]]}"
        )

    # Kolejność wyników zgadza się z rankingiem API, więc bierzemy pierwszy.
    return int(candidates[0]["id"])


def _city_catalog(client: PlkClient) -> list[dict[str, Any]]:
    """Pełny słownik miast (z listami stacji) — cache 24 h + memo w pamięci."""
    memo = getattr(client, "_city_catalog_memo", None)
    if memo is not None:
        return memo

    payload = client.get(
        "/api/v1/dictionaries/cities",
        params={"page": 1, "pageSize": 10000},
        cache_ttl=86400,
    )
    cities = _as_list(payload, "cities")

    if isinstance(payload, dict):
        total_pages = payload.get("totalPages") or 1
        for page in range(2, int(total_pages) + 1):
            extra = client.get(
                "/api/v1/dictionaries/cities",
                params={"page": page, "pageSize": 10000},
                cache_ttl=86400,
            )
            cities.extend(_as_list(extra, "cities"))

    if cities:
        with contextlib.suppress(AttributeError):
            client._city_catalog_memo = cities  # type: ignore[attr-defined]

    return cities


def find_city_stations(client: PlkClient, name: str) -> list[int] | None:
    """Zwraca stacje miasta, jeśli nazwa pasuje **dokładnie**; inaczej ``None``.

    Dokładność jest tu istotna: ``"Warszawa"`` ma oznaczać całą aglomerację,
    ale ``"Warszawa Centralna"`` to już konkretna stacja i nie wolno jej
    rozszerzać do całego miasta.
    """
    normalized = normalize_name(name)
    if not normalized:
        return None

    for city in _city_catalog(client):
        if normalize_name(str(city.get("name", ""))) == normalized:
            return [int(station_id) for station_id in city.get("stationIds", [])]
    return None


def resolve_city(client: PlkClient, name: str) -> list[int]:
    """Zamienia nazwę miasta na listę identyfikatorów jego stacji.

    Miasto w tym API to agregacja wielu stacji (np. Warszawa ma kilkanaście),
    więc wynik jest listą, nie pojedynczym numerem. To wygodniejsze niż wpisywanie
    nazwy w ``from``/``to``, bo obsługuje całą aglomerację.

    :raises ValidationError: gdy miasto nie istnieje w słowniku.
    """
    normalized = normalize_name(name)
    if not normalized:
        raise ValidationError("Nazwa miasta jest pusta")

    payload = client.get(
        "/api/v1/dictionaries/cities",
        params={"search": name.strip()},
        cache_ttl=86400,
    )
    cities = _as_list(payload, "cities")

    for city in cities:
        if normalize_name(str(city.get("name", ""))) == normalized:
            return [int(i) for i in city.get("stationIds", [])]

    # Dopasowanie częściowe — miasto mogło być wpisane inaczej.
    partial = [c for c in cities if normalized in normalize_name(str(c.get("name", "")))]
    if len(partial) == 1:
        return [int(i) for i in partial[0].get("stationIds", [])]

    raise ValidationError(
        f"Nie znaleziono miasta {name!r}. "
        "Dostępne miasta sprawdzisz komendą: python -m plk_open_data find-city \"" + name + "\""
    )


def resolve_carrier(client: PlkClient, name: str) -> str:
    """Zamienia nazwę **albo skrót** przewoźnika na kod używany w filtrach.

    Akceptuje oba warianty, bo użytkownik zna zwykle jedno z dwóch: skrót
    (``"IC"``) albo pełną nazwę (``"PKP Intercity"``). Dopasowanie ignoruje
    wielkość liter i polskie znaki.

    :raises ValidationError: gdy nic nie pasuje; komunikat wymienia dostępne kody.
    """
    normalized = normalize_name(name)
    carriers = _as_list(
        client.get("/api/v1/dictionaries/carriers", cache_ttl=86400), "carriers"
    )

    # Najpierw dokładny kod — to najczęstszy sposób podania przewoźnika.
    for carrier in carriers:
        if normalize_name(str(carrier.get("code", ""))) == normalized:
            return str(carrier.get("code", "")).strip()

    # Potem pełna nazwa.
    for carrier in carriers:
        if normalize_name(str(carrier.get("name", ""))) == normalized:
            return str(carrier.get("code", "")).strip()

    # Na końcu dopasowanie częściowe w obie strony („Intercity” -> „PKP Intercity”,
    # „Koleje Mazowieckie” -> „Koleje Mazowieckie-KM”).
    for carrier in carriers:
        code = normalize_name(str(carrier.get("code", "")))
        carrier_name = normalize_name(str(carrier.get("name", "")))
        if normalized and (normalized in carrier_name or normalized in code):
            return str(carrier.get("code", "")).strip()

    available = ", ".join(sorted(str(c.get("code", "")) for c in carriers)[:15])
    raise ValidationError(
        f"Nie znaleziono przewoźnika {name!r}. Przykładowe dostępne skróty: {available}"
    )


def _as_list(payload: Any, *keys: str) -> list[dict[str, Any]]:
    """Wyciąga listę obiektów z odpowiedzi, niezależnie od kształtu JSON-a."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in keys or _guess_list_keys(payload):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    # Niektóre słowniki zwracają mapę {"12345": {"id":..,"name":..}}
    values = list(payload.values())
    if values and all(isinstance(v, dict) for v in values):
        return [v for v in values if v]
    return []


def _guess_list_keys(payload: dict[str, Any]) -> list[str]:
    """Znajduje pola, które wyglądają jak lista słowników (nazwa + identyfikator)."""
    guesses: list[str] = []
    for key, value in payload.items():
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict):
                guesses.append(key)
    return guesses