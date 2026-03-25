"""
Portfolio optimizer.

Selects the best players to fill the budget, maximizing total expected
profit. Uses efficiency sorting to favor cheaper cards with decent OP
ratios over expensive cards with tiny OP ratios.

Ranking metric:
  - v2 players (expected_profit_per_hour present): efficiency = epph / buy_price
  - v1 players (no expected_profit_per_hour): efficiency = expected_profit / buy_price

Swap loop replaces expensive cards with multiple cheaper alternatives when
profitable. All comparisons use the same ranking metric as initial sort.
"""

from __future__ import annotations

from src.config import TARGET_PLAYER_COUNT


def optimize_portfolio(scored: list[dict], budget: int) -> list[dict]:
    """
    Select up to TARGET_PLAYER_COUNT players that fit within budget,
    maximizing total expected profit.

    When a player has `expected_profit_per_hour` (v2 scorer), that value is
    used as the ranking profit for efficiency computation and swap decisions.
    Players without it fall back to v1's `expected_profit`.

    Returns the selected list sorted by ranking profit descending.
    """
    # Compute efficiency and _ranking_profit based on scorer version
    for s in scored:
        epph = s.get("expected_profit_per_hour")
        if epph is not None and epph > 0:
            # v2 path: rank by expected profit per hour
            s["efficiency"] = epph / s["buy_price"] if s["buy_price"] > 0 else 0
            s["_ranking_profit"] = epph
        else:
            # v1 path: rank by expected profit
            s["efficiency"] = s["expected_profit"] / s["buy_price"] if s["buy_price"] > 0 else 0
            s["_ranking_profit"] = s["expected_profit"]

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
    # if they collectively produce more ranking profit
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

    # Sort final output by ranking profit descending, then strip internal key.
    # Use efficiency (already normalised by buy_price) so v2 players with high
    # expected_profit_per_hour rank correctly relative to v1 players.
    selected.sort(key=lambda s: s["efficiency"], reverse=True)

    # Remove internal key before returning
    for s in selected:
        s.pop("_ranking_profit", None)

    return selected
