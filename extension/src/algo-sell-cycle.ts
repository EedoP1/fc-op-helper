/**
 * Algo sell cycle: find a card in the unassigned pile, discover the cheapest
 * BIN on the transfer market, then list the card at that price.
 *
 * Steps:
 *   1. Navigate to unassigned pile
 *   2. Find matching card by rating + name substring
 *   3. Navigate to transfer market, search to discover cheapest BIN
 *   4. Navigate back to unassigned pile, re-find the card
 *   5. Click card -> List on Transfer Market -> set BIN -> List
 *   6. Return listed with sellPrice
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
import type { AlgoSignal } from './messages';

// ── Types ─────────────────────────────────────────────────────────────────────

export type AlgoSellCycleResult =
  | { outcome: 'listed'; sellPrice: number; quantity: number }
  | { outcome: 'skipped'; reason: string }
  | { outcome: 'error'; reason: string };

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Read the BIN price from an item element. */
function readBinPrice(item: Element): number {
  const el = item.querySelector(SELECTORS.ITEM_BIN_PRICE);
  if (!el) return NaN;
  return parseInt(el.textContent?.replace(/,/g, '') ?? '', 10);
}

/**
 * Find the EA-native "List for Transfer" button inside the quick list panel.
 * Matches by .primary class and text content to avoid Enhancer buttons.
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
  return document.querySelector<HTMLButtonElement>(
    `${SELECTORS.QUICK_LIST_PANEL} button.btn-standard.primary`,
  );
}

/**
 * Navigate to the unassigned pile via the Transfers hub.
 * Returns the list of item elements found in unassigned.
 */
async function navigateToUnassigned(): Promise<Element[]> {
  // Click Transfers nav
  const transfersBtn = requireElement<HTMLElement>(
    'NAV_TRANSFERS',
    SELECTORS.NAV_TRANSFERS,
  );
  await clickElement(transfersBtn);
  await jitter(1000, 2000);

  // Click Unassigned tile
  const unassignedTile = await waitForElement<HTMLElement>(
    'TILE_UNASSIGNED',
    SELECTORS.TILE_UNASSIGNED,
    document,
    10_000,
  );
  await clickElement(unassignedTile);
  await jitter(1000, 2000);

  // Collect items
  return Array.from(document.querySelectorAll<Element>(SELECTORS.TRANSFER_LIST_ITEM));
}

/**
 * Find a card in a list of items that matches the signal's rating and name.
 * Uses substring match on player name (DOM shows short names).
 */
function findMatchingCard(
  items: Element[],
  signal: AlgoSignal,
): Element | null {
  const signalNameLower = signal.player_name.toLowerCase();

  for (const item of items) {
    const ratingEl = item.querySelector(SELECTORS.ITEM_RATING);
    const nameEl = item.querySelector(SELECTORS.ITEM_PLAYER_NAME);

    const rating = parseInt(ratingEl?.textContent?.trim() ?? '', 10);
    const name = nameEl?.textContent?.trim().toLowerCase() ?? '';

    if (rating !== signal.rating) continue;
    // Substring match: DOM might show "Lo Celso" while signal has "Giovani Lo Celso"
    if (signalNameLower.includes(name) || name.includes(signalNameLower)) {
      return item;
    }
  }
  return null;
}

