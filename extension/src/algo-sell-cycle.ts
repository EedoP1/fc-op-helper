/**
 * Algo sell cycle: find a card in the unassigned pile, discover the cheapest
 * BIN on the transfer market, then list the card at that price.
 *
 * No DOM interaction — uses EA's internal service layer exclusively.
 *
 * Steps:
 *   1. Fetch unassigned pile via getUnassigned()
 *   2. Find matching card by definitionId (ea_id)
 *   3. Search market to discover cheapest BIN
 *   4. List the card at discovered price via listItem()
 */
import {
  buildCriteria,
  searchMarket,
  listItem,
  getUnassigned,
  freeUnassignedSlots,
  moveItem,
  requestItemsById,
  roundToNearestStep,
  getBeforeStepValue,
  MAX_PRICE,
  type EAItem,
} from './ea-services';
import { jitter } from './automation';
import type { AlgoSignal } from './messages';

// ── Types ─────────────────────────────────────────────────────────────────────

export type AlgoSellCycleResult =
  | { outcome: 'listed'; sellPrice: number; quantity: number }
  | { outcome: 'skipped'; reason: string }
  | { outcome: 'error'; reason: string };

// ── Constants ─────────────────────────────────────────────────────────────────

const RATE_LIMIT_ERROR_CODE = 460;
const MAX_RATE_LIMIT_RETRIES = 3;

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Find a matching item in the unassigned pile by definitionId (ea_id).
 * Falls back to name + rating match if definitionId doesn't match.
 */
function findMatchingItem(items: EAItem[], signal: AlgoSignal): EAItem | null {
  // Primary match: definitionId === ea_id
  const byDefId = items.find(item => item.definitionId === signal.ea_id);
  if (byDefId) return byDefId;

  // Fallback: name + rating match
  const signalNameLower = signal.player_name.toLowerCase();
  for (const item of items) {
    const itemName = item._staticData.name.toLowerCase();
    if (item.rating === signal.rating &&
        (signalNameLower.includes(itemName) || itemName.includes(signalNameLower))) {
      return item;
    }
  }
  return null;
}

/**
 * Discover the cheapest BIN for a player on the transfer market.
 * Narrows down through price tiers (like the buy cycle) to find the
 * true floor price, not just the cheapest in the first 20 results.
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

    // Less than a full page — we've found the floor
    if (result.items.length < EA_PAGE_SIZE) {
      return lowestBin;
    }

    // Narrow: if lowest is same as current max, step below to check for cheaper
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
 * Execute the algo sell cycle for a single signal.
 *
 * @param signal       AlgoSignal with action='SELL' from the backend
 * @param sendMessage  Callback to send messages to the service worker
 * @param ea_item_id   Optional EA instance ID — when provided, fetches the card
 *                     directly via requestItemsById instead of searching the
 *                     unassigned pile (which caps at 50 items).
 */
export async function executeAlgoSellCycle(
  signal: AlgoSignal,
  sendMessage: (msg: any) => Promise<any>,
  ea_item_id?: number,
): Promise<AlgoSellCycleResult> {
  // Step 1: Find matching card
  let card: EAItem | null = null;

  // Fast path: fetch by EA instance ID (skips unassigned pile entirely)
  if (ea_item_id) {
    console.log(`[algo-sell] Fetching card by ea_item_id=${ea_item_id}`);
    const { items, success } = await requestItemsById([ea_item_id]);
    if (success && items.length > 0) {
      card = items[0];
      console.log(`[algo-sell] Found card by ea_item_id, defId=${card.definitionId}`);
    } else {
      console.log(`[algo-sell] requestItemsById failed or empty, falling back to unassigned search`);
    }
  }

  // Slow path: search unassigned pile
  if (!card) {
    const { items, success } = await getUnassigned();
    if (!success) {
      return { outcome: 'error', reason: 'Failed to fetch unassigned pile' };
    }
    card = findMatchingItem(items, signal);
  }

  // Slowest path: if pile > 50 and card not found, clear dupes in batches
  // to shrink the pile and reveal the card (EA caps unassigned at 50 per fetch)
  if (!card) {
    const MAX_CLEAR_ROUNDS = 5;
    for (let round = 0; round < MAX_CLEAR_ROUNDS; round++) {
      console.log(`[algo-sell] Card not in first 50, clearing dupes round ${round + 1}...`);
      const freed = await freeUnassignedSlots();
      if (freed === 0) {
        console.log(`[algo-sell] No more dupes to clear`);
        break;
      }
      console.log(`[algo-sell] Freed ${freed} slots, refetching...`);
      await jitter(500, 1000);
      const { items: refreshed } = await getUnassigned();
      card = findMatchingItem(refreshed, signal);
      if (card) {
        console.log(`[algo-sell] Found card after ${round + 1} clearing rounds`);
        break;
      }
    }
  }

  if (!card) {
    return {
      outcome: 'skipped',
      reason: `Card not found in unassigned: ${signal.player_name} ${signal.rating}`,
    };
  }

  // Step 3: Discover cheapest BIN via market search, undercut by 1 step
  await jitter(1000, 2000);
  const discoveredPrice = await discoverLowestBin(signal.ea_id, signal.reference_price);
  const listBin = roundToNearestStep(getBeforeStepValue(discoveredPrice));
  const listStart = roundToNearestStep(getBeforeStepValue(listBin));

  // Step 4: List the card
  await jitter(1000, 2000);
  let listResult = await listItem(card, listStart, listBin);

  // If listing from unassigned failed, try moving to TL first then listing
  if (!listResult.success) {
    if (listResult.error === RATE_LIMIT_ERROR_CODE) {
      await jitter(4000, 8000);
      listResult = await listItem(card, listStart, listBin);
    } else {
      // Move to transfer list pile and retry
      await moveItem(card, 5); // 5 = ItemPile.TRANSFER
      await jitter(500, 1000);
      listResult = await listItem(card, listStart, listBin);
    }
  }

  if (!listResult.success) {
    return {
      outcome: 'error',
      reason: `Listing failed (error ${listResult.error}) — will retry next cycle`,
    };
  }

  return { outcome: 'listed', sellPrice: listBin, quantity: 1 };
}
