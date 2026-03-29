"""
Portfolio optimizer.

Selects the best players to fill the budget, maximizing total expected
profit per hour.

Algorithm:
1. Greedy fill by raw EPPH — pick the highest earners first.
2. Drop-and-backfill — if under 80 players, remove the most expensive
   player (ban it permanently), then backfill the freed budget with the
   next-best EPPH players. Repeat until >= 80 or no progress.
3. Backfill any remaining budget slack.
4. Upgrade loop — swap the weakest selected player for a stronger
   unselected player when the budget allows, excluding banned players.
"""

from __future__ import annotations

import logging

from src.config import TARGET_PLAYER_COUNT

logger = logging.getLogger(__name__)

_MIN_FILL_COUNT = 80  # trigger drop-and-backfill below this threshold


def optimize_portfolio(scored: list[dict], budget: int) -> list[dict]:
    """
    Select up to TARGET_PLAYER_COUNT players that fit within budget,
    maximizing total expected profit per hour.

    Returns the selected list sorted by EPPH descending.
    """
    # Filter out players with no expected profit or OP sell rate below 4%
    before = len(scored)
    scored = [
        s for s in scored
        if (s.get("expected_profit_per_hour") or 0) > 0
        and (s.get("op_ratio") or 0) >= 0.03
    ]
    logger.warning("OPTIMIZER v2: %d -> %d after EPPH + op_sell_rate filter", before, len(scored))

    # Compute ranking values
    for s in scored:
        epph = s.get("expected_profit_per_hour") or 0
        s["efficiency"] = epph / s["buy_price"] if s["buy_price"] > 0 else 0
        s["_ranking_profit"] = epph

    # Sort by EPPH descending for greedy fill
    scored.sort(key=lambda s: s["_ranking_profit"], reverse=True)

    # ── 1. Greedy fill by EPPH ───────────────────────────────────────────────
    selected: list[dict] = []
    total_used = 0
    used_ids: set[int] = set()
    banned_ids: set[int] = set()

    for entry in scored:
        if len(selected) >= TARGET_PLAYER_COUNT:
            break
        pid = entry["player"].resource_id
        if pid in used_ids:
            continue
        cost = entry["buy_price"]
        if total_used + cost > budget:
            continue
        selected.append(entry)
        used_ids.add(pid)
        total_used += cost

    logger.info("Greedy fill: %d players, %d/%d budget used", len(selected), total_used, budget)

    # ── 2. Drop-and-backfill: remove expensive players to free budget ────────
    drops = 0
    while len(selected) < _MIN_FILL_COUNT and selected:
        # Find most expensive selected player
        exp_idx = max(range(len(selected)), key=lambda i: selected[i]["buy_price"])
        expensive = selected[exp_idx]

        # Remove and ban
        freed = expensive["buy_price"]
        used_ids.discard(expensive["player"].resource_id)
        banned_ids.add(expensive["player"].resource_id)
        selected.pop(exp_idx)
        total_used -= freed
        drops += 1

        # Backfill freed budget with best available EPPH players
        remaining = budget - total_used
        added = 0
        for s in scored:
            if len(selected) >= TARGET_PLAYER_COUNT:
                break
            pid = s["player"].resource_id
            if pid in used_ids or pid in banned_ids:
                continue
            if s["buy_price"] <= remaining:
                selected.append(s)
                used_ids.add(pid)
                total_used += s["buy_price"]
                remaining -= s["buy_price"]
                added += 1

        # No new players fit — stop to avoid stripping the portfolio empty
        if added == 0:
            break

    if drops:
        logger.info("Drop-and-backfill: dropped %d expensive players, now %d players, %d/%d budget",
                     drops, len(selected), total_used, budget)

    # ── 3. Backfill remaining budget ─────────────────────────────────────────
    remaining = budget - total_used
    for s in scored:
        if len(selected) >= TARGET_PLAYER_COUNT:
            break
        pid = s["player"].resource_id
        if pid in used_ids or pid in banned_ids:
            continue
        if s["buy_price"] <= remaining:
            selected.append(s)
            used_ids.add(pid)
            total_used += s["buy_price"]
            remaining -= s["buy_price"]

    # ── 4. Upgrade loop: swap weakest for stronger unselected player ─────────
    upgrades = 0
    while upgrades < 200:
        if not selected:
            break
        remaining = budget - total_used

        # Find worst selected player by EPPH
        worst_idx = min(range(len(selected)), key=lambda i: selected[i]["_ranking_profit"])
        worst = selected[worst_idx]
        worst_epph = worst["_ranking_profit"]

        # Find best unselected player with higher EPPH that fits
        affordable = worst["buy_price"] + remaining
        best_upgrade = None
        for s in scored:
            pid = s["player"].resource_id
            if pid in used_ids or pid in banned_ids:
                continue
            if s["_ranking_profit"] <= worst_epph:
                break  # sorted by EPPH desc, no better candidates
            if s["buy_price"] <= affordable:
                best_upgrade = s
                break

        if best_upgrade is None:
            break

        # Swap: remove worst, add upgrade
        used_ids.discard(worst["player"].resource_id)
        total_used -= worst["buy_price"]
        selected.pop(worst_idx)

        selected.append(best_upgrade)
        used_ids.add(best_upgrade["player"].resource_id)
        total_used += best_upgrade["buy_price"]
        upgrades += 1

    if upgrades:
        logger.info("Upgrade loop: %d upgrades applied", upgrades)

    # Sort final output by EPPH descending
    selected.sort(key=lambda s: s.get("expected_profit_per_hour") or 0, reverse=True)

    # Remove internal key before returning
    for s in selected:
        s.pop("_ranking_profit", None)

    return selected
