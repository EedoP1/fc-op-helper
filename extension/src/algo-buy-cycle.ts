/**
 * Algo buy cycle: search for a player from an algo signal, buy at or below
 * the reference price guard — all via EA's internal services.Item APIs.
 *
 * Unlike the OP sell buy-cycle, this does NOT list the card after buying.
 * The card stays in the unassigned pile until a SELL signal triggers listing.
 *
 * Key differences from buy-cycle.ts:
 *   - Uses signal's reference_price for price guard (10% tolerance)
 *   - Searches by signal.ea_id via buildCriteria (not player name)
 *   - Skips listing step — card stays unassigned after purchase
 *   - No DOM interaction — uses EA service layer exclusively
 */
import {
  buildCriteria,
  searchMarket,
  buyItem,
  getBeforeStepValue,
  canPerformUnassignedGlitch,
  performUnassignedGlitch,
  DESTINATION_FULL_ERROR_CODE,
  type EAItem,
} from './ea-services';
import { jitter } from './automation';
import type { AlgoSignal } from './messages';

// ── Types ─────────────────────────────────────────────────────────────────────

export type AlgoBuyCycleResult =
  | { outcome: 'bought'; buyPrice: number; quantity: number }
  | { outcome: 'skipped'; reason: string }
  | { outcome: 'error'; reason: string };

// ── Constants ─────────────────────────────────────────────────────────────────

const PRICE_GUARD_MULTIPLIER = 1.10;
const MAX_RETRIES = 3;
const SNIPE_ERROR_CODE = 461;
const RATE_LIMIT_ERROR_CODE = 460;
const EA_PAGE_SIZE = 20;
const MAX_NARROW_STEPS = 10;

// ── Price Narrowing ──────────────────────────────────────────────────────────

/**
 * Narrow the search price until we find the cheapest available items.
 *
 * Algorithm (mirrors buy-cycle.ts narrowToFloor):
 *   1. Search at maxBuy
 *   2. If results >= EA_PAGE_SIZE, set maxBuy to lowest BIN found and repeat
 *   3. If 0 results, return the previous batch (we overshot)
 *   4. Stop when results < EA_PAGE_SIZE AND all items share one price
 */
async function narrowToFloor(
  ea_id: number,
  maxBuy: number,
): Promise<{ items: EAItem[]; error?: number }> {
  let currentMax = maxBuy;
  let lastItems: EAItem[] = [];

  for (let step = 0; step < MAX_NARROW_STEPS; step++) {
    const minBid = Math.floor(Math.random() * 151);
    const criteria = buildCriteria(ea_id, currentMax, minBid);

    if (step > 0) await jitter(1000, 2000);
    const result = await searchMarket(criteria);

    if (!result.success) {
      if (result.error === RATE_LIMIT_ERROR_CODE) {
        await jitter(4000, 8000);
        step--;
        continue;
      }
      return { items: lastItems, error: result.error };
    }

    if (result.items.length === 0) {
      return { items: lastItems };
    }

    lastItems = result.items;

    // Find price range in results
    let lowestBin = Infinity;
    let highestBin = 0;
    for (const item of result.items) {
      const bin = item.getAuctionData().buyNowPrice;
      if (bin < lowestBin) lowestBin = bin;
      if (bin > highestBin) highestBin = bin;
    }

    // Done: less than a full page AND only one price
    if (result.items.length < EA_PAGE_SIZE && lowestBin === highestBin) {
      return { items: result.items };
    }

    // All results at same price — step below to check for cheaper items
    if (currentMax === lowestBin) {
      const below = getBeforeStepValue(lowestBin);
      if (below <= 0) return { items: result.items };
      currentMax = below;
    } else {
      currentMax = lowestBin;
    }
  }

  return { items: lastItems };
}

// ── Main Entry Point ──────────────────────────────────────────────────────────

/**
 * Execute the algo buy cycle for a single signal.
 *
 * Steps:
 *   1. Narrow to the cheapest price tier via narrowToFloor
 *   2. Price guard check against signal.reference_price * 1.10
 *   3. Buy the cheapest card (retry on snipe with fresh search)
 *   4. Card stays in unassigned pile — no listing
 *
 * @param signal       AlgoSignal from the backend
 * @param sendMessage  Callback to send messages to the service worker
 */
export async function executeAlgoBuyCycle(
  signal: AlgoSignal,
  sendMessage: (msg: any) => Promise<any>,
): Promise<AlgoBuyCycleResult> {
  const priceGuard = Math.floor(signal.reference_price * PRICE_GUARD_MULTIPLIER);

  // Step 1: Narrow to the cheapest price tier
  const { items: floorItems, error: searchError } = await narrowToFloor(
    signal.ea_id,
    signal.reference_price,
  );

  if (searchError) {
    return { outcome: 'error', reason: `Search failed (error ${searchError})` };
  }
  if (floorItems.length === 0) {
    return { outcome: 'skipped', reason: 'No items found' };
  }

  const floorPrice = Math.min(...floorItems.map(i => i.getAuctionData().buyNowPrice));

  // Step 2: Price guard
  if (floorPrice > priceGuard) {
    return { outcome: 'skipped', reason: 'Price above guard' };
  }

  // Step 3: Buy the cheapest (retry on snipe with fresh search at floor price)
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    let items = floorItems;
    if (attempt > 0) {
      await jitter(1000, 2000);
      const minBid = Math.floor(Math.random() * 151);
      const criteria = buildCriteria(signal.ea_id, floorPrice, minBid);
      const result = await searchMarket(criteria);
      if (!result.success || result.items.length === 0) continue;
      items = result.items;
    }

    // Pick cheapest
    let cheapest = items[0];
    let cheapestBin = cheapest.getAuctionData().buyNowPrice;
    for (let i = 1; i < items.length; i++) {
      const bin = items[i].getAuctionData().buyNowPrice;
      if (bin < cheapestBin) {
        cheapest = items[i];
        cheapestBin = bin;
      }
    }

    await jitter(1000, 2000);

    // Buy
    const buyResult = await buyItem(cheapest, cheapestBin);
    if (!buyResult.success) {
      if (buyResult.error === SNIPE_ERROR_CODE) continue;
      if (buyResult.error === RATE_LIMIT_ERROR_CODE) {
        await jitter(4000, 8000);
        attempt--; // don't count rate limit as a retry
        continue;
      }
      if (buyResult.error === DESTINATION_FULL_ERROR_CODE) {
        // Unassigned pile full — attempt glitch to free slots
        if (canPerformUnassignedGlitch()) {
          console.log('[algo-buy] Unassigned pile full, performing glitch...');
          const freed = await performUnassignedGlitch();
          console.log(`[algo-buy] Glitch freed ${freed} slots`);
          if (freed > 0) {
            attempt--; // don't count as a retry, we freed space
            continue;
          }
        }
        return { outcome: 'error', reason: 'Unassigned pile full (no duplicates to swap)' };
      }
      return { outcome: 'error', reason: `Buy failed (error ${buyResult.error})` };
    }

    // Card stays in unassigned — no listing for algo buys
    return { outcome: 'bought', buyPrice: cheapestBin, quantity: 1 };
  }

  return { outcome: 'skipped', reason: `Sniped ${MAX_RETRIES} times` };
}
