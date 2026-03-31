/**
 * Buy cycle: search for a player, discover price via binary search, buy when
 * price guard passes, and list the card at locked OP sell price from the
 * post-buy screen.
 *
 * This is the core revenue-generating automation. It covers the BUY and
 * immediate LIST steps of the full cycle (D-02).
 *
 * Key decisions implemented:
 *   D-06: Price discovery via binary search on max BIN
 *   D-07: Buy even when BIN is below target (snipe if cheaper)
 *   D-08: Price guard — skip if BIN > buy_price * 1.05
 *   D-09: Cache-bust by varying min BID by +50 each search
 *   D-10: 3 sniped-buy retries per player before skipping
 *   D-12: List immediately after buy at locked OP price
 *   D-24: Increment daily cap counter on each search attempt
 *   D-28 / AUTO-05: jitter() between every DOM interaction
 */
import * as SELECTORS from './selectors';
import {
  requireElement,
  clickElement,
  waitForElement,
  typePrice,
  jitter,
  AutomationError,
} from './automation';
import { navigateToTransferMarket, isOnSearchPage } from './navigation';
import type { ActionNeeded } from './messages';

// ── Types ─────────────────────────────────────────────────────────────────────

export type BuyCycleResult =
  | { outcome: 'bought'; buyPrice: number }
  | { outcome: 'skipped'; reason: string }
  | { outcome: 'error'; reason: string };

// ── Cache-bust counter (D-09) ─────────────────────────────────────────────────

/**
 * Module-level counter incremented by 50 each search to vary the min BID field.
 * EA caches search results by query params — changing any param busts the cache.
 */
let cacheBustBid = 0;

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Read the BIN price text from an item element and parse it to a number.
 * EA displays prices as "1,500" — strip commas before parseInt.
 * Returns NaN if the element is not found or the text is not parseable.
 */
function readBinPrice(item: Element): number {
  const el = item.querySelector(SELECTORS.ITEM_BIN_PRICE);
  if (!el) return NaN;
  return parseInt(el.textContent?.replace(/,/g, '') ?? '', 10);
}

/**
 * Get all price inputs within the search price section.
 * Section 0 = Bid Price (min[0], max[1]), Section 1 = Buy Now Price (min[2], max[3]).
 * Returns all inputs in a flat array ordered: bid-min, bid-max, bin-min, bin-max.
 */
function getPriceInputs(): HTMLInputElement[] {
  return Array.from(
    document.querySelectorAll<HTMLInputElement>(SELECTORS.SEARCH_PRICE_INPUT),
  );
}

/**
 * Clear a price input and set it to a new value using the same event sequence
 * as typePrice (but for integers with no digit-by-digit delay needed here).
 * We reuse typePrice for correctness since EA's framework needs the events.
 */
async function setPriceInput(
  inputs: HTMLInputElement[],
  index: number,
  value: number,
): Promise<void> {
  const input = inputs[index];
  if (!input) return;
  await typePrice(input, value);
}

/**
 * Find the EA-native "List for Transfer" button inside the quick list panel.
 * Matches by .primary class and text content to avoid Enhancer buttons.
 * (selectors.ts: QUICK_LIST_CONFIRM_CLASS — "btn-standard primary", not call-to-action)
 */
function findListConfirmButton(): HTMLButtonElement | null {
  const buttons = document.querySelectorAll<HTMLButtonElement>(
    `.${SELECTORS.QUICK_LIST_CONFIRM_CLASS.split(' ').join('.')}`,
  );
  for (const btn of Array.from(buttons)) {
    const text = btn.textContent?.trim() ?? '';
    if (text.includes('List for Transfer') || text.includes('List on Transfer Market')) {
      return btn;
    }
  }
  // Fallback: any primary button inside the quick list panel
  return document.querySelector<HTMLButtonElement>(
    `${SELECTORS.QUICK_LIST_PANEL} button.btn-standard.primary`,
  );
}

// ── Rarity filter ─────────────────────────────────────────────────────────────

/** Rarity filter dropdown index in the search page filter list. */
const RARITY_DROPDOWN_INDEX = 2;

