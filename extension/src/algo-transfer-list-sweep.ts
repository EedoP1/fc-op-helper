/**
 * Algo transfer list sweep — scan the TL for sold/expired algo cards.
 *
 * Called by the algo automation loop at the start of each iteration.
 * Uses scanTransferList() for DOM reading, then:
 *   - Sold items matching algo positions → report to backend via ALGO_POSITION_SOLD
 *   - Expired items matching algo positions → discover current lowest BIN, relist individually
 *   - Clear sold items from the TL
 *
 * Does NOT use "Relist All" button (which relists at original locked price).
 * Instead, individually relists each expired card at current lowest BIN.
 */
import * as SELECTORS from './selectors';
import {
  clickElement,
  waitForElement,
  waitForSearchResults,
  requireElement,
  typePrice,
  jitter,
} from './automation';
import { scanTransferList, type TransferListScanResult } from './transfer-list-cycle';
import type { DetectedItem } from './trade-observer';
import type { ExtensionMessage } from './messages';

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

/**
 * Match a DetectedItem from the transfer list to an algo position by name.
 * Returns the matched position or null.
 * Uses DetectedItem.playerName (the field name in trade-observer.ts).
 */
function matchItemToPosition(
  item: DetectedItem,
  positions: PositionMatch[],
): PositionMatch | null {
  const itemName = item.playerName.toLowerCase();
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
 * Reuses the same search flow as algo-sell-cycle.ts.
 *
 * @param playerName  Player name to search for
 * @param rating      Player rating for result verification
 * @param fallbackPrice  Price to return if search fails
 * @returns Discovered lowest BIN price
 */
async function discoverLowestBin(
  playerName: string,
  rating: number,
  fallbackPrice: number,
): Promise<number> {
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

  nameInput.value = playerName;
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
      btn => btn.textContent?.trim().toLowerCase().includes(playerName.toLowerCase()),
    ) ?? buttons[0];
    if (match) {
      await clickElement(match);
      await jitter();
    }
  } catch {
    // No suggestions — continue with search
  }

  // Click search
  const searchBtn = requireElement<HTMLElement>(
    'SEARCH_SUBMIT_BUTTON',
    SELECTORS.SEARCH_SUBMIT_BUTTON,
  );
  await clickElement(searchBtn);

  const searchResult = await waitForSearchResults();
  let discoveredPrice = fallbackPrice;

  if (searchResult.outcome === 'results') {
    const resultsList = document.querySelector(SELECTORS.SEARCH_RESULTS_LIST)!;
    const resultItems = Array.from(
      resultsList.querySelectorAll<Element>(SELECTORS.TRANSFER_LIST_ITEM),
    );

    let cheapestBin = Infinity;
    for (const item of resultItems) {
      const binEl = item.querySelector(SELECTORS.ITEM_BIN_PRICE);
      const itemBin = parseInt(binEl?.textContent?.replace(/,/g, '') ?? '', 10);
      if (isNaN(itemBin)) continue;
      const ratingEl = item.querySelector(SELECTORS.ITEM_RATING);
      const itemRating = parseInt(ratingEl?.textContent?.trim() ?? '', 10);
      if (itemRating === rating && itemBin < cheapestBin) {
        cheapestBin = itemBin;
      }
    }
    if (cheapestBin < Infinity) {
      discoveredPrice = cheapestBin;
    }
  }

  // Navigate back from search results
  const backBtn = document.querySelector<HTMLElement>(SELECTORS.NAV_BACK_BUTTON);
  if (backBtn) {
    await clickElement(backBtn);
    await jitter(1000, 2000);
  }

  return discoveredPrice;
}

/**
 * Navigate back to the first page of the transfer list.
 */
async function goToFirstPage(): Promise<void> {
  let hasPrev = true;
  while (hasPrev) {
    const prevBtn = document.querySelector<HTMLButtonElement>(SELECTORS.PAGINATION_PREV);
    if (prevBtn && !prevBtn.disabled && !prevBtn.classList.contains('disabled')) {
      await clickElement(prevBtn);
      await jitter(400, 800);
      await new Promise(r => setTimeout(r, 300));
    } else {
      hasPrev = false;
    }
  }
}

/**
 * Find the Clear Sold button and click it (with confirmation dialog).
 */
