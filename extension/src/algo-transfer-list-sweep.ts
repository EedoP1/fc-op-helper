/**
 * Algo transfer list sweep — scan the TL for sold/expired algo cards.
 *
 * Called by the algo automation loop at the start of each iteration.
 * Uses EA service layer (getTransferList, searchMarket, listItem, clearSold).
 *
 * No DOM interaction — all operations use EA's internal service APIs.
 *
 *   - Sold items matching algo positions -> report to backend via ALGO_POSITION_SOLD
 *   - Expired items matching algo positions -> discover current lowest BIN, relist individually
 *   - Clear sold items from the TL
 *
 * Does NOT use relistAll() (which relists at original locked price).
 * Instead, individually relists each expired card at current lowest BIN.
 */
import {
  getTransferList,
  searchMarket,
  buildCriteria,
  listItem,
  clearSold,
  refreshAuctions,
  roundToNearestStep,
  getBeforeStepValue,
  MAX_PRICE,
  type EAItem,
} from './ea-services';
import { jitter } from './automation';
import type { ExtensionMessage } from './messages';

// ── Constants ────────────────────────────────────────────────────────────────

const RATE_LIMIT_ERROR_CODE = 460;

// ── Types ─────────────────────────────────────────────────────────────────────

export type AlgoSweepResult = {
  soldCount: number;
  relistedCount: number;
  clearedCount: number;
};

type PositionMatch = {
  ea_id: number;
  player_name: string;
  quantity: number;
  buy_price: number;
  listed_price: number | null;
};

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Match an EA item to an algo position by definitionId.
 * Falls back to name + rating match.
 */
function matchItemToPosition(
  item: EAItem,
  positions: PositionMatch[],
): PositionMatch | null {
  // Primary: match by definitionId
  for (const pos of positions) {
    if (item.definitionId === pos.ea_id) {
      return pos;
    }
  }

  // Fallback: name match
  const itemName = item._staticData.name.toLowerCase();
  for (const pos of positions) {
    const posName = pos.player_name.toLowerCase();
    if (posName.includes(itemName) || itemName.includes(posName)) {
      return pos;
    }
  }
  return null;
}

/**
 * Discover current lowest BIN for a player via transfer market search.
 * Narrows down through price tiers to find the true floor.
 */
async function discoverLowestBin(
  ea_id: number,
  fallbackPrice: number,
): Promise<number> {
  const MAX_NARROW_STEPS = 6;
  const EA_PAGE_SIZE = 20;
  let currentMax = MAX_PRICE;
  let lastCheapest = fallbackPrice;

  for (let step = 0; step < MAX_NARROW_STEPS; step++) {
    const criteria = buildCriteria(ea_id, currentMax);
    if (step > 0) await jitter(1000, 2000);
    const result = await searchMarket(criteria);

    if (!result.success) {
      if (result.error === RATE_LIMIT_ERROR_CODE) {
        await jitter(4000, 8000);
        step--;
        continue;
      }
      return lastCheapest;
    }

    if (result.items.length === 0) {
      return lastCheapest;
    }

    let lowestBin = Infinity;
    for (const item of result.items) {
      const bin = item.getAuctionData().buyNowPrice;
      if (bin < lowestBin) lowestBin = bin;
    }
    lastCheapest = lowestBin;

    if (result.items.length < EA_PAGE_SIZE) {
      return lowestBin;
    }

    if (currentMax === lowestBin) {
      const below = getBeforeStepValue(lowestBin);
      if (below <= 0) return lowestBin;
      currentMax = below;
    } else {
      currentMax = lowestBin;
    }
  }

  return lastCheapest;
}

// ── Main Export ──────────────────────────────────────────────────────────────

/**
 * Run the full algo transfer list sweep.
 *
 * @param sendMessage  Callback to relay messages to the service worker
 * @param positions    Current algo positions from /algo/status (with listed_price set)
 * @param stopped      Callback to check if automation was stopped
 * @returns Sweep result with counts
 */