/**
 * Map DB card_type values that DON'T match EA dropdown labels exactly.
 * null = skip rarity filter (use name+rating+position verification only).
 * Most card_types match the EA dropdown labels exactly and don't need entries here.
 */
const CARD_TYPE_TO_EA_RARITY: Record<string, string | null> = {
  // Empty/base cards — no rarity filter needed
  '': null,
  // Name differences between DB (fut.gg/scanner) and EA dropdown
  'Fantasy UT': 'Fantasy FC',
  'Fantasy UT Hero': 'Fantasy FC Hero',
  'Fantasy Captain ICON': 'Fantasy Captain ICON',
  'Champion Icon': 'Icon',
  'TOTY ICON': 'Team of the Year ICON',
  'Showdown Plus': 'Showdown Upgrade',
  // UEFA abbreviated names in DB -> full names in EA
  'UCL Road to the Knockouts': 'UEFA Champions League Primetime',
  'UECL Road to the Final': 'UEFA Conference League Road to the Final',
  'UECL Road to the Knockouts': 'UEFA Conference League Primetime',
  'UEL Road to the Final': 'UEFA Europa League Road to the Final',
  'UEL Road to the Knockouts': 'UEFA Europa League Primetime',
  'UWCL Primetime Hero': "UEFA Women's Champions League Primetime",
  'UWCL Road to the Knockouts': "UEFA Women's Champions League Primetime",
  // POTM cards don't have a rarity filter — they're under "Special Item" or missing
  'POTM Bundesliga': null,
  'POTM LALIGA EA SPORTS': null,
  'POTM LIGA F': null,
  'POTM Ligue 1': null,
  'POTM Premier League': null,
  'POTM Serie A': null,
  // Other missing rarities
  'Flashback Player': null,
  'End Of An Era': null,
  'Special Item': null,
  'SQUAD FOUNDATIONS': null,
  'Winter Wildcards Hero Red': 'Winter Wildcards Hero',
  'Winter Wildcards Icon Red': 'Winter Wildcards ICON',
};

/**
 * Open the Rarity dropdown and select the matching option.
 * Uses explicit mapping for known mismatches, case-insensitive fallback for the rest.
 * If card_type maps to null, leaves rarity at "Any" (relies on name+rating verification).
 *
 * The clickable target is .inline-container (not .ut-search-filter-control--row).
 */
async function setRarityFilter(cardType: string): Promise<void> {
  // Check explicit mapping first
  if (cardType in CARD_TYPE_TO_EA_RARITY) {
    const mapped = CARD_TYPE_TO_EA_RARITY[cardType];
    if (mapped === null) return; // skip rarity for this card type
  }
  const rarityLabel = CARD_TYPE_TO_EA_RARITY[cardType] ?? cardType;

  const dropdowns = document.querySelectorAll<HTMLElement>(SELECTORS.SEARCH_FILTER_DROPDOWN);
  const rarityDropdown = dropdowns[RARITY_DROPDOWN_INDEX];
  if (!rarityDropdown) return;

  // Open the dropdown by clicking .inline-container (not the row — row click doesn't work)
  const container = rarityDropdown.querySelector<HTMLElement>('.inline-container');
  if (!container) return;
  await clickElement(container);
  await jitter(300, 600);

  // Find and click the matching <li> option (case-insensitive comparison)
  const options = rarityDropdown.querySelectorAll('li');
  const targetLower = rarityLabel.toLowerCase();
  for (const opt of Array.from(options)) {
    if (opt.textContent?.trim().toLowerCase() === targetLower) {
      await clickElement(opt);
      return;
    }
  }
  // Option not found — close dropdown, proceed without filter (verification will catch mismatches)
  await clickElement(container);
}

// ── Card verification ─────────────────────────────────────────────────────────

/**
 * Verify the selected search result matches the expected player before buying.
 * Checks rating and position from the card element's DOM.
 * Returns false if the card doesn't match — prevents buying the wrong version.
 */
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

// ── Main export ───────────────────────────────────────────────────────────────

