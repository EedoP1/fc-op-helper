"""Integration test: CLI as API client with mocked HTTP responses."""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

import httpx
import pytest
from click.testing import CliRunner

from src.main import main


def _mock_response(status_code: int = 200, json_data: dict | None = None,
                   text: str = "") -> MagicMock:
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text or json.dumps(json_data or {})
    resp.json.return_value = json_data or {}
    return resp


PORTFOLIO_DATA = {
    "data": [
        {
            "ea_id": i, "name": f"Player {i}", "rating": 80 + i,
            "position": "ST", "price": 15000 + i * 1000,
            "margin_pct": 10, "op_ratio": 0.12,
            "expected_profit": 450.0 + i * 10, "efficiency": 0.03,
            "scan_tier": "hot", "is_stale": False,
            "last_scanned": "2026-03-25T14:00:00",
        }
        for i in range(1, 11)
    ],
    "count": 10,
    "budget": 500000,
    "budget_used": 165000,
    "budget_remaining": 335000,
}


def test_full_pipeline_displays_portfolio():
    """CLI fetches portfolio from API and displays Rich table."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_response(200, PORTFOLIO_DATA))

    with patch("src.main.httpx.AsyncClient", return_value=mock_client):
        runner = CliRunner()
        result = runner.invoke(main, ["--budget", "500000"])

    assert result.exit_code == 0
    assert "OP Sell Portfolio" in result.output
    assert "Exported" in result.output


def test_pipeline_with_empty_portfolio():
    """CLI handles empty portfolio gracefully."""
    empty_data = {
        "data": [], "count": 0, "budget": 500000,
        "budget_used": 0, "budget_remaining": 500000,
    }
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_response(200, empty_data))

    with patch("src.main.httpx.AsyncClient", return_value=mock_client):
        runner = CliRunner()
        result = runner.invoke(main, ["--budget", "500000"])

    assert result.exit_code == 0
    assert "No players selected" in result.output


def test_pipeline_csv_contains_all_players():
    """CSV export includes all players from API response."""
    import csv
    import glob
    import os

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=_mock_response(200, PORTFOLIO_DATA))

    with patch("src.main.httpx.AsyncClient", return_value=mock_client):
        runner = CliRunner()
        result = runner.invoke(main, ["--budget", "500000"])

    assert result.exit_code == 0

    csvs = sorted(glob.glob("op_sell_list_*.csv"), key=os.path.getmtime)
    assert csvs, "No CSV exported"

    with open(csvs[-1]) as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 10
    assert all("Player" in r and "Buy" in r for r in rows)


# ── V2 scorer integration test ────────────────────────────────────────────────

async def test_v2_scorer_writes_score():
    """Integration: scan_player with enough listing data produces v2 score.

    Seeds enough resolved ListingObservation rows to exceed BOOTSTRAP_MIN_OBSERVATIONS,
    then calls scan_player and asserts the written PlayerScore has v2 fields populated.
    """
    from src.server.db import create_engine_and_tables
    from src.server.models_db import PlayerRecord, PlayerScore, ListingObservation
    from src.server.scanner import ScannerService
    from src.server.circuit_breaker import CircuitBreaker
    from src.config import BOOTSTRAP_MIN_OBSERVATIONS, MIN_OP_OBSERVATIONS
    from tests.mock_client import make_player
    from sqlalchemy import select

    engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
    try:
        ea_id = 9001
        buy_price = 20000
        op_sell_price = int(buy_price * 1.20)  # 20% above market
        now = datetime.utcnow()

        # Seed PlayerRecord
        async with session_factory() as session:
            session.add(PlayerRecord(
                ea_id=ea_id, name="V2 Test Player", rating=88, position="ST",
                nation="Brazil", league="LaLiga", club="Real Madrid", card_type="gold",
                scan_tier="normal", is_active=True, listing_count=25, sales_per_hour=10.0,
            ))
            await session.commit()

        # Seed enough resolved ListingObservations to exceed BOOTSTRAP_MIN_OBSERVATIONS
        # Use 15 observations: 10 OP sold, 5 OP expired (all at 20% above market)
        n_obs = max(BOOTSTRAP_MIN_OBSERVATIONS + 5, MIN_OP_OBSERVATIONS + 12)
        async with session_factory() as session:
            for i in range(n_obs):
                hours_ago = i + 1
                outcome = "sold" if i < (n_obs * 2 // 3) else "expired"
                session.add(ListingObservation(
                    fingerprint=f"v2test:{ea_id}:{i}",
                    ea_id=ea_id,
                    buy_now_price=op_sell_price,
                    market_price_at_obs=buy_price,
                    first_seen_at=now - timedelta(hours=hours_ago + 1),
                    last_seen_at=now - timedelta(hours=hours_ago),
                    scan_count=1,
                    outcome=outcome,
                    resolved_at=now - timedelta(hours=hours_ago),
                ))
            await session.commit()

        # Set up scanner with mock client
        # Use parameters that satisfy v1 scorer: >=7 sales/hr, >=3 OP sales, >=20 listings
        # 100 sales over 10 hours = 10 sales/hr, 15% OP rate = 15 OP sales at 40% margin
        cb = CircuitBreaker(failure_threshold=5, success_threshold=2, recovery_timeout=60.0)
        svc = ScannerService(session_factory=session_factory, circuit_breaker=cb)
        market_data = make_player(
            ea_id=ea_id, price=buy_price, num_sales=100, num_listings=25,
            op_sales_pct=0.15, op_margin=0.40, hours_of_data=10.0,
        )
        mock_client = AsyncMock()
        mock_client.get_player_market_data = AsyncMock(return_value=market_data)
        svc._client = mock_client

        await svc.scan_player(ea_id)

        async with session_factory() as session:
            result = await session.execute(
                select(PlayerScore)
                .where(PlayerScore.ea_id == ea_id, PlayerScore.is_viable == True)  # noqa: E712
                .order_by(PlayerScore.scored_at.desc())
                .limit(1)
            )
            score_row = result.scalars().first()

        assert score_row is not None, "Expected a viable PlayerScore row"
        assert score_row.expected_profit_per_hour is not None, (
            "Expected expected_profit_per_hour to be populated by v2 scorer"
        )
    finally:
        await engine.dispose()