export async function runAlgoTransferListSweep(
  sendMessage: (msg: any) => Promise<any>,
  positions: PositionMatch[],
  stopped: () => boolean,
): Promise<AlgoSweepResult> {
  const result: AlgoSweepResult = { soldCount: 0, relistedCount: 0, clearedCount: 0 };

  // Step 1: Fetch transfer list via EA services
  let { groups, success } = await getTransferList();
  if (!success) {
    console.warn('[algo-tl-sweep] Failed to fetch transfer list');
    return result;
  }

  // Refresh auction data for accurate sold/expired detection
  if (groups.all.length > 0) {
    await jitter(1000, 2000);
    await refreshAuctions(groups.all);
    await jitter(1000, 2000);
    ({ groups } = await getTransferList());
  }

  if (stopped()) return result;

  // Step 2: Match sold items to algo positions and report
  const soldByPosition = new Map<number, { count: number; price: number }>();
  for (const item of groups.sold) {
    const match = matchItemToPosition(item, positions);
    if (!match) continue;
    const price = item.getAuctionData().buyNowPrice;
    const existing = soldByPosition.get(match.ea_id) ?? { count: 0, price };
    existing.count += 1;
    soldByPosition.set(match.ea_id, existing);
  }

  for (const [ea_id, { count, price }] of soldByPosition) {
    if (stopped()) return result;
    try {
      await sendMessage({
        type: 'ALGO_POSITION_SOLD',
        ea_id,
        sell_price: price,
        quantity: count,
      } satisfies ExtensionMessage);
      result.soldCount += count;
    } catch (err) {
      console.warn(`[algo-tl-sweep] ALGO_POSITION_SOLD failed for ea_id=${ea_id}:`, err);
    }
  }

  if (stopped()) return result;

  // Step 3: Match expired items to algo positions and relist at current lowest BIN
  const expiredByPosition = new Map<number, { items: EAItem[]; match: PositionMatch }>();
  for (const item of groups.expired) {
    const match = matchItemToPosition(item, positions);
    if (!match) continue;
    const existing = expiredByPosition.get(match.ea_id);
    if (!existing) {
      expiredByPosition.set(match.ea_id, { items: [item], match });
    } else {
      existing.items.push(item);
    }
  }

  for (const [ea_id, { items: expiredItems, match }] of expiredByPosition) {
    if (stopped()) return result;

    // Discover current lowest BIN for this player
    const fallback = match.listed_price ?? match.buy_price;
    await jitter(1000, 2000);
    const lowestBin = await discoverLowestBin(ea_id, fallback);

    if (stopped()) return result;

    const listBin = roundToNearestStep(getBeforeStepValue(lowestBin));
    const listStart = roundToNearestStep(getBeforeStepValue(listBin));

    // Relist each expired card individually at current market price
    for (const expItem of expiredItems) {
      if (stopped()) return result;

      await jitter(1000, 2000);
      const listResult = await listItem(expItem, listStart, listBin);

      if (listResult.success) {
        result.relistedCount += 1;
      } else {
        console.warn(`[algo-tl-sweep] Relist failed for defId=${expItem.definitionId} (error ${listResult.error})`);
      }
    }

    // Report relist to backend
    if (result.relistedCount > 0) {
      try {
        await sendMessage({
          type: 'ALGO_POSITION_RELIST',
          ea_id,
          price: listBin,
          quantity: expiredItems.length,
        } satisfies ExtensionMessage);
      } catch (err) {
        console.warn(`[algo-tl-sweep] ALGO_POSITION_RELIST failed for ea_id=${ea_id}:`, err);
      }
    }
  }

  // Step 4: Clear sold items from the TL
  if (soldByPosition.size > 0) {
    await jitter(1000, 2000);
    await clearSold();
    result.clearedCount = result.soldCount;
  }

  return result;
}
