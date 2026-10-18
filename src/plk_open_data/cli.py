"""Interfejs wiersza poleceń.

Komendy grupują się w trzy bloki:

* **Klucz API** — ``register``, ``verify``, ``resend``, ``accept-terms``, ``info``,
  ``usage``, ``limits``, ``request-limit``
* **Dane** — ``timetable``, ``delays``, ``train``, ``disruptions``,
  ``find-station``, ``find-city``, ``carriers``, ``cities``
* **Diagnostyka** — ``doctor``, ``version``, ``data-version``, ``cache``, ``fields``

Każda komenda wypisuje JSON na standardowe wyjście (albo CSV tam, gdzie to
sensowne), więc wynik można przekierować do pliku albo do ``jq``.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import sys
from pathlib import Path
from typing import Any

import typer

from . import __version__
from .api import apikey, dictionaries, disruptions, operations
from .client import PlkClient
from .errors import PlkError

app = typer.Typer(
    name="plk-open-data",
    help="Klient Open Railway Data API (PKP PLK): rozkład, opóźnienia, utrudnienia.",
    no_args_is_help=True,
    add_completion=False,
)


# ---------------------------------------------------------------------- #
# Serializacja wyjścia
# ---------------------------------------------------------------------- #


def _default(obj: Any) -> Any:
    """Zamienia obiekty bez natywnej serializacji JSON na czytelne teksty."""
    if isinstance(obj, dt.datetime):
        return obj.isoformat()
    if isinstance(obj, dt.date):
        return obj.isoformat()
    if isinstance(obj, dt.time):
        return obj.isoformat()
    if isinstance(obj, dt.timedelta):
        return obj.total_seconds()
    return str(obj)


def emit(payload: Any, *, as_csv: bool = False) -> None:
    """Wypisuje wynik jako JSON albo CSV na standardowe wyjście."""
    rows = payload if isinstance(payload, list) else [payload]
    rows = [r for r in rows if isinstance(r, dict)]

    if as_csv and rows:
        columns: list[str] = []
        for row in rows:
            for key in row:
                if key not in columns:
                    columns.append(key)

        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        typer.echo(buffer.getvalue().rstrip("\r\n"))
        return

    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=_default))


def _write_or_emit(payload: Any, output: Path | None, *, as_csv: bool = False) -> None:
    """Zapisuje do pliku albo wypisuje na ekran."""
    if output is None:
        emit(payload, as_csv=as_csv)
        return

    rows = payload if isinstance(payload, list) else [payload]
    rows = [r for r in rows if isinstance(r, dict)]

    if as_csv and rows:
        columns = list({k for row in rows for k in row})
        with output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    else:
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=_default),
            encoding="utf-8",
        )
    typer.echo(f"Zapisano do {output}", err=True)


def _client(**kwargs: Any) -> PlkClient:
    return PlkClient(**kwargs)


def _run(func: Any, *args: Any, **kwargs: Any) -> Any:
    """Wykonuje wywołanie API, zamieniając błędy na czytelny komunikat i kod wyjścia 1."""
    try:
        return func(*args, **kwargs)
    except PlkError as exc:
        typer.secho(f"Błąd: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


# ---------------------------------------------------------------------- #
# Klucz API
# ---------------------------------------------------------------------- #


@app.command("register")
def cmd_register(
    name: str = typer.Option(..., "--name", "-n", help="Nazwa klucza, np. 'Analiza opóźnień'."),
    email: str = typer.Option(..., "--email", "-e", help="E-mail do weryfikacji (wymagany)."),
    organization: str = typer.Option(None, "--organization", "-o"),
    first_name: str = typer.Option(None, "--first-name"),
    last_name: str = typer.Option(None, "--last-name"),
    expected_usage: str = typer.Option(
        None, "--usage", help="Deklarowany sposób wykorzystania API."
    ),
) -> None:
    """Wysyła wniosek o nowy klucz API (nie wymaga istniejącego klucza)."""
    result = _run(
        apikey.register,
        name,
        contact_email=email,
        organization=organization,
        first_name=first_name,
        last_name=last_name,
        expected_usage=expected_usage,
    )
    emit(result)
    if isinstance(result, dict):
        typer.secho(
            "\nSprawdź skrzynkę e-mail — wysłano tam link i 8-cyfrowy kod aktywacyjny.",
            fg=typer.colors.GREEN,
        )


@app.command("verify")
def cmd_verify(
    code: str = typer.Argument(None, help="8-cyfrowy kod z e-maila."),
    token: str = typer.Option(None, "--token", help="Token z linku aktywacyjnego."),
) -> None:
    """Aktywuje klucz kodem z e-maila albo tokenem z linku."""
    if token:
        emit(_run(apikey.verify_with_token, token))
        return
    if not code:
        typer.secho(
            "Podaj kod z e-maila albo --token z linku aktywacyjnego.", fg=typer.colors.RED, err=True
        )
        raise typer.Exit(2)

    with _client() as client:
        emit(_run(apikey.verify_with_code, client, code))
        typer.secho("\nKlucz zweryfikowany. Teraz zaakceptuj regulamin: accept-terms", fg=typer.colors.GREEN)


@app.command("resend")
def cmd_resend() -> None:
    """Prosi o ponowne wysłanie e-maila weryfikacyjnego (max 1/min, 5/godz.)."""
    with _client() as client:
        emit(_run(apikey.resend_verification, client))


@app.command("accept-terms")
def cmd_accept_terms() -> None:
    """Akceptuje regulamin dla bieżącego klucza."""
    with _client() as client:
        emit(_run(apikey.accept_terms, client))


@app.command("info")
def cmd_info() -> None:
    """Pokazuje informacje o bieżącym kluczu (status, pakiet limitów)."""
    with _client() as client:
        emit(_run(apikey.info, client))


@app.command("usage")
def cmd_usage(
    from_date: str = typer.Option(None, "--from", help="RRRR-MM-DD (domyślnie 30 dni wstecz)."),
    to_date: str = typer.Option(None, "--to", help="RRRR-MM-DD"),
) -> None:
    """Pokazuje statystyki wykorzystania klucza."""
    with _client() as client:
        emit(_run(apikey.usage, client, from_date=from_date, to_date=to_date))


@app.command("limits")
def cmd_limits() -> None:
    """Pokazuje bieżące limity i dostępne pakiety (działa nawet po przekroczeniu limitu)."""
    with _client() as client:
        emit(_run(apikey.limits, client))


@app.command("request-limit")
def cmd_request_limit(
    tier: str = typer.Argument(..., help="Basic, Standard albo Premium."),
    justification: str = typer.Option(
        ..., "--justification", "-j", help="Uzasadnienie, 20–1000 znaków."
    ),
) -> None:
    """Składa wniosek o zmianę pakietu limitów."""
    with _client() as client:
        emit(_run(apikey.request_limit_change, client, tier, justification))


# ---------------------------------------------------------------------- #
# Dane
# ---------------------------------------------------------------------- #


@app.command("timetable")
def cmd_timetable(
    from_station: str = typer.Option(..., "--from", "-f", help="Stacja początkowa lub numer ID."),
    to_station: str = typer.Option(..., "--to", "-t", help="Stacja docelowa lub numer ID."),
    date: str = typer.Option(None, "--date", "-d", help="RRRR-MM-DD (domyślnie dziś)."),
    carriers: str = typer.Option(None, "--carriers", "-c", help="Skróty przewoźników, np. IC,KM."),
    full_route: bool = typer.Option(False, "--full-route", help="Cała trasa zamiast odcinka."),
    output: Path = typer.Option(None, "--output", "-O", help="Zapisz do pliku (.json lub .csv)."),
    as_csv: bool = typer.Option(False, "--csv", help="Wypisz CSV zamiast JSON."),
) -> None:
    """Rozkład planowy między dwiema stacjami, jako rekordy pociąg × stacja."""
    from .highlevel import timetable

    with _client() as client:
        rows = _run(
            timetable,
            client,
            from_station=from_station,
            to_station=to_station,
            date=date,
            carriers=[c.strip() for c in carriers.split(",")] if carriers else None,
            full_route=full_route,
        )

    if output is None:
        emit(rows, as_csv=as_csv)
    else:
        _write_or_emit(rows, output, as_csv=as_csv or output.suffix.lower() == ".csv")


@app.command("connections")
def cmd_connections(
    from_station: str = typer.Option(
        ..., "--from", "-f", help="Stacja lub miasto początkowe (np. WARSZAWA)."
    ),
    to_station: str = typer.Option(
        ..., "--to", "-t", help="Stacja lub miasto docelowe (np. ZAKOPANE)."
    ),
    date: str = typer.Option(None, "--date", "-d", help="RRRR-MM-DD (domyślnie dziś)."),
    after: str = typer.Option(None, "--after", "-a", help="Najwcześniejszy odjazd GG:MM."),
    before: str = typer.Option(None, "--before", "-b", help="Najpóźniejszy odjazd GG:MM."),
    carriers: str = typer.Option(None, "--carriers", "-c", help="Skróty przewoźników, np. IC,KM."),
    exclude_carriers: str = typer.Option(
        None, "--exclude-carriers", help="Skróty przewoźników do pominięcia."
    ),
    output: Path = typer.Option(None, "--output", "-O", help="Zapisz do pliku (.json lub .csv)."),
    as_csv: bool = typer.Option(False, "--csv", help="Wypisz CSV zamiast tabeli."),
    as_json: bool = typer.Option(False, "--json", help="Wypisz JSON zamiast tabeli."),
) -> None:
    """Połączenia między miejscami — jeden wiersz na pociąg, z czasem przejazdu."""
    from .highlevel import connections

    with _client() as client:
        rows = _run(
            connections,
            client,
            from_station=from_station,
            to_station=to_station,
            date=date,
            after=after,
            before=before,
            carriers=[c.strip() for c in carriers.split(",")] if carriers else None,
            exclude_carriers=(
                [c.strip() for c in exclude_carriers.split(",")] if exclude_carriers else None
            ),
        )

    if output is not None:
        _write_or_emit(rows, output, as_csv=as_csv or output.suffix.lower() == ".csv")
    elif as_csv:
        emit(rows, as_csv=True)
    elif as_json:
        emit(rows)
    else:
        _emit_connections_table(rows)


def _emit_connections_table(rows: list[dict[str, Any]]) -> None:
    """Wypisuje połączenia jako wyrównaną tabelę — czytelną bez JSON-a."""
    if not rows:
        typer.echo("Brak połączeń w podanych kryteriach.")
        return

    header = ("Odjazd", "Przyjazd", "Czas", "Pociąg", "Trasa")
    table: list[tuple[str, str, str, str, str]] = []
    for row in rows:
        departure: dt.datetime = row["departure"]
        arrival: dt.datetime = row["arrival"]
        arrival_text = (
            f"{arrival:%d.%m %H:%M}" if arrival.date() != departure.date() else f"{arrival:%H:%M}"
        )
        minutes = int(row.get("duration_minutes") or 0)
        table.append(
            (
                f"{departure:%H:%M}",
                arrival_text,
                f"{minutes // 60}h{minutes % 60:02d}",
                f"{row.get('carrier_code') or ''} {row.get('train_number') or ''} "
                f"[{row.get('commercial_category') or ''}]".strip(),
                f"{row.get('from_station')} → {row.get('to_station')}",
            )
        )

    widths = [max(len(header[i]), *(len(line[i]) for line in table)) for i in range(len(header))]
    typer.echo("  ".join(header[i].ljust(widths[i]) for i in range(len(header))))
    typer.echo("  ".join("-" * widths[i] for i in range(len(header))))
    for line in table:
        typer.echo("  ".join(line[i].ljust(widths[i]) for i in range(len(header))))

    # W obrębie miasta pociąg staje kilka razy — pokazujemy pozostałe warianty.
    extra_board = []
    extra_exit = []
    for row in rows:
        options = row.get("departure_options") or []
        if len(options) > 1:
            others = ", ".join(
                f"{opt['station_name']} {opt['departure']:%H:%M}" for opt in options[1:]
            )
            extra_board.append(f"  {row.get('train_number')}: {others}")

        options = row.get("arrival_options") or []
        if len(options) > 1:
            others = ", ".join(
                f"{opt['station_name']} {opt['arrival']:%H:%M}" for opt in options[:-1]
            )
            extra_exit.append(f"  {row.get('train_number')}: {others}")

    if extra_board:
        typer.echo("\nWsiadanie także na innych stacjach w mieście:")
        for line in extra_board:
            typer.echo(line)
    if extra_exit:
        typer.echo("\nWysiadanie także na innych stacjach w mieście:")
        for line in extra_exit:
            typer.echo(line)


@app.command("delays")
def cmd_delays(
    station: str = typer.Option(..., "--station", "-s", help="Stacja lub numer ID."),
    min_delay: int = typer.Option(5, "--min-delay", "-m", help="Minimalne opóźnienie w minutach."),
    date: str = typer.Option(None, "--date", "-d"),
    output: Path = typer.Option(None, "--output", "-O"),
    as_csv: bool = typer.Option(False, "--csv"),
) -> None:
    """Pociągi opóźnione na stacji, od najbardziej opóźnionego."""
    from .highlevel import delays_at_station

    with _client() as client:
        rows = _run(delays_at_station, client, station, min_delay_minutes=min_delay, date=date)

    _write_or_emit(rows, output, as_csv=as_csv or bool(output and output.suffix.lower() == ".csv"))


@app.command("train")
def cmd_train(
    schedule_id: int = typer.Option(..., "--schedule-id", help="Rok rocznego rozkładu, np. 25."),
    order_id: int = typer.Option(..., "--order-id", help="Identyfikator zamówienia trasy."),
    date: str = typer.Option(None, "--date", "-d"),
) -> None:
    """Wykonanie jednego pociągu w czasie rzeczywistym, z opóźnieniami."""
    from .highlevel import train_realtime

    with _client() as client:
        emit(_run(train_realtime, client, schedule_id, order_id, date))


@app.command("disruptions")
def cmd_disruptions(
    station: str = typer.Option(None, "--station", "-s", help="Stacja lub numer ID."),
    date: str = typer.Option(None, "--date", "-d"),
    output: Path = typer.Option(None, "--output", "-O"),
    as_csv: bool = typer.Option(False, "--csv"),
) -> None:
    """Utrudnienia na liniach kolejowych z opisami typów."""
    with _client() as client:
        if station:
            rows = _run(
                disruptions.disruptions_with_types, client, date=date, stations=station
            )
        else:
            payload = _run(disruptions.list_disruptions, client, date_from=date, date_to=date)
            rows = payload.get("disruptions", []) if isinstance(payload, dict) else []
    _write_or_emit(rows, output, as_csv=as_csv or bool(output and output.suffix.lower() == ".csv"))


@app.command("find-station")
def cmd_find_station(
    query: str = typer.Argument(..., help="Fragment nazwy stacji."),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """Szuka stacji po nazwie — zwraca identyfikatory gotowe do użycia w filtrach."""
    with _client() as client:
        from .helpers import search_stations

        emit(_run(search_stations, client, query, limit=limit), as_csv=True)


@app.command("find-city")
def cmd_find_city(
    query: str = typer.Argument(..., help="Nazwa miasta."),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """Szuka miasta — zwraca listę identyfikatorów jego stacji."""
    with _client() as client:
        from .api import dictionaries as dict_api

        cities = _run(dict_api.list_cities, client, search=query)
        matches = [
            {"name": c.get("name"), "station_count": c.get("stationCount"), "station_ids": c.get("stationIds")}
            for c in cities[:limit]
        ]
        emit(matches, as_csv=True)


@app.command("carriers")
def cmd_carriers() -> None:
    """Lista przewoźników ze skrótami używanymi w filtrach --carriers."""
    with _client() as client:
        emit(_run(dictionaries.list_carriers, client), as_csv=True)


@app.command("cities")
def cmd_cities(
    search: str = typer.Option(None, "--search", "-s"),
) -> None:
    """Lista miast wraz z identyfikatorami stacji."""
    with _client() as client:
        cities = _run(dictionaries.list_cities, client, search=search)
        emit(
            [
                {"name": c.get("name"), "station_count": c.get("stationCount"), "station_ids": c.get("stationIds")}
                for c in cities
            ],
            as_csv=True,
        )


@app.command("statistics")
def cmd_statistics(
    date: str = typer.Option(None, "--date", "-d"),
) -> None:
    """Statystyki statusów pociągów (totalne, opóźnione, odwołane)."""
    with _client() as client:
        emit(_run(operations.statistics, client, date))


# ---------------------------------------------------------------------- #
# Diagnostyka
# ---------------------------------------------------------------------- #


@app.command("version")
def cmd_version() -> None:
    """Wersja API (działa bez klucza) i wersja biblioteki."""
    version = _run(dictionaries.api_version)
    emit({"client_version": __version__, "api": version})


@app.command("data-version")
def cmd_data_version() -> None:
    """Wersja danych u źródła — zmiana GUID-u oznacza odświeżenie cache po stronie API."""
    with _client() as client:
        emit(_run(dictionaries.data_version, client))


@app.command("fields")
def cmd_fields(
    endpoint: str = typer.Argument(..., help="schedules, operations albo disruptions."),
    csv_output: bool = typer.Option(False, "--csv", help="Wypisz słownik jako CSV."),
) -> None:
    """Słownik nazw pól — potrzebny do odczytania wariantu /shortened."""
    with _client() as client:
        if csv_output:
            typer.echo(_run(dictionaries.field_dictionary_csv, client, endpoint))
        else:
            emit(_run(dictionaries.field_dictionary, client, endpoint))


@app.command("cache")
def cmd_cache(
    action: str = typer.Argument("list", help="list, clear albo purge."),
    cache_dir: str = typer.Option(None, "--cache-dir"),
) -> None:
    """Zarządza lokalnym cache odpowiedzi (list, clear, purge)."""
    from .cache import DiskCache

    cache = DiskCache(cache_dir or ".plk-cache", enabled=True)

    if action == "clear":
        removed = cache.clear()
        emit({"action": "clear", "removed": removed})
    elif action == "purge":
        removed = cache.purge_expired()
        emit({"action": "purge", "removed": removed})
    elif action == "list":
        entries = cache.entries()
        emit({"action": "list", "count": len(entries), "entries": entries})
    else:
        typer.secho(f"Nieznana akcja: {action}. Użyj list, clear lub purge.", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)


@app.command("doctor")
def cmd_doctor() -> None:
    """Diagnostyka: klucz, limity, dostępność API, zużycie i cache — wszystko na raz.

    Pierwsza komenda do uruchomienia, gdy coś nie działa.
    """
    report: dict[str, Any] = {"client_version": __version__}
    key_present = False

    with _client() as client:
        report["api_key_present"] = client.has_api_key
        key_present = client.has_api_key

        try:
            version = _run(dictionaries.api_version)
            report["api"] = {
                "reachable": True,
                "version": version.get("version"),
                "build": version.get("buildDate"),
            }
        except PlkError as exc:
            report["api"] = {"reachable": False, "error": str(exc)}

        if key_present:
            try:
                info = _run(apikey.info, client)
                report["key"] = {
                    "name": info.get("name"),
                    "organization": info.get("organization"),
                    "tier": info.get("accessLevel"),
                    "hourly_limit": info.get("hourlyRateLimit"),
                    "daily_limit": info.get("dailyRateLimit"),
                    "active": info.get("isActive"),
                    "total_requests": info.get("totalRequests"),
                    "created_at": info.get("createdAt"),
                }
            except PlkError as exc:
                report["key"] = {"error": str(exc)}

            try:
                report["limits"] = _run(apikey.limits, client)
            except PlkError as exc:
                report["limits"] = {"error": str(exc)}

            try:
                report["data_version"] = _run(dictionaries.data_version, client)
            except PlkError as exc:
                report["data_version"] = {"error": str(exc)}

        report["usage"] = client.usage.as_dict()
        report["cache"] = client.cache.stats.as_dict()

    emit(report)

    healthy = bool(report["api"].get("reachable")) and (
        not key_present or "error" not in report.get("key", {})
    )

    if healthy and key_present:
        typer.secho("\nDiagnostyka zakończona — wszystko działa.", fg=typer.colors.GREEN)
        return

    if healthy and not key_present:
        # API odpowiada, ale bez klucza nie da się pobrać żadnych danych.
        typer.secho(
            "\nAPI odpowiada, ale nie ma klucza. Uzyskaj go komendą:\n"
            "  python -m plk_open_data register --name 'Twoja nazwa' --email twoj@email.pl\n"
            "  (potem aktywacja kodem z e-maila: python -m plk_open_data verify KOD)\n"
            "Klucz ustaw w zmiennej PLK_API_KEY albo w pliku .env.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(1)

    typer.secho(
        "\nWykryto problemy — patrz sekcje zawierające 'error'.",
        fg=typer.colors.RED,
    )
    raise typer.Exit(1)


def main() -> None:
    """Punkt wejścia skryptu ``plk-open-data``."""
    app()


if __name__ == "__main__":
    sys.exit(main())