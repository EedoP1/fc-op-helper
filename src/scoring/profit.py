"""Profit margin scorer — calculates net profit after EA's 5% tax."""

from src.config import EA_TAX_RATE
from src.models import HOSSResult


def compute_profit_score(
    buy_price: int, best_op_margin: float, hoss: HOSSResult
) -> tuple[float, int, float]:
    """
    Compute profit margin score and expected net profit.

    Uses the data-driven best_op_margin from HOSS to calculate realistic profit.

    Returns:
        (score 0-100, expected_net_profit in coins, net_profit_pct)
    """
    if buy_price <= 0 or best_op_margin <= 0:
        return 0.0, 0, 0.0

    sell_price = int(buy_price * (1 + best_op_margin))
    ea_tax = int(sell_price * EA_TAX_RATE)
    net_profit = sell_price - ea_tax - buy_price
    net_profit_pct = net_profit / buy_price if buy_price > 0 else 0.0

    # Factor in probability of actually selling at OP price
    # Use op_sell_rate from HOSS as the probability
    probability = hoss.op_sell_rate if hoss.op_sell_rate > 0 else 0.1
    expected_profit_per_attempt = net_profit * probability

    # Score: normalize expected profit percentage
    # 0% = 0 score, 5%+ expected profit = 100 score
    expected_pct = (expected_profit_per_attempt / buy_price) if buy_price > 0 else 0
    score = min(expected_pct / 0.05 * 100, 100.0)

    return max(score, 0.0), net_profit, net_profit_pct
