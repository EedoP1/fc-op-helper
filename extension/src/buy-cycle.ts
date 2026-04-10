/**
 * Buy cycle: search for a player, buy at or below target price, and list
 * at locked OP sell price — all via EA's internal services.Item APIs.
 *
 * Error handling follows FUT Enhancer's pattern: every EA call returns
 * {success, error} — callers check these per-call, no try/catch for flow control.
 */
import {
  buildCriteria,
  searchMarket,
  buyItem,
  listItem,
  moveItem,
  roundToNearestStep,
  getBeforeStepValue,
  type EAItem,
} from './ea-services';
import { jitter } from './automation';
import type { ActionNeeded } from './messages';

// ── Types ─────────────────────────────────────────────────────────────────────

export type BuyCycleResult =
  | { outcome: 'bought'; buyPrice: number }
  | { outcome: 'skipped'; reason: string }
  | { outcome: 'error'; reason: string };

// ── Constants ─────────────────────────────────────────────────────────────────

const PRICE_GUARD_MULTIPLIER = 1.05;
const MAX_RETRIES = 3;
const SNIPE_ERROR_CODE = 461;
const RATE_LIMIT_ERROR_CODE = 460;
const EA_PAGE_SIZE = 20;
const MAX_NARROW_STEPS = 10;

// ── Price Narrowing ──────────────────────────────────────────────────────────

/**
 * Narrow the search price until we find a single page of results at one price.
 *
 * Algorithm:
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
    // EA doesn't sort by price, so a full page at one price doesn't mean it's the floor
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

export async function executeBuyCycle(
  player: ActionNeeded,
  sendMessage: (msg: any) => Promise<any>,
): Promise<BuyCycleResult> {
  // Step 1: Narrow to the cheapest price tier
  const { items: floorItems, error: searchError } = await narrowToFloor(
    player.ea_id,
    player.buy_price,
  );

  if (searchError) {
    return { outcome: 'error', reason: `Search failed (error ${searchError})` };
  }
  if (floorItems.length === 0) {
    return { outcome: 'skipped', reason: 'No items found' };
  }

  const floorPrice = Math.min(...floorItems.map(i => i.getAuctionData().buyNowPrice));

  // Price guard
  if (floorPrice > player.buy_price * PRICE_GUARD_MULTIPLIER) {
    return { outcome: 'skipped', reason: 'Price above guard' };
  }

  // Step 2: Buy the cheapest (retry on snipe with fresh search at floor price)
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    let items = floorItems;
    if (attempt > 0) {
      await jitter(1000, 2000);
      const minBid = Math.floor(Math.random() * 151);
      const criteria = buildCriteria(player.ea_id, floorPrice, minBid);
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
      return { outcome: 'error', reason: `Buy failed (error ${buyResult.error})` };
    }

    // List at locked OP price — try directly first, then via move-to-TL
    const sellBin = roundToNearestStep(player.sell_price);
    const sellStart = roundToNearestStep(getBeforeStepValue(player.sell_price));

    await jitter(500, 1000);
    let listResult = await listItem(cheapest, sellStart, sellBin);

    if (!listResult.success) {
      // Listing from unassigned failed — move to TL pile and retry
      await moveItem(cheapest, 5); // 5 = ItemPile.TRANSFER
      await jitter(500, 1000);
      listResult = await listItem(cheapest, sellStart, sellBin);
    }

    if (!listResult.success) {
      // Still failed — card is on TL unlisted, Phase A.5 will list it next cycle
      console.log(`[buy-cycle] listing failed (error ${listResult.error}) for defId=${player.ea_id} — will retry next cycle`);
    }

    return { outcome: 'bought', buyPrice: cheapestBin };
  }

  return { outcome: 'skipped', reason: `Sniped ${MAX_RETRIES} times` };
}
