"""Integration test: CLI as API client with mocked HTTP responses."""

import json
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
