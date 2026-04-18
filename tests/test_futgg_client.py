"""Tests for FutGGClient.get_player_market_data{,_sync} — PricesFetchError + shell semantics."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.futgg_client import FutGGClient, PricesFetchError


@pytest.fixture
def client():
    return FutGGClient()


# ── Async path ────────────────────────────────────────────────────────────────

async def test_defn_ok_prices_none_raises(client):
    """Defn returns data but prices is None → PricesFetchError."""
    client.get_player_definition = AsyncMock(return_value={"eaId": 1, "overall": 85})
    client.get_player_prices = AsyncMock(return_value=None)
    with pytest.raises(PricesFetchError):
        await client.get_player_market_data(ea_id=1)


async def test_both_none_returns_none(client):
    """Full outage: defn AND prices None → returns None (unchanged behaviour)."""
    client.get_player_definition = AsyncMock(return_value=None)
    client.get_player_prices = AsyncMock(return_value=None)
    assert await client.get_player_market_data(ea_id=1) is None


async def test_defn_ok_prices_ok_no_bin_returns_shell(client):
    """Card momentarily untradeable: both endpoints succeed, no current_bin
    — returns a shell PlayerMarketData (current_lowest_bin=0), NOT None."""
    client.get_player_definition = AsyncMock(return_value={
        "eaId": 1,
        "overall": 85,
        "position": 19,
        "commonName": "Test",
        "rarity": {"slug": "gold"},
        "club": {},
        "league": {},
        "nation": {},
    })
    # liveAuctions empty + no currentPrice + no overview → _extract_current_bin = None
    client.get_player_prices = AsyncMock(return_value={
        "liveAuctions": [],
        "completedAuctions": [],
        "history": [],
    })
    result = await client.get_player_market_data(ea_id=1)
    assert result is not None
    assert result.current_lowest_bin == 0
    assert result.listing_count == 0
    assert result.price_history == []
    assert result.sales == []
    assert result.live_auction_prices == []
    assert result.live_auctions_raw == []
    assert result.created_at is None  # defn has no createdAt field
    assert result.player.name == "Test"
    assert result.player.rating == 85


async def test_get_player_market_data_returns_shell_when_no_current_bin(client):
    """Shell path also preserves createdAt from defn — critical for promo_dip_buy."""
    client.get_player_definition = AsyncMock(return_value={
        "eaId": 42,
        "overall": 84,
        "position": 19,
        "commonName": "Shell",
        "rarity": {"slug": "gold"},
        "club": {},
        "league": {},
        "nation": {},
        "url": "https://www.fut.gg/players/foo-42/",
        "createdAt": "2026-04-10T12:00:00Z",
    })
    client.get_player_prices = AsyncMock(return_value={
        "liveAuctions": [],
        "completedAuctions": [],
        "history": [],
        "currentPrice": {"price": 0},
        "priceRange": {"maxPrice": 15000000},
    })
    result = await client.get_player_market_data(ea_id=42)
    assert result is not None
    assert result.current_lowest_bin == 0
    assert result.listing_count == 0
    assert result.futgg_url == "https://www.fut.gg/players/foo-42/"
    assert result.max_price_range == 15000000
    assert result.created_at == datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
    assert result.player.resource_id == 42


# ── Sync path ─────────────────────────────────────────────────────────────────

def test_sync_defn_ok_prices_none_raises(client):
    """Sync path: defn OK, prices_fetcher returns None → PricesFetchError."""
    sync_client = MagicMock()
    defn_resp = MagicMock()
    defn_resp.status_code = 200
    defn_resp.json.return_value = {"data": {"eaId": 1, "overall": 85}}
    defn_resp.raise_for_status = MagicMock()
    sync_client.get.return_value = defn_resp

    prices_fetcher = MagicMock(return_value=None)  # prices failed

    with pytest.raises(PricesFetchError):
        client.get_player_market_data_sync(
            ea_id=1, sync_client=sync_client, prices_fetcher=prices_fetcher,
        )


def test_sync_both_none_returns_none(client):
    """Sync path: defn returns None AND prices_fetcher returns None → None, no raise."""
    sync_client = MagicMock()
    defn_resp = MagicMock()
    defn_resp.status_code = 200
    defn_resp.json.return_value = {}  # no "data" key → parsed defn is None
    defn_resp.raise_for_status = MagicMock()
    sync_client.get.return_value = defn_resp

    prices_fetcher = MagicMock(return_value=None)

    assert client.get_player_market_data_sync(
        ea_id=1, sync_client=sync_client, prices_fetcher=prices_fetcher,
    ) is None


def test_sync_defn_ok_prices_ok_no_bin_returns_shell(client):
    """Sync path: both endpoints succeed, no current_bin → shell PlayerMarketData."""
    sync_client = MagicMock()
    defn_resp = MagicMock()
    defn_resp.status_code = 200
    defn_resp.json.return_value = {"data": {
        "eaId": 1,
        "overall": 85,
        "position": 19,
        "commonName": "Test",
        "rarity": {"slug": "gold"},
        "club": {},
        "league": {},
        "nation": {},
    }}
    defn_resp.raise_for_status = MagicMock()
    sync_client.get.return_value = defn_resp

    prices_fetcher = MagicMock(return_value={
        "liveAuctions": [],
        "completedAuctions": [],
        "history": [],
    })

    result = client.get_player_market_data_sync(
        ea_id=1, sync_client=sync_client, prices_fetcher=prices_fetcher,
    )
    assert result is not None
    assert result.current_lowest_bin == 0
    assert result.listing_count == 0
    assert result.player.name == "Test"


def test_get_player_market_data_sync_returns_shell_when_no_current_bin(client):
    """Sync shell path: preserves createdAt from defn."""
    sync_client = MagicMock()
    defn_resp = MagicMock()
    defn_resp.status_code = 200
    defn_resp.json.return_value = {"data": {
        "eaId": 42,
        "overall": 84,
        "position": 19,
        "commonName": "Shell",
        "rarity": {"slug": "gold"},
        "club": {},
        "league": {},
        "nation": {},
        "url": "https://www.fut.gg/players/foo-42/",
        "createdAt": "2026-04-10T12:00:00Z",
    }}
    defn_resp.raise_for_status = MagicMock()
    sync_client.get.return_value = defn_resp

    prices_fetcher = MagicMock(return_value={
        "liveAuctions": [],
        "completedAuctions": [],
        "history": [],
        "currentPrice": {"price": 0},
        "priceRange": {"maxPrice": 15000000},
    })

    result = client.get_player_market_data_sync(
        ea_id=42, sync_client=sync_client, prices_fetcher=prices_fetcher,
    )
    assert result is not None
    assert result.current_lowest_bin == 0
    assert result.futgg_url == "https://www.fut.gg/players/foo-42/"
    assert result.max_price_range == 15000000
    assert result.created_at == datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
