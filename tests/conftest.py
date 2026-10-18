"""Wspólne narzędzia testowe: klient podłączony do atrapy transportu HTTP.

Testy nie wymagają klucza API ani połączenia z internetem — transport podmieniamy
na własny, który zwraca przygotowane odpowiedzi. Dzięki temu da się przetestować
także przypadki trudne do odtworzenia na żywo: retry po 429, wygasanie cache,
paginację i mapowanie kodów błędów na wyjątki.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from plk_open_data.client import PlkClient


class FakeTransport(httpx.BaseTransport):
    """Odpowiada na żądania z kolejki przygotowanych odpowiedzi."""

    def __init__(self) -> None:
        self.responses: list[httpx.Response] = []
        self.requests: list[httpx.Request] = []

    def add(
        self,
        *,
        json_body: Any = None,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        text: str | None = None,
    ) -> httpx.Response:
        """Dodaje odpowiedź, którą zwróci następne żądanie."""
        if text is not None:
            response = httpx.Response(status_code, text=text, headers=headers)
        else:
            response = httpx.Response(
                status_code,
                content=json.dumps(json_body if json_body is not None else {}).encode("utf-8"),
                headers={"content-type": "application/json", **(headers or {})},
            )
        self.responses.append(response)
        return response

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(
                f"Nieprzygotowana odpowiedź na {request.method} {request.url}"
            )
        response = self.responses.pop(0)
        response.request = request
        return response

    @property
    def called_urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def client(transport: FakeTransport, tmp_path: Path) -> PlkClient:
    """Klient z kluczem API, cache w katalogu tymczasowym i atrapym transportem."""
    instance = PlkClient(
        api_key="test-key",
        cache_dir=tmp_path / "cache",
        max_retries=0,  # domyślnie bez ponowień — testy sprawdzają je jawnie
    )
    instance._client = httpx.Client(
        transport=transport,
        base_url=instance.settings.base_url,
        headers=instance._client.headers,
        timeout=instance.settings.timeout,
    )
    yield instance
    instance.close()


@pytest.fixture
def client_no_cache(transport: FakeTransport) -> PlkClient:
    """Klient bez cache — do testów, gdzie każde zapytanie ma iść do API."""
    instance = PlkClient(api_key="test-key", use_cache=False, max_retries=0)
    instance._client = httpx.Client(
        transport=transport,
        base_url=instance.settings.base_url,
        headers=instance._client.headers,
        timeout=instance.settings.timeout,
    )
    yield instance
    instance.close()


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> Callable[[], None]:
    """Zamienia ``time.sleep`` na pustą funkcję, żeby testy retry nie trwały.

    :returns: funkcja zwracająca liczbę wywołań ``sleep`` (pozwala sprawdzić
        oczekiwane opóźnienie bez czekania).
    """
    calls: list[float] = []
    monkeypatch.setattr("plk_open_data.client.time.sleep", lambda seconds: calls.append(seconds))
    return lambda: calls  # type: ignore[return-value]