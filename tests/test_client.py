"""Testy warstwy HTTP: retry, cache, paginacja, obsługa błędów i klucza API."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import FakeTransport

from plk_open_data.cache import DiskCache, make_key
from plk_open_data.client import PlkClient, _clean_params, _has_next_page
from plk_open_data.errors import (
    AuthenticationError,
    ConfigurationError,
    NotFoundError,
    RateLimitError,
    ServerError,
    TransportError,
    ValidationError,
)


class TestBrakKlucza:
    def test_endpoint_requiring_key_fails_z_czytelnym_bledem(
        self,
        transport: FakeTransport,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Test musi być niezależny od lokalnego .env — inaczej przechodzi
        # u jednego dewelopera, a u drugiego nie.
        monkeypatch.setattr("plk_open_data.config.load_api_key", lambda explicit=None: explicit)

        with PlkClient(api_key=None, cache_dir=tmp_path, use_cache=False) as client:
            with pytest.raises(ConfigurationError, match="PLK_API_KEY"):
                client.get("/api/v1/dictionaries/stations")

    def test_endpoint_publiczny_dziala_bez_klucza(
        self, transport: FakeTransport, tmp_path: Path
    ) -> None:
        transport.add(json_body={"version": "1.1.4"})
        with PlkClient(api_key=None, cache_dir=tmp_path, use_cache=False) as client:
            _client_with_transport(transport, client)
            assert client.get("/api/version") == {"version": "1.1.4"}

    def test_klucz_jest_wysylany_w_naglowku(self, client: PlkClient, transport: FakeTransport) -> None:
        transport.add(json_body={})
        client.get("/api/version")
        assert client._client.headers.get("x-api-key") == "test-key"


class TestMapowanieBledow:
    def test_401_staje_sie_bledem_autoryzacji(
        self, client: PlkClient, transport: FakeTransport
    ) -> None:
        transport.add(
            status_code=401,
            json_body={"error": "Unauthorized", "errorCode": "MissingKey", "messageEn": "no key"},
        )
        with pytest.raises(AuthenticationError):
            client.get("/api/v1/schedules")

    def test_404_staje_sie_not_found(self, client: PlkClient, transport: FakeTransport) -> None:
        transport.add(status_code=404, json_body={"error": "NotFound"})
        with pytest.raises(NotFoundError):
            client.get("/api/v1/schedules/route/25/999999")

    def test_400_staje_sie_bledem_walidacji(
        self, client: PlkClient, transport: FakeTransport
    ) -> None:
        transport.add(status_code=400, json_body={"messageEn": "stations cannot combine with from"})
        with pytest.raises(ValidationError, match="cannot combine"):
            client.get("/api/v1/schedules")

    def test_429_bez_ponowień_zgłasza_rate_limit(
        self, client: PlkClient, transport: FakeTransport
    ) -> None:
        transport.add(status_code=429, json_body={"messageEn": "limit exceeded"}, headers={"retry-after": "12"})
        with pytest.raises(RateLimitError) as exc:
            client.get("/api/v1/operations")
        assert exc.value.retry_after == 12.0

    def test_komunikat_po_polsku_nie_jest_uzywany_gdy_jest_angielski(
        self, client: PlkClient, transport: FakeTransport
    ) -> None:
        transport.add(
            status_code=400,
            json_body={
                "message": "Niepoprawne dane \u017acy",
                "messageEn": "Invalid parameters supplied",
            },
        )
        with pytest.raises(ValidationError, match="Invalid parameters supplied"):
            client.get("/api/v1/schedules")

    def test_licznik_bledow_rosnie(self, client: PlkClient, transport: FakeTransport) -> None:
        transport.add(status_code=503, json_body={})
        with pytest.raises(ServerError):
            client.get("/api/version")
        assert client.usage.errors == 1


class TestRetry:
    def test_ponawia_po_429_i_zwraca_wynik(
        self, transport: FakeTransport, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        delays: list[float] = []
        monkeypatch.setattr("plk_open_data.client.time.sleep", delays.append)

        transport.add(status_code=429, json_body={}, headers={"retry-after": "3"})
        transport.add(json_body={"ok": True})

        with PlkClient(api_key="k", cache_dir=tmp_path, use_cache=False, max_retries=2) as client:
            client._client = _client_with_transport(transport, client)
            assert client.get("/api/version") == {"ok": True}

        assert delays == [3.0]
        assert client.usage.rate_limited == 1

    def test_szanuje_retry_after_zamiast_backoffu(
        self, transport: FakeTransport, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        delays: list[float] = []
        monkeypatch.setattr("plk_open_data.client.time.sleep", delays.append)

        transport.add(status_code=503, headers={"retry-after": "7"})
        transport.add(json_body={"ok": True})

        with PlkClient(api_key="k", cache_dir=tmp_path, use_cache=False, max_retries=1) as client:
            client._client = _client_with_transport(transport, client)
            client.get("/api/version")

        assert delays == [7.0]

    def test_ponawia_po_500_bez_naglowka(
        self, transport: FakeTransport, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("plk_open_data.client.time.sleep", lambda _: None)
        transport.add(status_code=500, json_body={})
        transport.add(json_body={"ok": True})

        with PlkClient(api_key="k", cache_dir=tmp_path, use_cache=False, max_retries=1) as client:
            client._client = _client_with_transport(transport, client)
            assert client.get("/api/version") == {"ok": True}

    def test_wyciaga_sie_gdy_ponowienia_sie_koncza(
        self, transport: FakeTransport, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("plk_open_data.client.time.sleep", lambda _: None)
        for _ in range(3):
            transport.add(status_code=503, json_body={})

        with PlkClient(api_key="k", cache_dir=tmp_path, use_cache=False, max_retries=2) as client:
            client._client = _client_with_transport(transport, client)
            with pytest.raises(ServerError):
                client.get("/api/version")

    def test_401_nie_jest_ponawiane(
        self, transport: FakeTransport, tmp_path: Path
    ) -> None:
        transport.add(status_code=401, json_body={})
        with PlkClient(api_key="zly", cache_dir=tmp_path, use_cache=False, max_retries=3) as client:
            client._client = _client_with_transport(transport, client)
            with pytest.raises(AuthenticationError):
                client.get("/api/v1/schedules")

        assert len(transport.requests) == 1


class TestCache:
    def test_drugie_zapytanie_idzie_z_cache(
        self, client: PlkClient, transport: FakeTransport
    ) -> None:
        transport.add(json_body={"totalCount": 5})
        first = client.get("/api/v1/dictionaries/stations", cache_ttl=60)
        second = client.get("/api/v1/dictionaries/stations", cache_ttl=60)

        assert first == second == {"totalCount": 5}
        assert len(transport.requests) == 1
        assert client.usage.cache_hits == 1

    def test_dane_real_time_moga_miec_krotki_cache(
        self, client: PlkClient, transport: FakeTransport, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import plk_open_data.cache as cache_module

        real_time = cache_module.time.time()
        transport.add(json_body={"n": 1})
        client.get("/api/v1/operations", cache_ttl=60)

        # Przesuwamy czas o dwie minuty — wpis wygasa.
        monkeypatch.setattr(cache_module.time, "time", lambda: real_time + 120)
        transport.add(json_body={"n": 2})

        assert client.get("/api/v1/operations", cache_ttl=60) == {"n": 2}

    def test_cache_ttl_zero_wylacza_cache(
        self, client_no_cache: PlkClient, transport: FakeTransport
    ) -> None:
        transport.add(json_body={"n": 1})
        client_no_cache.get("/api/v1/operations", cache_ttl=0)
        transport.add(json_body={"n": 2})
        assert client_no_cache.get("/api/v1/operations", cache_ttl=0) == {"n": 2}

    def test_klucz_cache_nie_zawiera_sekreta(self, tmp_path: Path) -> None:
        key = make_key("/api/v1/schedules", {"from": "WARSZAWA"})
        assert "sekret" not in key
        assert key.startswith("plk_")

    def test_rozne_parametry_daja_rozne_klucze(self) -> None:
        assert make_key("/api/v1/schedules", {"from": "A"}) != make_key("/api/v1/schedules", {"from": "B"})

    def test_warianty_kolizji_w_cache_rozroznione(self) -> None:
        """Ten sam endpoint w wariancie pełnym i skróconym musi mieć osobne wpisy."""
        assert make_key("/api/v1/schedules", {}, variant="full") != make_key(
            "/api/v1/schedules", {}, variant="short"
        )


class TestPaginacja:
    def test_iteruje_po_wszystkich_stronach(
        self, client_no_cache: PlkClient, transport: FakeTransport
    ) -> None:
        transport.add(
            json_body={
                "trains": [{"id": 1}, {"id": 2}],
                "pagination": {"page": 1, "totalPages": 2, "hasNextPage": True},
            }
        )
        transport.add(
            json_body={
                "trains": [{"id": 3}],
                "pagination": {"page": 2, "totalPages": 2, "hasNextPage": False},
            }
        )

        trains = list(client_no_cache.paginate("/api/v1/operations", items_key="trains"))

        assert [t["id"] for t in trains] == [1, 2, 3]
        assert "page=2" in transport.called_urls[1]

    def test_rozpoznaje_skrocone_nazwy_paginacji(
        self, client_no_cache: PlkClient, transport: FakeTransport
    ) -> None:
        """Wariant /shortened użyje ``pg`` i ``hn`` zamiast ``pagination``."""
        transport.add(json_body={"tr": [{"id": 1}], "pg": {"p": 1, "hp": True, "hn": True}})
        transport.add(json_body={"tr": [{"id": 2}], "pg": {"p": 2, "hp": False, "hn": False}})

        trains = list(client_no_cache.paginate("/api/v1/operations/shortened", items_key="tr"))
        assert [t["id"] for t in trains] == [1, 2]

    def test_max_pages_zatrzymuje_petle(
        self, client_no_cache: PlkClient, transport: FakeTransport
    ) -> None:
        for page in range(1, 6):
            transport.add(
                json_body={
                    "trains": [{"id": page}],
                    "pagination": {"page": page, "hasNextPage": True},
                }
            )

        trains = list(
            client_no_cache.paginate("/api/v1/operations", items_key="trains", max_pages=2)
        )
        assert len(trains) == 2

    def test_post_odpowiedzi_nie_sa_cache_wane(
        self, client: PlkClient, transport: FakeTransport
    ) -> None:
        transport.add(json_body={"n": 1})
        client.post("/api/v1/apikey/accept-terms", json={"acceptTermsOfService": True})
        transport.add(json_body={"n": 2})
        client.post("/api/v1/apikey/accept-terms", json={"acceptTermsOfService": True})
        assert len(transport.requests) == 2


class TestHelperyKlienta:
    def test_none_i_puste_sa_odsiewane(self) -> None:
        assert _clean_params({"a": None, "b": "", "c": 1}) == {"c": 1}

    def test_bool_jest_serializowany_jako_tekst(self) -> None:
        assert _clean_params({"fullRoute": True, "dictionaries": False}) == {
            "fullRoute": "true",
            "dictionaries": "false",
        }

    def test_lista_jest_laczona_przecinkami(self) -> None:
        assert _clean_params({"stations": [1, 2, 3]}) == {"stations": "1,2,3"}

    def test_puste_wartosci_daja_none(self) -> None:
        assert _clean_params({}) is None
        assert _clean_params({"a": None}) is None

    def test_has_next_page_liczy_total_pages_gdy_brak_flagi(self) -> None:
        assert _has_next_page({"page": 1, "totalPages": 3}) is True
        assert _has_next_page({"page": 3, "totalPages": 3}) is False

    def test_offline_nie_wykonuje_zapytan(
        self, transport: FakeTransport, tmp_path: Path
    ) -> None:
        transport.add(json_body={})
        with PlkClient(api_key="k", cache_dir=tmp_path, offline=True) as client:
            client._client = _client_with_transport(transport, client)
            with pytest.raises(TransportError, match="offline"):
                client.get("/api/v1/schedules")
        assert transport.requests == []


class TestCacheDyskowy:
    def test_wpis_wygaśnie_po_ttl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cache = DiskCache(tmp_path / "c")
        cache.set("plk_test", {"wartosc": 1}, ttl=100)

        monkeypatch.setattr("plk_open_data.cache.time.time", lambda: 1_000_000.0)
        cache.set("plk_test2", {"wartosc": 2}, ttl=10)

        # Przesuwamy czas wewnątrz modułu cache
        import plk_open_data.cache as cache_module

        real_time = cache_module.time.time
        monkeypatch.setattr(cache_module.time, "time", lambda: 1_000_000.0 + 50)
        assert cache.get("plk_test2") is None
        assert cache.get("plk_test") == {"wartosc": 1}
        monkeypatch.setattr(cache_module.time, "time", real_time)

    def test_uszkodzony_wpis_jest_ignorowany(self, tmp_path: Path) -> None:
        cache = DiskCache(tmp_path / "c")
        cache.directory.mkdir(parents=True, exist_ok=True)
        (cache.directory / "plk_abc.json").write_text("to nie jest json", encoding="utf-8")

        assert cache.get("plk_abc") is None
        assert not (cache.directory / "plk_abc.json").exists()

    def test_clear_usuwa_wszystkie_wpisy(self, tmp_path: Path) -> None:
        cache = DiskCache(tmp_path / "c")
        for i in range(3):
            cache.set(f"plk_{i}", {"i": i}, ttl=100)
        assert cache.clear() == 3
        assert cache.get("plk_0") is None


def _client_with_transport(transport: FakeTransport, client: PlkClient):
    """Podmienia transport klienta na atrapę (helper dla testów z własnymi limitami)."""
    import httpx

    old = client._client
    client._client = httpx.Client(
        transport=transport,
        base_url=client.settings.base_url,
        headers=old.headers,
        timeout=client.settings.timeout,
    )
    return client._client