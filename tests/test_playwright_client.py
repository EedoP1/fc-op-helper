"""Tests for PlaywrightPricesClient Cloudflare poll loop."""
import asyncio
from unittest.mock import AsyncMock

import pytest

from src.server.playwright_client import PlaywrightPricesClient


class _FakePage:
    """Fake Playwright Page. content() returns values from a script list, one per call.

    The last script entry is repeated forever after it is reached so callers can
    simulate "challenge never clears" without infinite-length lists.
    """

    def __init__(self, content_script: list[str]):
        self._script = list(content_script)
        self.goto = AsyncMock()

    async def content(self) -> str:
        if len(self._script) > 1:
            return self._script.pop(0)
        return self._script[0]


@pytest.fixture
def client_with_fake_pool():
    """Build a PlaywrightPricesClient with an empty page pool (caller seeds it)."""
    client = PlaywrightPricesClient()
    client._page_pool = asyncio.Queue()
    return client


async def test_fetch_prices_polls_and_succeeds(client_with_fake_pool, monkeypatch):
    """Challenge clears after 3 polls — returns parsed data, loop exits early."""
    client = client_with_fake_pool
    page = _FakePage([
        '<html><h1>Just a moment</h1></html>',
        '<html><h1>Just a moment</h1></html>',
        '<html><h1>Just a moment</h1></html>',
        '<pre>{"data": {"ok": true}}</pre>',
    ])
    await client._page_pool.put(page)

    sleeps: list[float] = []
    orig_sleep = asyncio.sleep

    async def fake_sleep(d):
        sleeps.append(d)
        await orig_sleep(0)  # yield to loop without actually waiting

    monkeypatch.setattr("src.server.playwright_client.asyncio.sleep", fake_sleep)

    result = await client._fetch_prices(ea_id=12345)
    assert result == {"ok": True}
    # Either 3 polls (challenge clears on 4th content read) or 4 (one extra after break).
    assert 3 <= len(sleeps) <= 4
    assert all(d == 0.5 for d in sleeps)


async def test_fetch_prices_times_out_returns_none(client_with_fake_pool, monkeypatch):
    """Challenge never clears in 30s — returns None after ~60 polls."""
    client = client_with_fake_pool
    page = _FakePage(['<html><h1>Just a moment</h1></html>'])
    await client._page_pool.put(page)

    fake_now = [0.0]

    def fake_monotonic():
        return fake_now[0]

    async def fake_sleep(d):
        fake_now[0] += d

    monkeypatch.setattr("src.server.playwright_client.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("src.server.playwright_client.time.monotonic", fake_monotonic)

    result = await client._fetch_prices(ea_id=12345)
    assert result is None
    # 30s budget at 0.5s/poll → roughly 60 sleeps. Check the fake clock advanced past 30s.
    assert fake_now[0] >= 30.0


async def test_fetch_prices_no_challenge(client_with_fake_pool, monkeypatch):
    """No challenge in content — returns data immediately, zero sleeps."""
    client = client_with_fake_pool
    page = _FakePage(['<pre>{"data": {"ok": true}}</pre>'])
    await client._page_pool.put(page)

    sleep_count = [0]

    async def fake_sleep(d):
        sleep_count[0] += 1

    monkeypatch.setattr("src.server.playwright_client.asyncio.sleep", fake_sleep)

    result = await client._fetch_prices(ea_id=12345)
    assert result == {"ok": True}
    assert sleep_count[0] == 0
