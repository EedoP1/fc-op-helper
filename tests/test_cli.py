"""Tests for CLI API client behavior (src/main.py)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from src.main import main


# ── Fixtures / shared mock data ────────────────────────────────────────────────

MOCK_PORTFOLIO_RESPONSE = {
    "data": [
        {
            "ea_id": 123,
            "name": "Test Player",
            "rating": 85,
            "position": "ST",
            "price": 15000,
            "margin_pct": 10,
            "op_ratio": 0.15,
            "expected_profit": 450.0,
            "efficiency": 0.03,
            "op_sales": 5,
            "total_sales": 30,
            "expected_profit_per_hour": 450.0,
            "scan_tier": "hot",
            "is_stale": False,
            "last_scanned": "2026-03-25T14:00:00",
        }
    ],
    "count": 1,
    "budget": 1000000,
    "budget_used": 15000,
    "budget_remaining": 985000,
}

MOCK_PLAYER_RESPONSE = {
    "ea_id": 123,
    "name": "Test Player",
    "rating": 85,
    "position": "ST",
    "nation": "Brazil",
    "league": "Premier League",
    "club": "Arsenal",
    "card_type": "Gold Rare",
    "scan_tier": "hot",
    "last_scanned": "2026-03-25T14:00:00",
    "is_stale": False,
    "current_score": {
        "buy_price": 15000,
        "sell_price": 18500,
        "net_profit": 3325,
        "margin_pct": 10,
        "op_sales": 12,
        "total_sales": 87,
        "op_ratio": 0.138,
        "expected_profit": 458.9,
        "efficiency": 0.0306,
        "sales_per_hour": 12.5,
        "scored_at": "2026-03-25T14:00:00",
    },
    "score_history": [],
    "trend": {"direction": "up", "price_change": 500, "efficiency_change": 0.002},
}


def _make_response(status_code: int, body: dict | None = None) -> MagicMock:
    """Build a mock httpx Response-like object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = json.dumps(body) if body else ""
    resp.json.return_value = body or {}
    return resp


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_portfolio_display():
    """Portfolio mode fetches /api/v1/portfolio and renders a table."""
    runner = CliRunner()
    resp = _make_response(200, MOCK_PORTFOLIO_RESPONSE)

    with patch("httpx.AsyncClient") as mock_client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=resp)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = instance

        result = runner.invoke(main, ["--budget", "1000000"])

    assert result.exit_code == 0, result.output
    assert "Test Player" in result.output
    assert "OP Sell Portfolio" in result.output


def test_portfolio_csv_export(tmp_path, monkeypatch):
    """Portfolio mode exports a CSV file with correct columns."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    resp = _make_response(200, MOCK_PORTFOLIO_RESPONSE)

    with patch("httpx.AsyncClient") as mock_client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=resp)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = instance

        result = runner.invoke(main, ["--budget", "1000000"])

    assert result.exit_code == 0, result.output
    assert "Exported:" in result.output

    # Find the CSV file
    csv_files = list(tmp_path.glob("op_sell_list_*.csv"))
    assert len(csv_files) == 1, f"Expected 1 CSV file, got: {csv_files}"

    content = csv_files[0].read_text(encoding="utf-8")
    assert "Rank" in content
    assert "Player" in content
    assert "Test Player" in content
    assert "EP/hr" in content
    assert "Sell Rate" in content
    assert "Efficiency" in content


def test_player_detail_display():
    """Player detail mode fetches /api/v1/players/{ea_id} and renders a panel."""
    runner = CliRunner()
    resp = _make_response(200, MOCK_PLAYER_RESPONSE)

    with patch("httpx.AsyncClient") as mock_client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=resp)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = instance

        result = runner.invoke(main, ["--player", "123"])

    assert result.exit_code == 0, result.output
    assert "Test Player" in result.output
    assert "Player Detail" in result.output
    assert "Arsenal" in result.output
    assert "trending up" in result.output


def test_server_unreachable_exits_1():
    """When server is unreachable, prints error with URL and exits code 1."""
    runner = CliRunner()

    with patch("httpx.AsyncClient") as mock_client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(side_effect=Exception("connect error"))
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)

        # Simulate ConnectError specifically
        import httpx as _httpx
        instance.get = AsyncMock(
            side_effect=_httpx.ConnectError("Connection refused")
        )
        mock_client_cls.return_value = instance

        result = runner.invoke(main, ["--budget", "1000000"])

    assert result.exit_code == 1
    assert "localhost:8000" in result.output or "Cannot reach server" in result.output


def test_budget_and_player_mutually_exclusive():
    """Passing both --budget and --player shows usage error and exits 1."""
    runner = CliRunner()
    result = runner.invoke(main, ["--budget", "1000000", "--player", "123"])
    assert result.exit_code == 1
    assert "mutually exclusive" in result.output


def test_neither_budget_nor_player_exits_1():
    """Passing neither --budget nor --player exits with code 1 and usage hint."""
    runner = CliRunner()
    result = runner.invoke(main, [])
    assert result.exit_code == 1
    assert "--budget" in result.output or "--player" in result.output


def test_api_returns_500_graceful():
    """API 500 response prints human-readable error, not Python traceback."""
    runner = CliRunner()
    resp = _make_response(500, None)
    resp.text = "Internal Server Error"
    resp.json.side_effect = ValueError("no JSON")

    with patch("httpx.AsyncClient") as mock_client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=resp)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = instance

        result = runner.invoke(main, ["--budget", "1000000"])

    assert result.exit_code == 1
    # Should contain human-readable message, not a Python traceback
    assert "500" in result.output
    assert "Traceback" not in result.output


def test_player_not_found_returns_404_gracefully():
    """404 response for player detail prints readable message and exits 1."""
    runner = CliRunner()
    resp = _make_response(404, {"detail": "Player not found"})

    with patch("httpx.AsyncClient") as mock_client_cls:
        instance = AsyncMock()
        instance.get = AsyncMock(return_value=resp)
        instance.__aenter__ = AsyncMock(return_value=instance)
        instance.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = instance

        result = runner.invoke(main, ["--player", "99999"])

    assert result.exit_code == 1
    assert "not found" in result.output.lower() or "99999" in result.output