/**
 * Execute the full buy+list cycle for a single player.
 *
 * Steps:
 *   1. Navigate to Transfer Market search page (if not already there)
 *   2. Fill player name and rarity filter
 *   3. Binary-search price discovery with max BIN starting at buy_price
 *   4. Buy Now when price guard passes (BIN <= buy_price * 1.05)
 *   5. List immediately at locked OP sell price
 *   6. Increment daily cap counter after each search attempt
 *
 * @param player     ActionNeeded item from the backend portfolio actions endpoint
 * @param sendMessage  Callback to send messages to the service worker
 */
export async function executeBuyCycle(
  player: ActionNeeded,
  sendMessage: (msg: any) => Promise<any>,
): Promise<BuyCycleResult> {
  const PRICE_GUARD_MULTIPLIER = 1.05;
  const MAX_RETRIES = 3;             // D-10: 3 sniped-buy retries
  const MAX_BIN_STEP_PCT = 0.05;    // D-06: step max BIN up by 5% increments
  const MAX_BIN_STEPS = 5;           // stop after 5 steps to avoid runaway

  try {
    // ── Step 1: Navigate to search page (D-04) ─────────────────────────────
    if (!isOnSearchPage()) {
      await navigateToTransferMarket();
    }

    // ── Step 2: Fill player name search field (D-05) ───────────────────────
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

    nameInput.value = player.name;
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
        btn => btn.textContent?.trim().toLowerCase().includes(player.name.toLowerCase()),
      ) ?? buttons[0];
      if (match) {
        await clickElement(match);
        await jitter();
      }
    } catch {
      // No suggestions dropdown — name input may have pre-filtered already, continue
    }

    // ── Select rarity filter (D-05) ─────────────────────────────────────
    // Rarity is dropdown index 2: Quality=0, EvolutionStatus=1, Rarity=2
    if (player.card_type) {
      await setRarityFilter(player.card_type);
      await jitter();
    }

    // ── Step 3: Price discovery via binary search (D-06, D-07, D-08, D-09) ─
    let retries = 0;
    let maxBin = player.buy_price;
    const priceGuard = Math.floor(player.buy_price * PRICE_GUARD_MULTIPLIER);

    for (let step = 0; step <= MAX_BIN_STEPS; step++) {
      // Cache-bust: increment the module-level bid counter (D-09)
      cacheBustBid += 50;
      const minBid = cacheBustBid;

      // Set min BID (index 0 in the flat price-input array) to cache-bust value
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

      // Increment daily cap after every search attempt (D-24)
      sendMessage({ type: 'DAILY_CAP_INCREMENT' }).catch(() => {});

      // Wait for results to appear (up to 8 seconds)
      await jitter(1500, 3000);

      const resultsList = document.querySelector(SELECTORS.SEARCH_RESULTS_LIST);
      const resultItems = resultsList
        ? Array.from(resultsList.querySelectorAll<Element>(SELECTORS.TRANSFER_LIST_ITEM))
        : [];

      if (resultItems.length === 0) {
        // No results — step max BIN up by 5%
        maxBin = Math.floor(maxBin * (1 + MAX_BIN_STEP_PCT));

        if (maxBin > priceGuard) {
          // Price guard: the cheapest available card is above tolerance (D-08)
          return { outcome: 'skipped', reason: 'Price above guard' };
        }
        // Continue to next step with higher maxBin
        continue;
      }

      // Results found — read BIN of first (cheapest) result
      const cheapestItem = resultItems[0];
      const binPrice = readBinPrice(cheapestItem);

      if (isNaN(binPrice)) {
        return { outcome: 'error', reason: 'Could not read BIN price from search result' };
      }

      if (binPrice > priceGuard) {
        // Cheapest card is above the price guard tolerance (D-08)
        return { outcome: 'skipped', reason: 'Cheapest card above price guard' };
      }

      // BIN <= price guard — verify this is the right card before buying
      if (!verifyCard(cheapestItem, player.rating, player.position)) {
        return { outcome: 'skipped', reason: `Card mismatch: expected ${player.rating} ${player.position} ${player.card_type}` };
      }

      // ── Step 4: Execute Buy Now (D-26, D-10, D-11) ─────────────────────
      let bought = false;
      let actualBinPaid = binPrice;

      while (retries < MAX_RETRIES) {
        // Click the first result item to select it
        await clickElement(cheapestItem);
        await jitter();

        // Click Buy Now button
        const buyNowBtn = requireElement<HTMLElement>(
          'BUY_NOW_BUTTON',
          SELECTORS.BUY_NOW_BUTTON,
        );
        await clickElement(buyNowBtn);
        await jitter();

        // Wait for confirmation dialog
        await waitForElement(
          'EA_DIALOG_PRIMARY_BUTTON',
          SELECTORS.EA_DIALOG_PRIMARY_BUTTON,
          document,
          5_000,
        );

        // Click the primary confirm button in the dialog
        const confirmBtn = requireElement<HTMLElement>(
          'EA_DIALOG_PRIMARY_BUTTON',
          SELECTORS.EA_DIALOG_PRIMARY_BUTTON,
        );
        await clickElement(confirmBtn);
        await jitter(1000, 2000);

        // Check if the item expired (sniped) — expired items get .expired class
        const isExpired =
          cheapestItem.classList.contains('expired') ||
          document.querySelector(SELECTORS.SEARCH_RESULT_EXPIRED) !== null;

        if (isExpired) {
          // Card was sniped — retry (D-10)
          retries++;
          if (retries >= MAX_RETRIES) {
            return { outcome: 'skipped', reason: 'Sniped 3 times' };
          }
          // Re-search for fresh results
          const refreshBtn = requireElement<HTMLElement>(
            'SEARCH_SUBMIT_BUTTON',
            SELECTORS.SEARCH_SUBMIT_BUTTON,
          );
          await clickElement(refreshBtn);

          // Increment daily cap for the re-search (D-24)
          sendMessage({ type: 'DAILY_CAP_INCREMENT' }).catch(() => {});

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

          const freshBin = readBinPrice(freshItems[0]);
          if (isNaN(freshBin) || freshBin > priceGuard) {
            return { outcome: 'skipped', reason: 'Post-snipe price above guard' };
          }

          actualBinPaid = freshBin;
          // Loop to re-attempt buy with the refreshed first item
          continue;
        }

        // No expired indicator — buy succeeded
        bought = true;
        break;
      }

      if (!bought) {
        return { outcome: 'skipped', reason: 'Sniped 3 times' };
      }

      // ── Step 5: List immediately at locked OP price (D-12, D-13) ────────
      await jitter();

      // Click "List on Transfer Market" accordion to reveal the quick list panel
      const accordionBtn = requireElement<HTMLElement>(
        'LIST_ON_MARKET_ACCORDION',
        SELECTORS.LIST_ON_MARKET_ACCORDION,
      );
      await clickElement(accordionBtn);
      await jitter();

      // Wait for the quick list panel inputs to appear
      await waitForElement(
        'QUICK_LIST_PANEL',
        SELECTORS.QUICK_LIST_PANEL,
        document,
        8_000,
      );

      // Get [0]=Start Price, [1]=BIN Price inputs inside the quick list panel
      const listInputs = Array.from(
        document.querySelectorAll<HTMLInputElement>(SELECTORS.QUICK_LIST_PRICE_INPUTS),
      );

      if (listInputs.length < 2) {
        return { outcome: 'error', reason: 'Quick list panel inputs not found' };
      }

      // Set start price (EA requires start price <= BIN)
      const startPrice = Math.max(player.sell_price - 100, 200);
      await typePrice(listInputs[0], startPrice);
      await jitter();

      // Set BIN price to locked OP sell price (D-12: locked, not refreshed)
      await typePrice(listInputs[1], player.sell_price);
      await jitter();

      // Click the "List for Transfer" confirm button
      const listBtn = findListConfirmButton();
      if (!listBtn) {
        return { outcome: 'error', reason: 'List for Transfer button not found' };
      }
      await clickElement(listBtn);
      await jitter(1000, 2000);

      return { outcome: 'bought', buyPrice: actualBinPaid };
    }

    // Exhausted all maxBin steps without finding affordable results
    return { outcome: 'skipped', reason: 'Price above guard' };
  } catch (err) {
    if (err instanceof AutomationError) {
      return { outcome: 'error', reason: err.message };
    }
    const msg = err instanceof Error ? err.message : String(err);
    return { outcome: 'error', reason: `Unexpected: ${msg}` };
  }
}
