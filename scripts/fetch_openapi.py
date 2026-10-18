#!/usr/bin/env python
"""Odświeża snapshot specyfikacji OpenAPI.

Uruchomienie:

    python scripts/fetch_openapi.py

Skrypt pobiera bieżącą specyfikację z API i porównuje ją z lokalnym snapshotem
w ``references/openapi.json``. Jeśli API się zmieniło (nowa wersja, nowe pola,
nowe endpointy), wypisuje dokładnie co — dzięki temu przy aktualizacji biblioteki
widać, co w niej rozjechało się z rzeczywistością.

Porównanie jest tekstowe, ale ograniczone do najważniejszych elementów:
wersji, listy ścieżek i zbioru nazw pól w schematach.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

SPEC_URL = "https://pdp-api.plk-sa.pl/swagger/v1/swagger.json"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "references" / "openapi.json"
TIMEOUT = 30


def fetch(url: str = SPEC_URL) -> dict[str, Any]:
    """Pobiera specyfikację jako słownik."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def summarize(spec: dict[str, Any]) -> dict[str, Any]:
    """Wyciąga z specyfikacji elementy warte porównania."""
    paths = spec.get("paths", {})
    return {
        "version": spec.get("info", {}).get("version"),
        "path_count": len(paths),
        "paths": sorted(paths),
        "methods": {
            path: sorted(m.upper() for m in ops if m.lower() in {"get", "post", "put", "delete", "patch"})
            for path, ops in sorted(paths.items())
        },
        "schemas": sorted(spec.get("components", {}).get("schemas", {})),
    }


def diff(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Zwraca listę opisanych zmian między dwoma snapshotami."""
    changes: list[str] = []

    if old.get("version") != new.get("version"):
        changes.append(f"Wersja API: {old.get('version')} -> {new.get('version')}")

    old_paths = set(old.get("paths", []))
    new_paths = set(new.get("paths", []))

    for path in sorted(new_paths - old_paths):
        changes.append(f"+ nowy endpoint: {path}")
    for path in sorted(old_paths - new_paths):
        changes.append(f"- usunięty endpoint: {path}")

    old_methods = old.get("methods", {})
    new_methods = new.get("methods", {})
    for path in sorted(old_paths & new_paths):
        if old_methods.get(path) != new_methods.get(path):
            changes.append(
                f"~ metody {path}: {old_methods.get(path)} -> {new_methods.get(path)}"
            )

    old_schemas = set(old.get("schemas", []))
    new_schemas = set(new.get("schemas", []))
    for name in sorted(new_schemas - old_schemas):
        changes.append(f"+ nowy schemat: {name}")
    for name in sorted(old_schemas - new_schemas):
        changes.append(f"- usunięty schemat: {name}")

    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description="Odśwież snapshot specyfikacji API.")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Zapisz nową specyfikację do references/openapi.json.",
    )
    parser.add_argument(
        "--fail-on-change",
        action="store_true",
        help="Kończ kodem 1, jeśli API zmieniło się od snapshotu (dla CI).",
    )
    args = parser.parse_args()

    print(f"Pobieram {SPEC_URL} ...")
    try:
        current = fetch()
    except urllib.error.URLError as exc:
        print(f"Nie udało się pobrać specyfikacji: {exc}", file=sys.stderr)
        return 2

    print(f"API w wersji {current.get('info', {}).get('version')}")

    if not SNAPSHOT_PATH.exists():
        print(f"\nBrak snapshotu w {SNAPSHOT_PATH}.")
        if args.write:
            SNAPSHOT_PATH.write_text(
                json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print("Snapshot zapisany.")
            return 0
        print("Uruchom ponownie z --write, aby go utworzyć.")
        return 1

    stored = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    changes = diff(summarize(stored), summarize(current))

    if not changes:
        print("\nSpecyfikacja jest zgodna ze snapshotem.")
        return 0

    print(f"\nWykryto {len(changes)} zmian:")
    for change in changes:
        print(f"  {change}")

    if args.write:
        SNAPSHOT_PATH.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nSnapshot zapisany w {SNAPSHOT_PATH}")
        return 0

    print("\nZapisz nowy snapshot komendą: python scripts/fetch_openapi.py --write")
    return 1 if args.fail_on_change else 0


if __name__ == "__main__":
    sys.exit(main())