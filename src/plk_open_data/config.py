"""Konfiguracja klienta: klucz API, adres bazowy, timeout, cache.

Klucz API szukany jest w kolejności:
  1. jawny argument przekazany do :func:`load_api_key`
  2. zmienna środowiskowa ``PLK_API_KEY`` (lub ``PKP_API_KEY``)
  3. plik ``.env`` w katalogu roboczym lub w katalogu projektu

Plik ``.env`` jest czytany własnym, ~30 linii parserem, żeby nie ciągnąć
do zależności ``python-dotenv``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASE_URL = "https://pdp-api.plk-sa.pl"
"""Bazowy adres API. Uwaga: ``/scalar/v1`` to strona z dokumentacją, nie API."""

DEFAULT_TIMEOUT = 30.0

#: Sprawdzane po kolei zmienne środowiskowe z kluczem API.
API_KEY_ENV_VARS = ("PLK_API_KEY", "PKP_API_KEY")

#: Ile godzin domyślnie trzymać w cache słowniki (stacje, przewoźnicy, miasta).
#: Słowniki zmieniają się raz na jakiś czas, a pobranie ich zjada limit zapytań.
DEFAULT_DICTIONARY_TTL = 24 * 3600

#: Domyślny TTL dla odpowiedzi bieżących (operations, disruptions).
DEFAULT_LIVE_TTL = 60


def _read_env_file(path: Path) -> dict[str, str]:
    """Czyta plik ``.env`` w formacie ``KLUCZ=wartość``.

    Pomija komentarze (``#``), puste linie i eksport (``export KLUCZ=...``).
    Cudzysłowy wokół wartości są usuwane.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return values

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _candidate_env_files() -> Iterator[Path]:
    """Katalogi, w których szukamy pliku ``.env`` — od bardziej do mniej konkretnych."""
    # Katalog projektu: src/plk_open_data/config.py -> trzy poziomy w górę
    project_root = Path(__file__).resolve().parents[2]
    seen: set[Path] = set()
    for directory in (Path.cwd(), project_root):
        try:
            resolved = directory.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        yield resolved / ".env"


def load_api_key(explicit: str | None = None) -> str | None:
    """Zwraca klucz API albo ``None``, jeśli żadne źródło go nie zawiera.

    Nie rzuca wyjątkiem — brak klucza jest obsługiwany przez warstwę klienta,
    bo część endpointów (np. ``/api/version``) działa bez autoryzacji.
    """
    if explicit and explicit.strip():
        return explicit.strip()

    for var in API_KEY_ENV_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return value

    for env_file in _candidate_env_files():
        values = _read_env_file(env_file)
        for var in API_KEY_ENV_VARS:
            value = values.get(var, "").strip()
            if value:
                return value

    return None


@dataclass(slots=True)
class Settings:
    """Ustawienia klienta. Tworzone przez :func:`build_settings`."""

    base_url: str = DEFAULT_BASE_URL
    api_key: str | None = None
    timeout: float = DEFAULT_TIMEOUT
    cache_dir: Path = field(default_factory=lambda: Path(".plk-cache"))
    use_cache: bool = True

    def endpoint(self, path: str) -> str:
        """Łączy bazowy adres ze ścieżką endpointu (obsługuje wiodące `/`)."""
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"


def build_settings(
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
    cache_dir: str | Path | None = None,
    use_cache: bool = True,
) -> Settings:
    """Zbuduj ustawienia, uzupełniając wartości ze środowiska i plików ``.env``."""
    resolved_key = load_api_key(api_key)
    return Settings(
        base_url=(base_url or os.environ.get("PLK_BASE_URL") or DEFAULT_BASE_URL),
        api_key=resolved_key,
        timeout=timeout if timeout is not None else float(os.environ.get("PLK_TIMEOUT", DEFAULT_TIMEOUT)),
        cache_dir=Path(cache_dir or os.environ.get("PLK_CACHE_DIR") or ".plk-cache"),
        use_cache=use_cache,
    )