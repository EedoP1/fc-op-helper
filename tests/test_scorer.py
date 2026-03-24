"""Tests for the OP sell scorer."""

from tests.mock_client import make_player
from src.scorer import score_player


def test_player_with_strong_op_sales_scores():
    """A player with 15% OP sales at 40% margin should score."""
    md = make_player(
        price=20000, num_sales=100, op_sales_pct=0.15, op_margin=0.40,
    )
    result = score_player(md)
    assert result is not None
    assert result["margin_pct"] == 40
    assert result["op_sales"] >= 3
    assert result["net_profit"] > 0
    assert result["expected_profit"] > 0


def test_player_with_no_op_sales_rejected():
    """A player with 0 OP sales should be rejected."""
    md = make_player(
        price=20000, num_sales=100, op_sales_pct=0.0, op_margin=0.40,
    )
    result = score_player(md)
    assert result is None


def test_player_with_1_op_sale_rejected():
    """A player with only 1 OP sale (below min 3) should be rejected."""
    md = make_player(
        price=20000, num_sales=100, op_sales_pct=0.01, op_margin=0.40,
    )
    result = score_player(md)
    assert result is None


def test_low_liquidity_player_rejected():
    """A player with < 7 sales/hr should be rejected."""
    md = make_player(
        price=20000, num_sales=20, op_sales_pct=0.20, op_margin=0.40,
        hours_of_data=10.0,  # 20/10 = 2 sales/hr < 7
    )
    result = score_player(md)
    assert result is None


def test_too_few_listings_rejected():
    """A player with < 20 live listings should be rejected."""
    md = make_player(
        price=20000, num_sales=100, op_sales_pct=0.15, op_margin=0.40,
        num_listings=10,
    )
    result = score_player(md)
    assert result is None


def test_picks_highest_margin_with_enough_op_sales():
    """Should pick 40% margin if it has 3+ OP sales, not drop to lower."""
    md = make_player(
        price=15000, num_sales=100, op_sales_pct=0.10, op_margin=0.40,
    )
    result = score_player(md)
    assert result is not None
    assert result["margin_pct"] == 40


def test_falls_to_lower_margin_when_high_margin_has_too_few():
    """If 40% margin has < 3 OP sales but lower margin has 3+, pick lower."""
    md = make_player(
        price=15000, num_sales=100, op_sales_pct=0.10, op_margin=0.20,
    )
    result = score_player(md)
    assert result is not None
    # Should pick a margin <= 25% (not 40%) since OP sales are at 20% above
    assert result["margin_pct"] <= 25


def test_expected_profit_calculation():
    """expected_profit = net_profit × op_ratio."""
    md = make_player(
        price=10000, num_sales=100, op_sales_pct=0.20, op_margin=0.40,
    )
    result = score_player(md)
    assert result is not None
    expected = result["net_profit"] * result["op_ratio"]
    assert abs(result["expected_profit"] - expected) < 1


def test_net_profit_accounts_for_ea_tax():
    """net_profit should be sell_price - 5% tax - buy_price."""
    md = make_player(
        price=10000, num_sales=100, op_sales_pct=0.20, op_margin=0.40,
    )
    result = score_player(md)
    assert result is not None
    sell = int(10000 * 1.40)
    tax = int(sell * 0.05)
    assert result["net_profit"] == sell - tax - 10000


def test_op_detection_uses_price_at_time():
    """OP sales should be checked against price at time of sale, not current."""
    # Make a player whose price was 30k when sales happened, now dropped to 20k
    # Sales at 28k look OP vs current 20k but NOT vs historical 30k
    md = make_player(
        price=20000, num_sales=100, op_sales_pct=0.0, op_margin=0.40,
    )
    # Override price history to show higher historical price
    from datetime import datetime, timezone, timedelta
    from src.models import PricePoint
    now = datetime.now(timezone.utc)
    md.price_history = [
        PricePoint(resource_id=1, recorded_at=now - timedelta(hours=h), lowest_bin=30000)
        for h in range(11)
    ]
    # Override some sales to be at 28k (above current 20k but below historical 30k)
    from src.models import SaleRecord
    for i in range(5):
        md.sales[i] = SaleRecord(
            resource_id=1,
            sold_at=now - timedelta(hours=i),
            sold_price=28000,
        )

    result = score_player(md)
    # These 28k sales should NOT count as OP at 40% margin
    # because 30k * 1.40 = 42k > 28k
    # And at lower margins: 30k * 1.03 = 30.9k > 28k
    # So no OP sales should be found
    assert result is None