async function clearSoldItems(): Promise<number> {
  await goToFirstPage();
  await jitter();

  const container = document.querySelector(SELECTORS.TRANSFER_LIST_CONTAINER);
  if (!container) return 0;

  const buttons = container.querySelectorAll<HTMLElement>('.section-header-btn');
  let clearBtn: HTMLElement | null = null;
  for (const btn of buttons) {
    const text = btn.textContent?.trim().toLowerCase() ?? '';
    if (text.includes('clear sold')) {
      clearBtn = btn;
      break;
    }
  }

  if (!clearBtn) return 0;

  await clickElement(clearBtn);
  await jitter();

  try {
    const confirmBtn = await waitForElement<HTMLElement>(
      'EA_DIALOG_PRIMARY_BUTTON',
      SELECTORS.EA_DIALOG_PRIMARY_BUTTON,
      document,
      3_000,
    );
    await clickElement(confirmBtn);
    await jitter();
  } catch {
    // No confirmation dialog — clear may execute immediately
  }

  return 1; // cleared
}

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

  // Step 1: Scan the transfer list
  const scan = await scanTransferList();

  // Handle processing items — wait and rescan
  const hasProcessing = scan.listed.some(item => item.status === 'processing');
  let finalScan: TransferListScanResult = scan;
  if (hasProcessing) {
    await new Promise(r => setTimeout(r, 5_000));
    finalScan = await scanTransferList();
  }

  if (stopped()) return result;

  // Step 2: Match sold items to algo positions and report
  const soldByPosition = new Map<number, { count: number; price: number }>();
  for (const item of finalScan.sold) {
    const match = matchItemToPosition(item, positions);
    if (!match) continue;
    const existing = soldByPosition.get(match.ea_id) ?? { count: 0, price: item.price };
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
  const expiredByPosition = new Map<number, { count: number; match: PositionMatch }>();
  for (const item of finalScan.expired) {
    const match = matchItemToPosition(item, positions);
    if (!match) continue;
    const existing = expiredByPosition.get(match.ea_id);
    if (!existing) {
      expiredByPosition.set(match.ea_id, { count: 1, match });
    } else {
      existing.count += 1;
    }
  }

  for (const [ea_id, { count, match }] of expiredByPosition) {
    if (stopped()) return result;

    // Discover current lowest BIN for this player
    const fallback = match.listed_price ?? match.buy_price;
    // Get rating from one of the expired items for search result verification
    const expiredItem = finalScan.expired.find(
      item => matchItemToPosition(item, [match]) !== null,
    );
    const rating = expiredItem?.rating ?? 0;

    const lowestBin = await discoverLowestBin(match.player_name, rating, fallback);

    if (stopped()) return result;

    // Navigate to transfer list to relist individual expired cards
    const { navigateToTransferList } = await import('./navigation');
    await navigateToTransferList();
    await jitter();

    // Rescan to find expired items for this player
    const rescan = await scanTransferList();
    const playerExpired = rescan.expired.filter(
      item => matchItemToPosition(item, [match]) !== null,
    );

    for (const expItem of playerExpired) {
      if (stopped()) return result;

      // Navigate to TL again (DOM may have changed)
      await navigateToTransferList();
      await jitter();

      // Find and click the expired card element on the current TL page
      const tlItems = Array.from(
        document.querySelectorAll<Element>(SELECTORS.TRANSFER_LIST_ITEM),
      );
      const itemNameLower = expItem.playerName.toLowerCase();
      let targetItem: Element | null = null;
      for (const el of tlItems) {
        const nameEl = el.querySelector(SELECTORS.ITEM_PLAYER_NAME);
        const name = nameEl?.textContent?.trim().toLowerCase() ?? '';
        const ratingEl = el.querySelector(SELECTORS.ITEM_RATING);
        const itemRating = parseInt(ratingEl?.textContent?.trim() ?? '', 10);
        if ((name.includes(itemNameLower) || itemNameLower.includes(name)) && itemRating === expItem.rating) {
          // Verify it's expired (has expired status indicator)
          const statusEl = el.querySelector('.auction-state');
          const statusText = statusEl?.textContent?.trim().toLowerCase() ?? '';
          if (statusText === 'expired') {
            targetItem = el;
            break;
          }
        }
      }

      if (!targetItem) continue;

      await clickElement(targetItem);
      await jitter();

      // Wait for and click the "Re-list" / "List on Transfer Market" accordion button
      try {
        const relistBtn = await waitForElement<HTMLElement>(
          'LIST_ON_MARKET_ACCORDION',
          SELECTORS.LIST_ON_MARKET_ACCORDION,
          document,
          8_000,
        );
        await clickElement(relistBtn);
        await jitter();
      } catch {
        continue; // Can't relist this card — skip
      }

      // Wait for quick list panel
      try {
        await waitForElement(
          'QUICK_LIST_PANEL',
          SELECTORS.QUICK_LIST_PANEL,
          document,
          8_000,
        );
      } catch {
        continue;
      }

      // Set prices
      const listInputs = Array.from(
        document.querySelectorAll<HTMLInputElement>(SELECTORS.QUICK_LIST_PRICE_INPUTS),
      );
      if (listInputs.length < 2) continue;

      const startPrice = Math.max(lowestBin - 100, 200);
      await typePrice(listInputs[0], startPrice);
      await jitter();
      await typePrice(listInputs[1], lowestBin);
      await jitter();

      // Click "List for Transfer"
      const panelButtons = document.querySelectorAll<HTMLButtonElement>(
        `${SELECTORS.QUICK_LIST_PANEL} button.btn-standard.primary`,
      );
      let listBtn: HTMLButtonElement | null = null;
      for (const btn of Array.from(panelButtons)) {
        const text = btn.textContent?.trim() ?? '';
        if (text.includes('List for Transfer') || text.includes('List on Transfer Market')) {
          listBtn = btn;
          break;
        }
      }
      if (!listBtn) {
        listBtn = document.querySelector<HTMLButtonElement>(
          `${SELECTORS.QUICK_LIST_PANEL} button.btn-standard.primary`,
        );
      }

      if (listBtn) {
        await clickElement(listBtn);
        await jitter(1500, 3000);
        result.relistedCount += 1;
      }
    }

    // Report relist to backend
    if (result.relistedCount > 0) {
      try {
        await sendMessage({
          type: 'ALGO_POSITION_RELIST',
          ea_id,
          price: lowestBin,
          quantity: count,
        } satisfies ExtensionMessage);
      } catch (err) {
        console.warn(`[algo-tl-sweep] ALGO_POSITION_RELIST failed for ea_id=${ea_id}:`, err);
      }
    }
  }

  // Step 4: Clear sold items from the TL
  if (soldByPosition.size > 0) {
    const { navigateToTransferList } = await import('./navigation');
    await navigateToTransferList();
    await jitter();
    const cleared = await clearSoldItems();
    result.clearedCount = cleared > 0 ? result.soldCount : 0;
  }

  return result;
}
