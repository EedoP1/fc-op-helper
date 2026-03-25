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


def test_picks_margin_maximizing_expected_profit():
    """Scorer should pick the margin that maximizes expected_profit, not greedily the highest."""
    from datetime import datetime, timezone, timedelta
    from src.models import SaleRecord, PricePoint

    # Create a player at price=20000 with 100 sales spread over 10 hours
    md = make_player(
        price=20000, num_sales=100, op_sales_pct=0.0, op_margin=0.40,
        hours_of_data=10.0,
    )

    now = datetime.now(timezone.utc)

    # Rebuild sales manually:
    # - 3 sales at 40% above market (28000+) -> also count as 8% OP sales
    # - 37 sales at 8% above market (21600+) but below 40% (< 28000)
    # - 60 normal sales at/below market price
    sales = []
    for i in range(60):
        t = now - timedelta(hours=10.0 * (i / 100))
        sales.append(SaleRecord(
            resource_id=1, sold_at=t, sold_price=20000 - (i % 5) * 100,
        ))
    for i in range(37):
        t = now - timedelta(hours=10.0 * ((60 + i) / 100))
        # At 8%+ above market but below 40%: e.g. 22000 (10% above)
        sales.append(SaleRecord(
            resource_id=1, sold_at=t, sold_price=22000,
        ))
    for i in range(3):
        t = now - timedelta(hours=10.0 * ((97 + i) / 100))
        # At 40%+ above market: 28500
        sales.append(SaleRecord(
            resource_id=1, sold_at=t, sold_price=28500,
        ))

    md.sales = sales

    # Stable price history at 20000
    md.price_history = [
        PricePoint(resource_id=1, recorded_at=now - timedelta(hours=h), lowest_bin=20000)
        for h in range(11)
    ]

    result = score_player(md)
    assert result is not None

    # At 40%: 3 OP sales, ratio=0.03, sell=28000, net=28000-1400-20000=6600, expected=198
    # At 8%: 40 OP sales (37+3), ratio=0.40, sell=21600, net=21600-1080-20000=520, expected=208
    # 8% wins with higher expected_profit
    assert result["margin_pct"] == 8, (
        f"Expected margin 8% (expected_profit=208) but got {result['margin_pct']}% "
        f"(expected_profit={result['expected_profit']})"
    )


def test_optimal_margin_still_picks_highest_when_it_wins():
    """When highest margin has the best expected_profit, it should still be picked."""
    # 10% OP sales all at 40% margin -> only 40% has OP sales, so 40% maximizes expected_profit
    md = make_player(
        price=15000, num_sales=100, op_sales_pct=0.10, op_margin=0.40,
    )
    result = score_player(md)
    assert result is not None
    # At 40%: 10 OP, ratio=0.10, sell=21000, net=21000-1050-15000=4950, expected=495
    # At lower margins: same 10 OP count (sales above 40% also above lower margins),
    # same ratio=0.10, but lower net_profit -> lower expected_profit
    assert result["margin_pct"] == 40
