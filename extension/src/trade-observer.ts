/**
 * Trade observer — pure DOM reader for the EA Web App Transfer List.
 *
 * Reads the transfer list DOM and returns detected player items with their
 * trade status (listed, sold, expired, bought) and price. Designed to be
 * called from the content script when the user is on the Transfer List page.
 *
 * All CSS selectors are imported from selectors.ts (AUTO-08 requirement).
 * No hardcoded selector strings in this module.
 *
 * Pure function — no side effects. Designed for unit testing with jsdom.
 */
import {
  TRANSFER_LIST_CONTAINER,
  TRANSFER_LIST_ITEM,
  ITEM_STATUS_LABEL,
  ITEM_PLAYER_NAME,
  ITEM_BIN_PRICE,
} from './selectors';

/**
 * A single detected item from the Transfer List DOM.
 * Stateless — represents what was found in the DOM at scan time.
 */
export type DetectedItem = {
  playerName: string;
  status: 'listed' | 'sold' | 'expired' | 'bought';
  price: number;
};

/**
 * Map raw status text from the DOM to a normalized outcome string.
 * EA may use different casing or labels — normalize here.
 * Common patterns: "Active" -> listed, "Sold" -> sold, "Expired" -> expired.
 * Time strings (e.g. "55 Minutes") indicate an active listing.
 */
const STATUS_MAP: Record<string, DetectedItem['status']> = {
  'active': 'listed',
  'listed': 'listed',
  'sold': 'sold',
  'expired': 'expired',
  'won': 'bought',
  'bought': 'bought',
};

/**
 * Parse a price string from the DOM into an integer.
 * Handles comma-separated values like "15,000" and strips non-numeric chars.
 */
function parsePrice(text: string): number {
  const cleaned = text.replace(/[^0-9]/g, '');
  return parseInt(cleaned, 10) || 0;
}

/**
 * Detect if a status text string represents an active listing
 * (i.e. a time remaining string like "55 Minutes" or "1 Hour").
 * These are not in STATUS_MAP but indicate the item is actively listed.
 */
function isTimeRemaining(text: string): boolean {
  // Time strings contain digits (e.g. "55 Minutes", "1 Hour", "30 Seconds")
  return /\d/.test(text);
}

/**
 * Read the current Transfer List DOM and return detected player items.
 * Returns empty array if the transfer list container is not present (wrong page)
 * or if selectors do not match (DOM structure changed).
 *
 * @param root - DOM root to query (defaults to document). Pass a test container for unit tests.
 */
export function readTransferList(root: Document | Element = document): DetectedItem[] {
  const container = root.querySelector(TRANSFER_LIST_CONTAINER);
  if (!container) return [];

  const items = container.querySelectorAll(TRANSFER_LIST_ITEM);
  const result: DetectedItem[] = [];

  for (const item of items) {
    const nameEl = item.querySelector(ITEM_PLAYER_NAME);
    const statusEl = item.querySelector(ITEM_STATUS_LABEL);
    const priceEl = item.querySelector(ITEM_BIN_PRICE);

    if (!nameEl || !statusEl) continue; // skip malformed items

    const rawStatus = (statusEl.textContent ?? '').trim().toLowerCase();

    let status: DetectedItem['status'] | undefined = STATUS_MAP[rawStatus];

    // Time remaining strings (e.g. "55 Minutes") indicate an active listing
    if (!status && isTimeRemaining(rawStatus)) {
      status = 'listed';
    }

    if (!status) continue; // unknown status — skip

    const playerName = (nameEl.textContent ?? '').trim();
    const price = priceEl ? parsePrice(priceEl.textContent ?? '0') : 0;

    result.push({ playerName, status, price });
  }

  return result;
}

/**
 * Check if the transfer list container is present in the DOM.
 * Used by the content script to gate observer activation.
 *
 * @param root - DOM root to query (defaults to document).
 */
export function isTransferListPage(root: Document | Element = document): boolean {
  return root.querySelector(TRANSFER_LIST_CONTAINER) !== null;
}
