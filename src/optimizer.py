"""
Portfolio optimizer.

Selects the best players to fill the budget, maximizing total v3 score.

Algorithm:
1. Greedy fill by score — pick the highest scored players first.
2. Drop-and-backfill — if under 80 players, remove the most expensive
   player (ban it permanently), then backfill the freed budget with the
   next-best players. Repeat until >= 80 or no progress.
3. Backfill any remaining budget slack.
4. Upgrade loop — swap the weakest selected player for a stronger
   unselected player when the budget allows, excluding banned players.
"""

from __future__ import annotations

import logging

from src.config import TARGET_PLAYER_COUNT

logger = logging.getLogger(__name__)

_MIN_FILL_COUNT = 80  # trigger drop-and-backfill below this threshold


def optimize_portfolio(
    scored: list[dict],
    budget: int,
    exclude_card_types: list[str] | None = None,
) -> list[dict]:
    """
    Select up to TARGET_PLAYER_COUNT players that fit within budget,
    maximizing total weighted score.

    Args:
        scored: List of scored player dicts.
        budget: Total coin budget.
        exclude_card_types: Card types to exclude (e.g. ["Team of the Week", "Rare"]).

    Returns the selected list sorted by score descending.
    """
    before = len(scored)
    scored = [
        s for s in scored
        if (s.get("expected_profit_per_hour") or 0) > 0
        and (s.get("net_profit") or 0) >= 2000
    ]
    if exclude_card_types:
        exclude_set = set(exclude_card_types)
        scored = [s for s in scored if s.get("card_type") not in exclude_set]
    logger.warning("OPTIMIZER v3: %d -> %d after filters (excl=%s)", before, len(scored), exclude_card_types)

    # Sort by score descending for greedy fill
    scored.sort(key=lambda s: s.get("expected_profit_per_hour") or 0, reverse=True)

    # ── 1. Greedy fill by score ──────────────────────────────────────────────
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
        exp_idx = max(range(len(selected)), key=lambda i: selected[i]["buy_price"])
        expensive = selected[exp_idx]

        freed = expensive["buy_price"]
        used_ids.discard(expensive["player"].resource_id)
        banned_ids.add(expensive["player"].resource_id)
        selected.pop(exp_idx)
        total_used -= freed
        drops += 1

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

        worst_idx = min(range(len(selected)), key=lambda i: selected[i].get("expected_profit_per_hour") or 0)
        worst = selected[worst_idx]
        worst_score = worst.get("expected_profit_per_hour") or 0

        affordable = worst["buy_price"] + remaining
        best_upgrade = None
        for s in scored:
            pid = s["player"].resource_id
            if pid in used_ids or pid in banned_ids:
                continue
            if (s.get("expected_profit_per_hour") or 0) <= worst_score:
                break
            if s["buy_price"] <= affordable:
                best_upgrade = s
                break

        if best_upgrade is None:
            break

        used_ids.discard(worst["player"].resource_id)
        total_used -= worst["buy_price"]
        selected.pop(worst_idx)

        selected.append(best_upgrade)
        used_ids.add(best_upgrade["player"].resource_id)
        total_used += best_upgrade["buy_price"]
        upgrades += 1

    if upgrades:
        logger.info("Upgrade loop: %d upgrades applied", upgrades)

    selected.sort(key=lambda s: s.get("expected_profit_per_hour") or 0, reverse=True)

    return selected
