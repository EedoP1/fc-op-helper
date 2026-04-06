/**
 * Buy cycle: search for a player, buy at or below target price, and list
 * at locked OP sell price — all via EA's internal services.Item APIs.
 *
 * No DOM manipulation, no navigation, no selectors, no page dependency.
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

/** Skip if cheapest BIN exceeds buy_price by more than 5%. */
const PRICE_GUARD_MULTIPLIER = 1.05;

/** Maximum buy attempts before giving up on a player. */
const MAX_RETRIES = 3;

/** EA error code returned when another buyer wins the auction. */
const SNIPE_ERROR_CODE = 461;

// ── Main Entry Point ──────────────────────────────────────────────────────────

/**
 * Execute a full buy-and-list cycle for a single player.
 *
 * Searches the transfer market up to MAX_RETRIES times (with random cache-bust
 * minBid), buys the cheapest listing that passes the price guard, and
 * immediately lists at the locked OP sell price.
 */
export async function executeBuyCycle(
  player: ActionNeeded,
  sendMessage: (msg: any) => Promise<any>,
): Promise<BuyCycleResult> {
  try {
    for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
      // Build search criteria with random minBid for cache busting
      const minBid = Math.floor(Math.random() * 1001);
      const criteria = buildCriteria(player.ea_id, player.buy_price, minBid);

      await jitter(1000, 2000);

      const { items } = await searchMarket(criteria);

      if (items.length === 0) continue;

      // Find the cheapest item by buyNowPrice
      let cheapest = items[0];
      let cheapestBin = cheapest.getAuctionData().buyNowPrice;
      for (let i = 1; i < items.length; i++) {
        const bin = items[i].getAuctionData().buyNowPrice;
        if (bin < cheapestBin) {
          cheapest = items[i];
          cheapestBin = bin;
        }
      }

      // Price guard — skip if BIN is too far above target
      if (cheapestBin > player.buy_price * PRICE_GUARD_MULTIPLIER) {
        return { outcome: 'skipped', reason: 'Price above guard' };
      }

      await jitter(1000, 2000);

      const result = await buyItem(cheapest, cheapestBin);

      if (!result.success) {
        if (result.errorCode === SNIPE_ERROR_CODE) continue;
        return { outcome: 'error', reason: `Buy failed (code ${result.errorCode})` };
      }

      // List at locked OP sell price
      const sellBin = roundToNearestStep(player.sell_price);
      const sellStart = roundToNearestStep(getBeforeStepValue(player.sell_price));

      await jitter(1000, 2000);

      await listItem(cheapest, sellStart, sellBin);

      return { outcome: 'bought', buyPrice: cheapestBin };
    }

    return { outcome: 'skipped', reason: `Sniped ${MAX_RETRIES} times` };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { outcome: 'error', reason: message };
  }
}
