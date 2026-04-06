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
  roundToNearestStep,
  getBeforeStepValue,
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

// ── Main Entry Point ──────────────────────────────────────────────────────────

export async function executeBuyCycle(
  player: ActionNeeded,
  sendMessage: (msg: any) => Promise<any>,
): Promise<BuyCycleResult> {
  for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
    const minBid = Math.floor(Math.random() * 1001);
    const criteria = buildCriteria(player.ea_id, player.buy_price, minBid);

    await jitter(2000, 4000);

    // Search
    const searchResult = await searchMarket(criteria);
    if (!searchResult.success) {
      if (searchResult.error === RATE_LIMIT_ERROR_CODE) {
        // Rate limited — wait and retry (FUT Enhancer uses 1200ms between retries)
        await jitter(4000, 8000);
        continue;
      }
      return { outcome: 'error', reason: `Search failed (error ${searchResult.error})` };
    }
    if (searchResult.items.length === 0) continue;

    // Find cheapest
    let cheapest = searchResult.items[0];
    let cheapestBin = cheapest.getAuctionData().buyNowPrice;
    for (let i = 1; i < searchResult.items.length; i++) {
      const bin = searchResult.items[i].getAuctionData().buyNowPrice;
      if (bin < cheapestBin) {
        cheapest = searchResult.items[i];
        cheapestBin = bin;
      }
    }

    // Price guard
    if (cheapestBin > player.buy_price * PRICE_GUARD_MULTIPLIER) {
      return { outcome: 'skipped', reason: 'Price above guard' };
    }

    await jitter(2000, 4000);

    // Buy
    const buyResult = await buyItem(cheapest, cheapestBin);
    if (!buyResult.success) {
      if (buyResult.error === SNIPE_ERROR_CODE) continue; // sniped — retry
      return { outcome: 'error', reason: `Buy failed (error ${buyResult.error})` };
    }

    // List at locked OP price
    const sellBin = roundToNearestStep(player.sell_price);
    const sellStart = roundToNearestStep(getBeforeStepValue(player.sell_price));

    await jitter(2000, 4000);

    const listResult = await listItem(cheapest, sellStart, sellBin);
    if (!listResult.success) {
      // Bought but failed to list — card is in unassigned pile
      return { outcome: 'error', reason: `Listed failed (error ${listResult.error}) — card in unassigned pile` };
    }

    return { outcome: 'bought', buyPrice: cheapestBin };
  }

  return { outcome: 'skipped', reason: `Sniped ${MAX_RETRIES} times` };
}
