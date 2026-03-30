/**
 * EA Web App page navigation helpers.
 *
 * Provides functions to move between pages in the EA Web App via sidebar clicks.
 * All functions use DOM helpers from automation.ts and selectors from selectors.ts.
 *
 * Navigation pattern (D-04): click the sidebar item, then poll for the expected
 * page-identifying DOM element to appear (up to 10s timeout).
 * Jitter before and after clicks maintains human-paced timing (D-28).
 */
import * as SELECTORS from './selectors';
import { requireElement, clickElement, waitForElement, jitter } from './automation';

/**
 * Navigate to the Transfer Market search page.
 *
 * Clicks the Transfers nav button in the sidebar, then clicks the
 * "Search the Transfer Market" tile on the Transfers hub page.
 * Waits up to 10s for the search player name input to appear.
 *
 * Per D-04: auto-navigate between EA pages via sidebar clicks.
 */
export async function navigateToTransferMarket(): Promise<void> {
  await jitter();

  // Click the Transfers nav button to open the Transfers hub
  const transfersBtn = requireElement<HTMLElement>(
    'NAV_TRANSFERS',
    SELECTORS.NAV_TRANSFERS,
  );
  await clickElement(transfersBtn);

  await jitter();

  // Wait for the Transfers hub tile to appear, then click "Search the Transfer Market"
  const searchTile = await waitForElement<HTMLElement>(
    'TILE_SEARCH_MARKET',
    SELECTORS.TILE_SEARCH_MARKET,
    document,
    10_000,
  );
  await clickElement(searchTile);

  await jitter();

  // Wait for the search page to be ready (player name input must be present)
  await waitForElement(
    'SEARCH_PLAYER_NAME_INPUT',
    SELECTORS.SEARCH_PLAYER_NAME_INPUT,
    document,
    10_000,
  );
}

/**
 * Navigate to the Transfer List (trade pile) page.
 *
 * Clicks the Transfers nav button in the sidebar, then clicks the
 * "Transfer List" tile on the Transfers hub page.
 * Waits up to 10s for the transfer list container to appear.
 *
 * Per D-04: auto-navigate between EA pages via sidebar clicks.
 */
export async function navigateToTransferList(): Promise<void> {
  await jitter();

  // Click the Transfers nav button to open the Transfers hub
  const transfersBtn = requireElement<HTMLElement>(
    'NAV_TRANSFERS',
    SELECTORS.NAV_TRANSFERS,
  );
  await clickElement(transfersBtn);

  await jitter();

  // Wait for the Transfers hub tile to appear, then click "Transfer List"
  const listTile = await waitForElement<HTMLElement>(
    'TILE_TRANSFER_LIST',
    SELECTORS.TILE_TRANSFER_LIST,
    document,
    10_000,
  );
  await clickElement(listTile);

  await jitter();

  // Wait for the transfer list container to be ready
  await waitForElement(
    'TRANSFER_LIST_CONTAINER',
    SELECTORS.TRANSFER_LIST_CONTAINER,
    document,
    10_000,
  );
}

/**
 * Returns true if the current page shows the Transfer Market search form.
 * Non-throwing — uses querySelector directly without requireElement.
 */
export function isOnSearchPage(): boolean {
  return document.querySelector(SELECTORS.SEARCH_PLAYER_NAME_INPUT) !== null;
}

/**
 * Returns true if the current page shows the Transfer List (trade pile).
 * Non-throwing — uses querySelector directly without requireElement.
 */
export function isOnTransferListPage(): boolean {
  return document.querySelector(SELECTORS.TRANSFER_LIST_CONTAINER) !== null;
}
