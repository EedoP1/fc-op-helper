/**
 * Algo buy cycle: search for a player from an algo signal, discover price
 * via binary search, buy when price guard passes, and navigate back.
 *
 * Unlike the OP sell buy-cycle, this does NOT list the card after buying.
 * The card stays in the unassigned pile until a SELL signal triggers listing.
 *
 * Key differences from buy-cycle.ts:
 *   - Uses signal's reference_price for price guard (10% tolerance)
 *   - Searches by signal.player_name + signal.rating + signal.position
 *   - Skips listing step — card stays unassigned after purchase
 *   - Navigates back to search page after buying
 */
import * as SELECTORS from './selectors';
import {
  requireElement,
  clickElement,
  waitForElement,
  waitForSearchResults,
  typePrice,
  jitter,
  AutomationError,
} from './automation';
import { navigateToTransferMarket, isOnSearchPage } from './navigation';
import type { AlgoSignal } from './messages';

// ── Types ─────────────────────────────────────────────────────────────────────

export type AlgoBuyCycleResult =
  | { outcome: 'bought'; buyPrice: number; quantity: number }
  | { outcome: 'skipped'; reason: string }
  | { outcome: 'error'; reason: string };

// ── Cache-bust counter ───────────────────────────────────────────────────────

let cacheBustBid = 0;

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Read the BIN price text from an item element and parse to number. */
function readBinPrice(item: Element): number {
  const el = item.querySelector(SELECTORS.ITEM_BIN_PRICE);
  if (!el) return NaN;
  return parseInt(el.textContent?.replace(/,/g, '') ?? '', 10);
}

/** Get all price inputs: bid-min, bid-max, bin-min, bin-max. */
function getPriceInputs(): HTMLInputElement[] {
  return Array.from(
    document.querySelectorAll<HTMLInputElement>(SELECTORS.SEARCH_PRICE_INPUT),
  );
}

/** Set a price input value using event dispatch. */
async function setPriceInput(
  inputs: HTMLInputElement[],
  index: number,
  value: number,
): Promise<void> {
  const input = inputs[index];
  if (!input) return;
  await typePrice(input, value);
}

/** Verify card matches expected rating and position. */
function verifyCard(
  item: Element,
  expectedRating: number,
  expectedPosition: string,
): boolean {
  const ratingEl = item.querySelector(SELECTORS.ITEM_RATING);
  const positionEl = item.querySelector(SELECTORS.ITEM_POSITION);

  const rating = parseInt(ratingEl?.textContent?.trim() ?? '', 10);
  const position = positionEl?.textContent?.trim() ?? '';

  if (isNaN(rating) || rating !== expectedRating) return false;
  if (position.toUpperCase() !== expectedPosition.toUpperCase()) return false;

  return true;
}

// ── Main export ──────────────────────────────────────────────────────────────

/**
 * Execute the algo buy cycle for a single signal.
 *
 * Steps:
 *   1. Navigate to Transfer Market search page (if not already there)
 *   2. Fill player name from signal
 *   3. Binary-search price discovery with max BIN starting at reference_price
 *   4. Buy Now when price guard passes (BIN <= reference_price * 1.10)
 *   5. Navigate back to search page (no listing)
 *
 * @param signal       AlgoSignal from the backend
 * @param sendMessage  Callback to send messages to the service worker
 */
