"""Tests for FutGGClient.get_player_market_data{,_sync} — PricesFetchError semantics."""
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


async def test_defn_ok_prices_ok_no_bin_returns_none(client):
    """Card not on market: both endpoints succeed but no tradeable BIN → None, no raise."""
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
    assert await client.get_player_market_data(ea_id=1) is None


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


def test_sync_defn_ok_prices_ok_no_bin_returns_none(client):
    """Sync path: both endpoints succeed, but no tradeable BIN → None, no raise."""
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

    assert client.get_player_market_data_sync(
        ea_id=1, sync_client=sync_client, prices_fetcher=prices_fetcher,
    ) is None