// ── Main export ──────────────────────────────────────────────────────────────

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
  try {
    // ── Step 1: Navigate to unassigned pile ──────────────────────────────
    await jitter();
    const items = await navigateToUnassigned();

    if (items.length === 0) {
      return { outcome: 'skipped', reason: 'No items in unassigned pile' };
    }

    // ── Step 2: Find matching card ───────────────────────────────────────
    const card = findMatchingCard(items, signal);
    if (!card) {
      return { outcome: 'skipped', reason: `Card not found in unassigned: ${signal.player_name} ${signal.rating}` };
    }

    // ── Step 3: Discover cheapest BIN via transfer market search ─────────
    // Navigate to transfer market search
    const transfersBtn = requireElement<HTMLElement>(
      'NAV_TRANSFERS',
      SELECTORS.NAV_TRANSFERS,
    );
    await clickElement(transfersBtn);
    await jitter(1000, 2000);

    const searchTile = await waitForElement<HTMLElement>(
      'TILE_SEARCH_MARKET',
      '.ut-tile-transfer-market',
      document,
      10_000,
    );
    await clickElement(searchTile);
    await jitter();

    // Wait for search page
    await waitForElement(
      'SEARCH_PLAYER_NAME_INPUT',
      SELECTORS.SEARCH_PLAYER_NAME_INPUT,
      document,
      10_000,
    );

    // Fill player name
    const nameInput = requireElement<HTMLInputElement>(
      'SEARCH_PLAYER_NAME_INPUT',
      SELECTORS.SEARCH_PLAYER_NAME_INPUT,
    );
    nameInput.focus();
    nameInput.value = '';
    nameInput.dispatchEvent(new Event('input', { bubbles: true }));
    await jitter(300, 600);

    nameInput.value = signal.player_name;
    nameInput.dispatchEvent(new Event('input', { bubbles: true }));
    nameInput.dispatchEvent(new Event('change', { bubbles: true }));

    // Wait for autocomplete and click match
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
      // No suggestions — continue
    }

    // Click search
    const searchBtn = requireElement<HTMLElement>(
      'SEARCH_SUBMIT_BUTTON',
      SELECTORS.SEARCH_SUBMIT_BUTTON,
    );
    await clickElement(searchBtn);

    const searchResult = await waitForSearchResults();

    let discoveredPrice = signal.reference_price; // fallback to reference price

    if (searchResult.outcome === 'results') {
      const resultsList = document.querySelector(SELECTORS.SEARCH_RESULTS_LIST)!;
      const resultItems = Array.from(
        resultsList.querySelectorAll<Element>(SELECTORS.TRANSFER_LIST_ITEM),
      );

      // Find cheapest verified card
      let cheapestBin = Infinity;
      for (const item of resultItems) {
        const itemBin = readBinPrice(item);
        if (isNaN(itemBin)) continue;
        // Verify by rating
        const ratingEl = item.querySelector(SELECTORS.ITEM_RATING);
        const rating = parseInt(ratingEl?.textContent?.trim() ?? '', 10);
        if (rating === signal.rating && itemBin < cheapestBin) {
          cheapestBin = itemBin;
        }
      }
      if (cheapestBin < Infinity) {
        discoveredPrice = cheapestBin;
      }
    }

    // ── Step 4: Navigate back to unassigned pile ─────────────────────────
    // Go back from search results
    const backBtn = document.querySelector<HTMLElement>(SELECTORS.NAV_BACK_BUTTON);
    if (backBtn) {
      await clickElement(backBtn);
      await jitter(1000, 2000);
    }

    // Re-navigate to unassigned
    const items2 = await navigateToUnassigned();
    if (items2.length === 0) {
      return { outcome: 'error', reason: 'Unassigned pile empty after price discovery' };
    }

    // Re-find the card
    const card2 = findMatchingCard(items2, signal);
    if (!card2) {
      return { outcome: 'error', reason: 'Card disappeared from unassigned after price discovery' };
    }

    // ── Step 5: Click card -> List on Transfer Market -> set price ────────
    await clickElement(card2);
    await jitter();

    // Click the "List on Transfer Market" accordion
    const accordionBtn = await waitForElement<HTMLElement>(
      'LIST_ON_MARKET_ACCORDION',
      SELECTORS.LIST_ON_MARKET_ACCORDION,
      document,
      8_000,
    );
    await clickElement(accordionBtn);
    await jitter();

    // Wait for the quick list panel
    await waitForElement(
      'QUICK_LIST_PANEL',
      SELECTORS.QUICK_LIST_PANEL,
      document,
      8_000,
    );

    // Get price inputs: [0]=Start Price, [1]=BIN Price
    const listInputs = Array.from(
      document.querySelectorAll<HTMLInputElement>(SELECTORS.QUICK_LIST_PRICE_INPUTS),
    );

    if (listInputs.length < 2) {
      return { outcome: 'error', reason: 'Quick list panel price inputs not found' };
    }

    // Set start price (must be <= BIN)
    const startPrice = Math.max(discoveredPrice - 100, 200);
    await typePrice(listInputs[0], startPrice);
    await jitter();

    // Set BIN price to discovered market price
    await typePrice(listInputs[1], discoveredPrice);
    await jitter();

    // Click "List for Transfer"
    const listBtn = findListConfirmButton();
    if (!listBtn) {
      return { outcome: 'error', reason: 'List for Transfer button not found' };
    }
    await clickElement(listBtn);
    await jitter(1500, 3000);

    // Verify listing succeeded: quick list panel disappears on success
    const panelStillVisible = document.querySelector(SELECTORS.QUICK_LIST_PANEL) !== null;
    if (panelStillVisible) {
      const navBack = document.querySelector<HTMLElement>(SELECTORS.NAV_BACK_BUTTON);
      if (navBack) await clickElement(navBack);
      return { outcome: 'error', reason: 'Listing failed — panel still visible (TL full?)' };
    }

    return { outcome: 'listed', sellPrice: discoveredPrice, quantity: 1 };
  } catch (err) {
    if (err instanceof AutomationError) {
      return { outcome: 'error', reason: err.message };
    }
    const msg = err instanceof Error ? err.message : String(err);
    return { outcome: 'error', reason: `Unexpected: ${msg}` };
  }
}
