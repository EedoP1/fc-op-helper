"""
Portfolio optimizer.

Selects the best players to fill the budget, maximizing total expected
profit per hour. Ranks by raw EPPH (not efficiency) so the highest-
earning players are picked first regardless of price.

Swap loop replaces expensive cards with multiple cheaper alternatives when
their combined EPPH exceeds the expensive card's EPPH.
"""

from __future__ import annotations

from src.config import TARGET_PLAYER_COUNT


def optimize_portfolio(scored: list[dict], budget: int) -> list[dict]:
    """
    Select up to TARGET_PLAYER_COUNT players that fit within budget,
    maximizing total expected profit per hour.

    Ranking: expected_profit_per_hour (descending).

    Returns the selected list sorted by EPPH descending.
    """
    # Filter out players with no expected profit (stale v1 scores, zero-activity cards)
    before = len(scored)
    scored = [s for s in scored if (s.get("expected_profit_per_hour") or 0) > 0]
    import logging
    logging.getLogger(__name__).warning("OPTIMIZER v2: %d -> %d after EPPH filter", before, len(scored))

    # Compute ranking values from expected_profit_per_hour
    for s in scored:
        epph = s.get("expected_profit_per_hour") or 0
        s["efficiency"] = epph / s["buy_price"] if s["buy_price"] > 0 else 0
        s["_ranking_profit"] = epph

    # Greedy fill by EPPH (best earners first)
    scored.sort(key=lambda s: s["_ranking_profit"], reverse=True)

    selected = []
    total_used = 0
    used_ids: set[int] = set()

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

    # Swap loop: replace the most expensive card with cheaper alternatives
    # if they collectively produce more EPPH
    swaps = 0
    while len(selected) < TARGET_PLAYER_COUNT and swaps < 100:
        if not selected:
            break

        exp_idx = max(range(len(selected)), key=lambda i: selected[i]["buy_price"])
        expensive = selected[exp_idx]
        freed = expensive["buy_price"]

        replacements = []
        repl_rp = 0
        repl_cost = 0
        temp_used = {s["player"].resource_id for s in selected} - {expensive["player"].resource_id}

        for s in scored:
            pid = s["player"].resource_id
            if pid in temp_used:
                continue
            if repl_cost + s["buy_price"] <= freed:
                replacements.append(s)
                repl_rp += s["_ranking_profit"]
                repl_cost += s["buy_price"]
                temp_used.add(pid)

        if len(replacements) >= 2 and repl_rp > expensive["_ranking_profit"]:
            used_ids.discard(expensive["player"].resource_id)
            selected.pop(exp_idx)
            total_used -= freed
            for r in replacements:
                selected.append(r)
                used_ids.add(r["player"].resource_id)
                total_used += r["buy_price"]
            swaps += 1
        else:
            break

    # Backfill remaining budget (by EPPH — scored is already sorted)
    remaining = budget - total_used
    for s in scored:
        if len(selected) >= TARGET_PLAYER_COUNT:
            break
        pid = s["player"].resource_id
        if pid in used_ids:
            continue
        if s["buy_price"] <= remaining:
            selected.append(s)
            used_ids.add(pid)
            total_used += s["buy_price"]
            remaining -= s["buy_price"]

    # Upgrade loop: replace the worst-EPPH selected player with a better
    # unselected player if the budget allows (swap cheap weak → expensive strong).
    # This uses leftover budget to upgrade quality.
    upgrades = 0
    while upgrades < 200:
        if not selected:
            break
        remaining = budget - total_used

        # Find worst selected player by EPPH
        worst_idx = min(range(len(selected)), key=lambda i: selected[i]["_ranking_profit"])
        worst = selected[worst_idx]
        worst_epph = worst["_ranking_profit"]

        # Find best unselected player with higher EPPH that fits in budget
        # (can use worst's freed budget + remaining)
        affordable = worst["buy_price"] + remaining
        best_upgrade = None
        for s in scored:
            pid = s["player"].resource_id
            if pid in used_ids:
                continue
            if s["_ranking_profit"] <= worst_epph:
                break  # scored is sorted by EPPH desc, no better candidates
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

    # Sort final output by EPPH descending
    selected.sort(key=lambda s: s.get("expected_profit_per_hour") or 0, reverse=True)

    # Remove internal key before returning
    for s in selected:
        s.pop("_ranking_profit", None)

    return selected
