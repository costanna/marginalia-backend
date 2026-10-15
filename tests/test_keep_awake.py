"""Keep-awake estilo woke-dyno: sin hilos ni HTTP real en tests."""
import threading

import pytest

import app.services.keep_awake as keep_awake
from app.services.keep_awake import (
    keep_awake_configured,
    ping_once,
    reset_keep_awake_state,
    start_keep_awake,
)


@pytest.fixture(autouse=True)
def _no_threads():
    reset_keep_awake_state()
    yield
    reset_keep_awake_state()


class _FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_disabled_without_flag_or_url():
    assert keep_awake_configured(False, "") is False
    assert keep_awake_configured(True, "") is False
    assert keep_awake_configured(False, "https://api.example.com/api/v1/health") is False


def test_needs_absolute_url():
    assert keep_awake_configured(True, "https://api.example.com/api/v1/health") is True
    assert keep_awake_configured(True, "localhost:8000/api/v1/health") is False
    assert keep_awake_configured(True, None) is False


def test_ping_once_ok(monkeypatch):
    monkeypatch.setattr(
        keep_awake.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(200)
    )
    assert ping_once("https://api.example.com/api/v1/health") is True


def test_ping_once_fails_silently(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("sin red")

    monkeypatch.setattr(keep_awake.urllib.request, "urlopen", boom)
    assert ping_once("https://api.example.com/api/v1/health") is False
    monkeypatch.setattr(
        keep_awake.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(500)
    )
    assert ping_once("https://api.example.com/api/v1/health") is False


def test_start_spawns_one_daemon_thread_pinging_the_url():
    calls = []
    stop = threading.Event()

    def fake_ping(url):
        calls.append(url)
        stop.set()  # el hilo real seguiría; en tests paramos tras el primer ping
        return True

    def fake_sleep(seconds):
        assert seconds == 10 * 60

    thread = start_keep_awake(
        "https://api.example.com/api/v1/health", 10, ping=fake_ping, sleep=fake_sleep, stop=stop
    )
    assert isinstance(thread, threading.Thread)
    assert thread.daemon is True
    thread.join(timeout=5)
    assert calls == ["https://api.example.com/api/v1/health"]
    # Segunda llamada no duplica el hilo.
    assert (
        start_keep_awake("https://api.example.com/api/v1/health", 10, ping=fake_ping, sleep=fake_sleep) is None
    )


def test_start_rejects_bad_url():
    assert start_keep_awake("localhost:8000/api/v1/health", 10) is None
    assert start_keep_awake("https://api.example.com/api/v1/health", 0) is None


def test_lifespan_starts_only_when_configured(monkeypatch):
    import app.main as main_module

    calls = []
    monkeypatch.setattr(main_module, "start_keep_awake", lambda *a, **k: calls.append((a, k)) or "thread")

    async def _noop():
        return None

    monkeypatch.setattr(main_module, "aclose_llm_client", _noop)

    async def run_lifespan(settings):
        monkeypatch.setattr(main_module, "get_settings", lambda: settings)
        async with main_module.lifespan(main_module.app):
            pass

    import asyncio
    from types import SimpleNamespace

    asyncio.run(run_lifespan(SimpleNamespace(keep_awake_enabled=False, keep_awake_url="", keep_awake_minutes=10)))
    assert calls == []
    asyncio.run(
        run_lifespan(
            SimpleNamespace(
                keep_awake_enabled=True,
                keep_awake_url="https://api.example.com/api/v1/health",
                keep_awake_minutes=10,
            )
        )
    )
    assert calls == [(("https://api.example.com/api/v1/health", 10), {})]
