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
  moveItem,
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
 * Searches by ea_id and finds the lowest buyNowPrice in results.
 */
async function discoverLowestBin(
  ea_id: number,
  fallbackPrice: number,
): Promise<number> {
  const criteria = buildCriteria(ea_id, MAX_PRICE);
  const result = await searchMarket(criteria);

  if (!result.success || result.items.length === 0) {
    return fallbackPrice;
  }

  let cheapest = Infinity;
  for (const item of result.items) {
    const bin = item.getAuctionData().buyNowPrice;
    if (bin < cheapest) {
      cheapest = bin;
    }
  }

  return cheapest < Infinity ? cheapest : fallbackPrice;
}

// ── Main Export ──────────────────────────────────────────────────────────────

/**
 * Execute the algo sell cycle for a single signal.
 *
 * @param signal       AlgoSignal with action='SELL' from the backend
 * @param sendMessage  Callback to send messages to the service worker
 */
export async function executeAlgoSellCycle(
  signal: AlgoSignal,
  sendMessage: (msg: any) => Promise<any>,
): Promise<AlgoSellCycleResult> {
  // Step 1: Fetch unassigned pile
  const { items, success: unassignedSuccess } = await getUnassigned();
  if (!unassignedSuccess) {
    return { outcome: 'error', reason: 'Failed to fetch unassigned pile' };
  }
  if (items.length === 0) {
    return { outcome: 'skipped', reason: 'No items in unassigned pile' };
  }

  // Step 2: Find matching card
  const card = findMatchingItem(items, signal);
  if (!card) {
    return {
      outcome: 'skipped',
      reason: `Card not found in unassigned: ${signal.player_name} ${signal.rating}`,
    };
  }

  // Step 3: Discover cheapest BIN via market search
  await jitter(1000, 2000);
  const discoveredPrice = await discoverLowestBin(signal.ea_id, signal.reference_price);
  const listBin = roundToNearestStep(discoveredPrice);
  const listStart = roundToNearestStep(getBeforeStepValue(discoveredPrice));

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
