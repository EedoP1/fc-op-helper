"""
Portfolio optimizer.

Selects the best players to fill the budget, maximizing total expected
profit. Uses efficiency sorting (expected_profit / buy_price) to favor
cheaper cards with decent OP ratios. Swap loop replaces expensive cards
with multiple cheaper alternatives when profitable.
"""

from __future__ import annotations

from src.config import TARGET_PLAYER_COUNT


def optimize_portfolio(scored: list[dict], budget: int) -> list[dict]:
    """
    Select up to TARGET_PLAYER_COUNT players that fit within budget,
    maximizing total expected profit.

    Returns the selected list sorted by expected profit descending.
    """
    # Add efficiency metric
    for s in scored:
        s["efficiency"] = s["expected_profit"] / s["buy_price"] if s["buy_price"] > 0 else 0

    # Greedy fill by efficiency
    scored.sort(key=lambda s: s["efficiency"], reverse=True)

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
    # if they collectively produce more expected profit
    swaps = 0
    while len(selected) < TARGET_PLAYER_COUNT and swaps < 100:
        if not selected:
            break

        exp_idx = max(range(len(selected)), key=lambda i: selected[i]["buy_price"])
        expensive = selected[exp_idx]
        freed = expensive["buy_price"]

        replacements = []
        repl_ep = 0
        repl_cost = 0
        temp_used = {s["player"].resource_id for s in selected} - {expensive["player"].resource_id}

        for s in scored:
            pid = s["player"].resource_id
            if pid in temp_used:
                continue
            if repl_cost + s["buy_price"] <= freed:
                replacements.append(s)
                repl_ep += s["expected_profit"]
                repl_cost += s["buy_price"]
                temp_used.add(pid)

        if len(replacements) >= 2 and repl_ep > expensive["expected_profit"]:
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

    # Backfill remaining budget
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

    selected.sort(key=lambda s: s["expected_profit"], reverse=True)
    return selected
