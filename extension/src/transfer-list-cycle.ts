/**
 * Transfer list cycle — scan all pages, relist expired cards, detect and clear sold cards.
 *
 * Implements the relist half of the automation loop (D-02). When combined with Plan 03's
 * buy cycle, this provides the full buy/list/relist/rebuy loop.
 *
 * Key decisions:
 * - Relist All button (EA built-in) relists at original locked OP price — aligned with D-03.
 * - All pages of the transfer list are scanned before relisting (D-39).
 * - Daily cap checked via DAILY_CAP_REQUEST to gate buy operations (D-24, AUTO-04).
 * - Sold cards are detected and returned for the main loop to handle rebuy (D-02).
 * - ea_id matching by name+rating is deferred to the main loop (Plan 05) — DetectedItem
 *   does not carry ea_id; this module returns raw scan results.
 * - Relist All and Clear Sold are distinguished by section context or .primary class,
 *   never by position (dialog button ordering is inconsistent per STATE.md).
 */
import * as SELECTORS from './selectors';
import {
  clickElement,
  waitForElement,
  jitter,
  AutomationError,
} from './automation';
import { navigateToTransferList, isOnTransferListPage } from './navigation';
import { readTransferList, type DetectedItem } from './trade-observer';

// ── Types ────────────────────────────────────────────────────────────────────

/** Categorized items found across all pages of the transfer list. */
export type TransferListScanResult = {
  /** Cards currently actively listed (time remaining). */
  listed: DetectedItem[];
  /** Cards that expired without selling (eligible for Relist All). */
  expired: DetectedItem[];
  /** Cards that were sold (to be cleared and requeued for rebuy). */
  sold: DetectedItem[];
};

/** Full result of a complete transfer list cycle (scan + relist + clear). */
export type TransferListCycleResult = {
  /** All items scanned across all pages. */
  scanned: TransferListScanResult;
  /** Number of expired cards that were relisted via the Relist All button. */
  relistedCount: number;
  /** Number of sold cards that were cleared from the transfer list. */
  soldCleared: number;
  /** True when the daily transaction cap has been reached (D-24). */
  isCapped: boolean;
};

// ── Internal helpers ──────────────────────────────────────────────────────────

/**
 * Find the section-header-btn button within a specific transfer list section.
 * Transfer list has sections like "Sold Items" and "Unsold Items", each with a
 * .section-header-btn. Distinguished by .primary class:
 *   - "Re-list All" button: .section-header-btn (text contains "Re-list")
 *   - "Clear Sold" button: .section-header-btn (text contains "Clear Sold")
 *   NOTE: Both buttons have .primary class — match by text, not class.
 *
 * @param want - 'relist' for Re-list All, 'clear' for Clear Sold.
 */
function findSectionHeaderButton(want: 'relist' | 'clear'): HTMLElement | null {
  const container = document.querySelector(SELECTORS.TRANSFER_LIST_CONTAINER);
  if (!container) return null;

  const buttons = container.querySelectorAll<HTMLElement>('.section-header-btn');
  for (const btn of buttons) {
    const text = btn.textContent?.trim().toLowerCase() ?? '';
    if (want === 'relist' && (text.includes('re-list') || text.includes('relist'))) return btn;
    if (want === 'clear' && text.includes('clear sold')) return btn;
  }

  return null;
}

/**
 * Scans all pages of the transfer list and accumulates items by status.
 * Handles pagination by checking for an enabled "next" button after each page (D-39).
 *
 * @returns Accumulated scan result across all transfer list pages.
 */
async function scanAllPages(): Promise<TransferListScanResult> {
  const listed: DetectedItem[] = [];
  const expired: DetectedItem[] = [];
  const sold: DetectedItem[] = [];

  let hasMore = true;
  while (hasMore) {
    // Read current page items
    const items = readTransferList();
    for (const item of items) {
      if (item.status === 'listed' || item.status === 'processing') listed.push(item);
      else if (item.status === 'expired') expired.push(item);
      else if (item.status === 'sold') sold.push(item);
      // 'bought' status is not expected on the transfer list (trade pile only)
    }

    // Check for pagination — PAGINATION_NEXT selector from selectors.ts (main repo)
    const nextBtn = document.querySelector<HTMLButtonElement>(SELECTORS.PAGINATION_NEXT);
    if (nextBtn && !nextBtn.disabled && !nextBtn.classList.contains('disabled')) {
      await clickElement(nextBtn);
      // Wait for the page content to update after pagination click
      await jitter(600, 1200);
      // Poll briefly to let the DOM settle (new items should replace old ones)
      await new Promise(r => setTimeout(r, 300));
    } else {
      hasMore = false;
    }
  }

  return { listed, expired, sold };
}

/**
 * Navigate back to the first page of the transfer list.
 * Clicks "Prev" button repeatedly until no prev button is enabled.
 * Used before clicking Relist All so we relist from page 1.
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

// ── Public API ────────────────────────────────────────────────────────────────

/**
 * Executes the full transfer list cycle:
 * 1. Navigate to transfer list (if not already there).
 * 2. Scan all pages to categorize listed/expired/sold cards (D-39).
 * 3. Check daily cap status via backend (D-24, AUTO-04).
 * 4. Relist all expired cards via the Relist All button (D-03, AUTO-03).
 * 5. Report relist outcomes to backend.
 * 6. Clear sold cards from the transfer list (D-02).
 * 7. Return structured results for the main automation loop.
 *
 * @param sendMessage - Callback to send messages to the service worker.
 * @returns TransferListCycleResult with scan data, counts, and cap status.
 */
