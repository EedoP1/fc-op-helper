"""
Portfolio optimizer — maximize total profit/hour within budget.

Strategy:
1. Sort candidates by profit_per_hour descending
2. Greedily pack highest profit/hr players that fit
3. SWAP LOOP: Check if removing the most expensive player and filling
   the freed budget with 2+ cheaper players yields more total profit/hr.
   Repeat until 100 players or swapping isn't profitable.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from src.config import (
    EA_TAX_RATE,
    MIN_NET_PROFIT_PCT,
    TARGET_PLAYER_COUNT,
)
from src.models import (
    ConfidenceTier,
    Player,
    PlayerScore,
    PortfolioSummary,
    Recommendation,
    HOSSResult,
)
from src.scoring.tier import assign_tier

logger = logging.getLogger(__name__)

MAX_SINGLE_PLAYER_BUDGET_PCT = 0.10


def _enrich_candidates(players: list[dict], budget: int) -> list[dict]:
    """Compute profit metrics for all candidates."""
    max_per_player = int(budget * MAX_SINGLE_PLAYER_BUDGET_PCT)
    candidates = []

    for entry in players:
        hoss: HOSSResult = entry["hoss"]
        buy_price = entry["current_bin"]
        best_margin = hoss.best_op_margin

        if buy_price <= 0 or buy_price > max_per_player:
            continue

        sell_price = int(buy_price * (1 + best_margin))
        ea_tax = int(sell_price * EA_TAX_RATE)
        net_profit = sell_price - ea_tax - buy_price
        net_profit_pct = net_profit / buy_price

        if net_profit_pct < MIN_NET_PROFIT_PCT or net_profit <= 0:
            continue

        my_sells_per_hour = hoss.my_op_sells_per_hour
        profit_per_hour = net_profit * my_sells_per_hour

        candidates.append({
            **entry,
            "buy_price": buy_price,
            "sell_price": sell_price,
            "net_profit": net_profit,
            "net_profit_pct": net_profit_pct,
            "profit_per_hour": profit_per_hour,
            "my_sells_per_hour": my_sells_per_hour,
            "tier": assign_tier(buy_price),
        })

    return candidates


def optimize_portfolio(players: list[dict], budget: int) -> PortfolioSummary:
    """Select players maximizing total profit/hour, then swap-optimize."""
    candidates = _enrich_candidates(players, budget)
    candidates.sort(key=lambda c: c["profit_per_hour"], reverse=True)

    # ── GREEDY FILL ──────────────────────────────────────────────
    selected = []  # list of candidate dicts
    selected_ids = set()
    total_used = 0

    for entry in candidates:
        if len(selected) >= TARGET_PLAYER_COUNT:
            break
        buy_price = entry["buy_price"]
        pid = entry["player"].resource_id
        if pid in selected_ids:
            continue
        if total_used + buy_price > budget:
            continue
        selected.append(entry)
        selected_ids.add(pid)
        total_used += buy_price

    # ── SWAP LOOP ────────────────────────────────────────────────
    # Try removing the most expensive player and replacing with
    # multiple cheaper ones that yield more total profit/hr
    max_swaps = 200  # safety limit
    swaps_done = 0

    while len(selected) < TARGET_PLAYER_COUNT and swaps_done < max_swaps:
        if not selected:
            break

        # Find most expensive player in current selection
        most_expensive_idx = max(range(len(selected)), key=lambda i: selected[i]["buy_price"])
        expensive = selected[most_expensive_idx]
        freed_budget = expensive["buy_price"]
        removed_pph = expensive["profit_per_hour"]

        # Find candidates NOT in the selection that fit in freed budget
        # sorted by profit_per_hour descending
        replacements = []
        replacement_pph = 0
        replacement_cost = 0

        for c in candidates:
            pid = c["player"].resource_id
            if pid in selected_ids and pid != expensive["player"].resource_id:
                continue
            if pid == expensive["player"].resource_id:
                continue
            if replacement_cost + c["buy_price"] <= freed_budget:
                replacements.append(c)
                replacement_pph += c["profit_per_hour"]
                replacement_cost += c["buy_price"]

        # Need at least 2 replacements and more total profit/hr
        if len(replacements) >= 2 and replacement_pph > removed_pph:
            # Do the swap
            selected_ids.discard(expensive["player"].resource_id)
            selected.pop(most_expensive_idx)
            total_used -= freed_budget

            for r in replacements:
                selected.append(r)
                selected_ids.add(r["player"].resource_id)
                total_used += r["buy_price"]

            swaps_done += 1
            logger.info(
                f"Swap #{swaps_done}: removed {expensive['player'].name} "
                f"({freed_budget:,} coins, {removed_pph:.0f}/hr) → "
                f"added {len(replacements)} players "
                f"({replacement_cost:,} coins, {replacement_pph:.0f}/hr)"
            )
        else:
            # Can't improve by swapping this one, stop
            break

    # ── BACKFILL remaining budget ────────────────────────────────
    # After swaps, try to fill any leftover budget
    remaining = budget - total_used
    if remaining > 0 and len(selected) < TARGET_PLAYER_COUNT:
        for c in candidates:
            if len(selected) >= TARGET_PLAYER_COUNT:
                break
            pid = c["player"].resource_id
            if pid in selected_ids:
                continue
            if c["buy_price"] <= remaining:
                selected.append(c)
                selected_ids.add(pid)
                total_used += c["buy_price"]
                remaining -= c["buy_price"]

    # ── Re-sort by profit/hr and build recommendations ───────────
    selected.sort(key=lambda c: c["profit_per_hour"], reverse=True)

    recommendations = []
    for i, entry in enumerate(selected):
        player: Player = entry["player"]
        score: PlayerScore = entry["score"]
        hoss: HOSSResult = entry["hoss"]

        risk_flags = []
        upside_flags = []

        if hoss.confidence < 0.5:
            risk_flags.append("LOW DATA")
        if score.price_stability < 30:
            risk_flags.append("VOLATILE")
        if score.supply < 30:
            risk_flags.append("HIGH SUPPLY")

        if hoss.op_sell_rate > 0.3:
            upside_flags.append("HIGH OP RATE")
        my_sph = entry["my_sells_per_hour"]
        if my_sph >= 0.5:
            upside_flags.append(f"{my_sph:.1f}/hr")
        elif my_sph >= 0.1:
            upside_flags.append(f"{my_sph:.2f}/hr")
        if score.buyer_psychology > 70:
            upside_flags.append("META")

        if hoss.confidence >= 0.8 and score.composite >= 60:
            confidence_tier = ConfidenceTier.HIGH
        elif hoss.confidence >= 0.4 or score.composite >= 40:
            confidence_tier = ConfidenceTier.MEDIUM
        else:
            confidence_tier = ConfidenceTier.LOW

        recommendations.append(Recommendation(
            rank=i + 1,
            player=player,
            current_buy_price=entry["buy_price"],
            recommended_list_price=entry["sell_price"],
            expected_net_profit=entry["net_profit"],
            expected_net_profit_pct=round(entry["net_profit_pct"], 4),
            best_op_margin=hoss.best_op_margin,
            op_sales_per_hour=round(my_sph, 4),
            expected_profit_per_hour=round(entry["profit_per_hour"], 2),
            confidence=confidence_tier,
            hoss_score=score.hoss,
            composite_score=score.composite,
            price_tier=entry["tier"],
            risk_flags=risk_flags,
            upside_flags=upside_flags,
        ))

    total_profit = sum(r.expected_net_profit for r in recommendations)
    total_pph = sum(r.expected_profit_per_hour for r in recommendations)
    total_used = sum(r.current_buy_price for r in recommendations)
    high_count = sum(1 for r in recommendations if r.confidence == ConfidenceTier.HIGH)
    med_count = sum(1 for r in recommendations if r.confidence == ConfidenceTier.MEDIUM)
    low_count = sum(1 for r in recommendations if r.confidence == ConfidenceTier.LOW)

    return PortfolioSummary(
        total_budget=budget,
        total_used=total_used,
        total_expected_profit=total_profit,
        total_profit_per_hour=round(total_pph, 2),
        expected_profit_pct=round(total_profit / total_used, 4) if total_used > 0 else 0,
        player_count=len(recommendations),
        high_confidence_count=high_count,
        medium_confidence_count=med_count,
        low_confidence_count=low_count,
        recommendations=recommendations,
    )
