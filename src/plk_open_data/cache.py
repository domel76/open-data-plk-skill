"""Dyskowy cache odpowiedzi API z czasem życia (TTL).

Po co to jest: limity API są niskie (pakiet Basic to 100 zapytań na godzinę,
1000 na dobę). Zapytanie o ten sam rozkład dwa razy nie powinno drugi raz
zjadać limitu. Odpowiedzi trzymamy na dysku jako JSON, w katalogu
``.plk-cache`` (domyślnie, konfigurowalny).

Cache nie jest częścią API — to wyłącznie optymalizacja. Każdy odczyt
przechodzi przez :meth:`DiskCache.get`, który zwraca ``None`` po wygaśnięciu
wpisu, więc klient zawsze może zapytać API ponownie.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Prefiks w nazwie pliku, żeby łatwo odróżnić wpisy cache od innych plików.
CACHE_FILE_PREFIX = "plk_"


def make_key(path: str, params: dict[str, Any] | None = None, variant: str = "") -> str:
    """Buduje stabilny klucz cache dla zapytania.

    Klucz zależy od ścieżki, parametrów i wariantu odpowiedzi (np. ``shortened``),
    ale **nie** od klucza API — treść odpowiedzi jest taka sama dla każdego klucza,
    a wstawianie sekretu w nazwę pliku skończyłoby się jego wyciekiem na dysk.
    """
    parts = [variant, path, json.dumps(params or {}, sort_keys=True, default=str)]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{CACHE_FILE_PREFIX}{digest[:32]}"


@dataclass(slots=True)
class CacheStats:
    hits: int = 0
    misses: int = 0
    writes: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses, "writes": self.writes}


class DiskCache:
    """Prosty cache JSON na dysku. Bez zależności zewnętrznych."""

    def __init__(self, directory: str | Path = ".plk-cache", enabled: bool = True) -> None:
        self.directory = Path(directory)
        self.enabled = enabled
        self.stats = CacheStats()
        if self.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str) -> Any | None:
        """Zwraca zapamiętaną wartość albo ``None`` (brak wpisu, wygaśnięty, uszkodzony)."""
        if not self.enabled:
            return None

        path = self._path_for(key)
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            self.stats.misses += 1
            return None

        try:
            entry = json.loads(raw)
            expires_at = entry["expires_at"]
        except (json.JSONDecodeError, KeyError, TypeError):
            # Uszkodzony wpis — usuwamy, żeby nie zatruwał kolejnych odczytów.
            path.unlink(missing_ok=True)
            self.stats.misses += 1
            return None

        if expires_at <= time.time():
            path.unlink(missing_ok=True)
            self.stats.misses += 1
            return None

        self.stats.hits += 1
        return entry.get("value")

    def set(self, key: str, value: Any, ttl: int) -> None:
        """Zapisuje wartość z ważnością ``ttl`` sekund."""
        if not self.enabled or ttl <= 0:
            return

        entry = {"key": key, "expires_at": time.time() + ttl, "stored_at": time.time(), "value": value}
        path = self._path_for(key)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            # Zapis przez plik tymczasowy: przerwanie w połowie nie zostawi
            # uszkodzonego JSON-a, na który trafilibyśmy przy kolejnym odczycie.
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
            self.stats.writes += 1
        except (OSError, TypeError, ValueError):
            # Cache to optymalizacja — jeśli zapis się nie uda, zapytanie
            # po prostu wróci do API.
            path.unlink(missing_ok=True)

    def delete(self, key: str) -> None:
        self._path_for(key).unlink(missing_ok=True)

    def clear(self) -> int:
        """Usuwa wszystkie wpisy. Zwraca liczbę usuniętych plików."""
        if not self.directory.is_dir():
            return 0
        removed = 0
        for path in self.directory.glob(f"{CACHE_FILE_PREFIX}*"):
            path.unlink(missing_ok=True)
            removed += 1
        return removed

    def purge_expired(self) -> int:
        """Usuwa wpisy po wygaśnięciu TTL, nie ruszając aktywnych."""
        if not self.directory.is_dir():
            return 0
        now = time.time()
        removed = 0
        for path in self.directory.glob(f"{CACHE_FILE_PREFIX}*.json"):
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
                if entry.get("expires_at", 0) <= now:
                    path.unlink(missing_ok=True)
                    removed += 1
            except (OSError, json.JSONDecodeError, AttributeError):
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def entries(self) -> list[dict[str, Any]]:
        """Metadane wszystkich wpisów (bez pełnych odpowiedzi) — do diagnostyki."""
        result: list[dict[str, Any]] = []
        if not self.directory.is_dir():
            return result
        for path in sorted(self.directory.glob(f"{CACHE_FILE_PREFIX}*.json")):
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
                result.append(
                    {
                        "key": entry.get("key"),
                        "expires_at": entry.get("expires_at"),
                        "stored_at": entry.get("stored_at"),
                        "size_bytes": path.stat().st_size,
                    }
                )
            except (OSError, json.JSONDecodeError):
                continue
        return result