export async function executeTransferListCycle(
  sendMessage: (msg: any) => Promise<any>,
): Promise<TransferListCycleResult> {
  // Step 1 — Always navigate to transfer list (D-04).
  // EA doesn't update the TL DOM in place — must leave and re-enter to see
  // changes (new sales, expired cards). Always navigate even if already there.
  await navigateToTransferList();

  // Step 2 — Scan all pages (D-39)
  // If any items are "processing", wait 5s and rescan — processing is transient
  // and resolves to sold/expired. We need the final state for accurate counts.
  let scanned = await scanAllPages();
  const hasProcessing = scanned.listed.some(item => item.status === 'processing');
  if (hasProcessing) {
    await new Promise(r => setTimeout(r, 5_000));
    await navigateToTransferList();
    scanned = await scanAllPages();
  }
  const { listed, expired, sold } = scanned;

  // Step 3 — Check daily cap status (D-24, AUTO-04)
  let isCapped = false;
  try {
    const capResult = await sendMessage({ type: 'DAILY_CAP_REQUEST' });
    if (capResult && capResult.capped === true) {
      isCapped = true;
    }
  } catch {
    // If cap check fails (backend unavailable), assume not capped to avoid stopping automation
    console.warn('[transfer-list-cycle] DAILY_CAP_REQUEST failed — assuming not capped');
  }

  // Step 4 — Relist expired cards via Relist All button (D-03, AUTO-03)
  let relistedCount = 0;
  if (expired.length > 0) {
    // Navigate to first page so Relist All captures all expired cards
    await goToFirstPage();
    await jitter();

    try {
      // Relist All button has the .primary class on the section-header-btn
      // (distinguished from Clear Sold which does NOT have .primary per 08-01 SUMMARY)
      const relistBtn = findSectionHeaderButton('relist');
      if (relistBtn) {
        await clickElement(relistBtn);
        await jitter();

        // Wait for the confirmation dialog and click the primary (Yes) button
        // Re-list All dialog: [Cancel, Yes (primary)] — always match by .primary class
        try {
          const confirmBtn = await waitForElement<HTMLElement>(
            'EA_DIALOG_PRIMARY_BUTTON',
            SELECTORS.EA_DIALOG_PRIMARY_BUTTON,
            document,
            5_000,
          );
          await clickElement(confirmBtn);
          await jitter();
        } catch {
          // Dialog may not appear if there's nothing to relist on this view
          console.warn('[transfer-list-cycle] Relist All confirmation dialog did not appear');
        }

        relistedCount = expired.length;
      } else {
        // Button not found — may mean expired cards are on a different view state
        console.warn(
          `[transfer-list-cycle] Relist All button not found, but ${expired.length} expired items detected`,
        );
      }
    } catch (err) {
      if (err instanceof AutomationError) throw err; // propagate critical navigation errors
      console.warn('[transfer-list-cycle] Relist All error:', err);
    }

    // Step 5 — Report relist outcomes to backend
    // Note: ea_id resolution by name+rating is deferred to the main loop (Plan 05).
    // We send ea_id=0 here; the backend will accept it or the caller will post-process.
    if (relistedCount > 0) {
      try {
        await sendMessage({
          type: 'TRADE_REPORT_BATCH',
          reports: expired.map(item => ({
            ea_id: 0, // Resolved by main loop via name+rating matching
            price: item.price,
            outcome: 'expired' as const,
          })),
        });
      } catch {
        console.warn('[transfer-list-cycle] TRADE_REPORT_BATCH failed for relisted items');
      }
    }
  }

  // Step 6 — Clear sold cards (D-02)
  let soldCleared = 0;
  if (sold.length > 0) {
    try {
      // Navigate to first page before looking for Clear Sold button — same reason as
      // goToFirstPage() before relist: scanAllPages() ends on the last page, and EA only
      // shows the "Sold Items" section header button when those items are in view.
      await goToFirstPage();
      await jitter();

      // Clear Sold button does NOT have .primary class
      // (Relist All has .primary; Clear Sold does not per 08-01 SUMMARY)
      const clearBtn = findSectionHeaderButton('clear');

      if (clearBtn) {
        await clickElement(clearBtn);
        await jitter();

        // Wait for and confirm the clear sold dialog (if any appears)
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
          // No confirmation dialog — clear sold may execute immediately
        }

        soldCleared = sold.length;
      } else {
        console.warn(
          `[transfer-list-cycle] Clear Sold button not found, but ${sold.length} sold items detected`,
        );
      }
    } catch (err) {
      if (err instanceof AutomationError) throw err;
      console.warn('[transfer-list-cycle] Clear Sold error:', err);
    }
  }

  // Step 7 — Return result
  return {
    scanned: { listed, expired, sold },
    relistedCount,
    soldCleared,
    isCapped,
  };
}

/**
 * Standalone transfer list scan — navigate and read all pages without relisting.
 * Used for resume/cold-start flows (D-18, D-19) where the cycle needs to assess
 * current state without taking any relist or clear actions.
 *
 * @returns TransferListScanResult with all items across all pages.
 */
export async function scanTransferList(): Promise<TransferListScanResult> {
  await navigateToTransferList();
  return scanAllPages();
}
