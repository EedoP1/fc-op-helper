"""Integration test: full pipeline with mock data source."""

import asyncio
import pytest

from tests.mock_client import MockClient, make_player
from src.main import run


@pytest.mark.asyncio
async def test_full_pipeline_with_mock_client(capsys):
    """Run the full pipeline with mock data and verify it produces output."""
    players = [
        make_player(ea_id=i, name=f"Player {i}", price=15000 + i * 1000,
                     num_sales=100, op_sales_pct=0.12, op_margin=0.40,
                     num_listings=30, hours_of_data=10)
        for i in range(1, 51)
    ]
    client = MockClient(players)

    await run(budget=500000, verbose=False, client=client)

    captured = capsys.readouterr()
    assert "OP Sell Portfolio" in captured.out
    assert "Exported" in captured.out


@pytest.mark.asyncio
async def test_pipeline_with_no_viable_players(capsys):
    """Pipeline should handle gracefully when no players pass scoring."""
    # All players have 0 OP sales — none will score
    players = [
        make_player(ea_id=i, price=15000, num_sales=100,
                     op_sales_pct=0.0, op_margin=0.40, num_listings=30)
        for i in range(1, 11)
    ]
    client = MockClient(players)

    await run(budget=500000, verbose=False, client=client)

    captured = capsys.readouterr()
    assert "No players selected" in captured.out or "0 viable" in captured.out


@pytest.mark.asyncio
async def test_pipeline_respects_budget(capsys):
    """Total cost of selected players should not exceed budget."""
    players = [
        make_player(ea_id=i, name=f"Player {i}", price=20000,
                     num_sales=100, op_sales_pct=0.15, op_margin=0.40,
                     num_listings=30, hours_of_data=8)
        for i in range(1, 101)
    ]
    client = MockClient(players)

    await run(budget=300000, verbose=False, client=client)

    # Check CSV output
    import glob, csv, os
    csvs = sorted(glob.glob("op_sell_list_*.csv"), key=os.path.getmtime)
    assert csvs, "No CSV exported"

    with open(csvs[-1]) as f:
        rows = list(csv.DictReader(f))

    total_cost = sum(int(r["Buy"]) for r in rows)
    assert total_cost <= 300000
    assert len(rows) > 0