export async function executeAlgoBuyCycle(
  signal: AlgoSignal,
  sendMessage: (msg: any) => Promise<any>,
): Promise<AlgoBuyCycleResult> {
  const PRICE_GUARD_MULTIPLIER = 1.10;
  const MAX_RETRIES = 3;
  const MAX_BIN_STEP_PCT = 0.05;
  const MAX_BIN_STEPS = 5;

  try {
    // ── Step 1: Navigate to search page ──────────────────────────────────
    if (!isOnSearchPage()) {
      await navigateToTransferMarket();
    }

    // ── Step 2: Fill player name search field ────────────────────────────
    await jitter();

    const nameInput = requireElement<HTMLInputElement>(
      'SEARCH_PLAYER_NAME_INPUT',
      SELECTORS.SEARCH_PLAYER_NAME_INPUT,
    );

    // Clear and type the player name to trigger the typeahead
    nameInput.focus();
    nameInput.value = '';
    nameInput.dispatchEvent(new Event('input', { bubbles: true }));
    await jitter(300, 600);

    nameInput.value = signal.player_name;
    nameInput.dispatchEvent(new Event('input', { bubbles: true }));
    nameInput.dispatchEvent(new Event('change', { bubbles: true }));

    // Wait for autocomplete suggestions and click the matching one
    await jitter(1000, 2000);
    try {
      const suggestionList = await waitForElement(
        'SEARCH_PLAYER_SUGGESTIONS',
        SELECTORS.SEARCH_PLAYER_SUGGESTIONS,
        document,
        5_000,
      );
      const buttons = Array.from(suggestionList.querySelectorAll('button'));
      const match = buttons.find(
        btn => btn.textContent?.trim().toLowerCase().includes(signal.player_name.toLowerCase()),
      ) ?? buttons[0];
      if (match) {
        await clickElement(match);
        await jitter();
      }
    } catch {
      // No suggestions dropdown — name input may have pre-filtered already, continue
    }

    // ── Step 3: Price discovery via binary search ────────────────────────
    let retries = 0;
    let maxBin = signal.reference_price;
    const priceGuard = Math.floor(signal.reference_price * PRICE_GUARD_MULTIPLIER);

    for (let step = 0; step <= MAX_BIN_STEPS; step++) {
      // Cache-bust: increment the module-level bid counter
      cacheBustBid += 50;
      if (cacheBustBid > 1000) cacheBustBid = 50;
      const minBid = cacheBustBid;

      // Set min BID and max BIN price inputs
      const priceInputs = getPriceInputs();
      if (priceInputs.length >= 4) {
        await setPriceInput(priceInputs, 0, minBid);  // bid-min
        await jitter(200, 400);
        await setPriceInput(priceInputs, 3, maxBin);  // bin-max
        await jitter(200, 400);
      }

      // Click search button
      const searchBtn = requireElement<HTMLElement>(
        'SEARCH_SUBMIT_BUTTON',
        SELECTORS.SEARCH_SUBMIT_BUTTON,
      );
      await clickElement(searchBtn);

      // Poll for search results
      const searchResult = await waitForSearchResults();

      if (searchResult.outcome === 'timeout' || searchResult.outcome === 'empty') {
        // No results — go back to search form before retrying
        const backBtn = document.querySelector<HTMLElement>(SELECTORS.NAV_BACK_BUTTON);
        if (backBtn) {
          await clickElement(backBtn);
          await jitter(1000, 2000);
        }

        // Step max BIN up by 5%
        maxBin = Math.floor(maxBin * (1 + MAX_BIN_STEP_PCT));

        if (maxBin > priceGuard) {
          return { outcome: 'skipped', reason: 'Price above guard' };
        }
        continue;
      }

      // Results found — scan for cheapest verified card
      const resultsList = document.querySelector(SELECTORS.SEARCH_RESULTS_LIST)!;
      const resultItems = Array.from(
        resultsList.querySelectorAll<Element>(SELECTORS.TRANSFER_LIST_ITEM),
      );

      let binPrice = Infinity;
      let cheapestItem: Element | null = null;

      for (const item of resultItems) {
        const itemBin = readBinPrice(item);
        if (isNaN(itemBin)) continue;
        if (!verifyCard(item, signal.rating, signal.position)) continue;
        if (itemBin < binPrice) {
          binPrice = itemBin;
          cheapestItem = item;
        }
      }

      if (!cheapestItem || binPrice === Infinity) {
        const backBtn = document.querySelector<HTMLElement>(SELECTORS.NAV_BACK_BUTTON);
        if (backBtn) {
          await clickElement(backBtn);
          await jitter(1000, 2000);
        }
        maxBin = Math.floor(maxBin * (1 + MAX_BIN_STEP_PCT));
        if (maxBin > priceGuard) {
          return { outcome: 'skipped', reason: 'No verified cards found within price guard' };
        }
        continue;
      }

      if (binPrice > priceGuard) {
        return { outcome: 'skipped', reason: 'Cheapest card above price guard' };
      }

      // ── Step 4: Execute Buy Now ────────────────────────────────────────
      let bought = false;
      let actualBinPaid = binPrice;

      while (retries < MAX_RETRIES) {
        let attemptFailed = false;

        // Click the card to select it
        await clickElement(cheapestItem);
        await jitter();

        // Click Buy Now button
        const buyNowBtn = document.querySelector<HTMLElement>(SELECTORS.BUY_NOW_BUTTON);
        if (!buyNowBtn) {
          attemptFailed = true;
        }

        // Wait for and click confirmation dialog
        if (!attemptFailed) {
          await clickElement(buyNowBtn!);
          await jitter();

          try {
            await waitForElement(
              'EA_DIALOG_PRIMARY_BUTTON',
              SELECTORS.EA_DIALOG_PRIMARY_BUTTON,
              document,
              5_000,
            );
          } catch {
            attemptFailed = true;
          }
        }

        if (!attemptFailed) {
          const confirmBtn = document.querySelector<HTMLElement>(SELECTORS.EA_DIALOG_PRIMARY_BUTTON);
          if (!confirmBtn) {
            attemptFailed = true;
          } else {
            await clickElement(confirmBtn);
            await jitter(500, 1000);

            // Poll for the accordion — definitive post-buy success indicator
            try {
              await waitForElement(
                'LIST_ON_MARKET_ACCORDION',
                SELECTORS.LIST_ON_MARKET_ACCORDION,
                document,
                8_000,
              );
              bought = true;
              break;
            } catch {
              attemptFailed = true;
            }
          }
        }

        // Retry logic
        retries++;
        if (retries >= MAX_RETRIES) {
          return { outcome: 'skipped', reason: 'Sniped 3 times' };
        }

        // Navigate back to search form to retry
        const backBtn = document.querySelector<HTMLElement>(SELECTORS.NAV_BACK_BUTTON);
        if (backBtn) {
          await clickElement(backBtn);
          await jitter(1000, 2000);
        }

        // Cache-bust before re-searching
        cacheBustBid += 50;
        if (cacheBustBid > 1000) cacheBustBid = 50;
        const retryPriceInputs = getPriceInputs();
        if (retryPriceInputs.length >= 4) {
          await setPriceInput(retryPriceInputs, 0, cacheBustBid);
          await jitter(200, 400);
        }

        // Re-search for fresh results
        const refreshBtn = await waitForElement<HTMLElement>(
          'SEARCH_SUBMIT_BUTTON',
          SELECTORS.SEARCH_SUBMIT_BUTTON,
          document,
          8_000,
        );
        await clickElement(refreshBtn);
        await jitter(1500, 3000);

        const refreshedList = document.querySelector(SELECTORS.SEARCH_RESULTS_LIST);
        const freshItems = refreshedList
          ? Array.from(
              refreshedList.querySelectorAll<Element>(SELECTORS.TRANSFER_LIST_ITEM),
            )
          : [];

        if (freshItems.length === 0) {
          return { outcome: 'skipped', reason: 'Sniped and no fresh results' };
        }

        cheapestItem = freshItems[0];
        const freshBin = readBinPrice(freshItems[0]);
        if (isNaN(freshBin) || freshBin > priceGuard) {
          return { outcome: 'skipped', reason: 'Post-snipe price above guard' };
        }

        actualBinPaid = freshBin;
        continue;
      }

      if (!bought) {
        return { outcome: 'skipped', reason: 'Sniped 3 times' };
      }

      // ── Step 5: Navigate back (no listing) ─────────────────────────────
      // Card stays in unassigned pile until a SELL signal triggers listing.
      await jitter();
      const backBtn = document.querySelector<HTMLElement>(SELECTORS.NAV_BACK_BUTTON);
      if (backBtn) {
        await clickElement(backBtn);
        await jitter(1000, 2000);
      }

      return { outcome: 'bought', buyPrice: actualBinPaid, quantity: 1 };
    }

    // Exhausted all maxBin steps
    return { outcome: 'skipped', reason: 'Price above guard' };
  } catch (err) {
    if (err instanceof AutomationError) {
      return { outcome: 'error', reason: err.message };
    }
    const msg = err instanceof Error ? err.message : String(err);
    return { outcome: 'error', reason: `Unexpected: ${msg}` };
  }
}
