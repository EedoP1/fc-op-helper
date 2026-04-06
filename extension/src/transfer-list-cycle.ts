/**
 * Transfer list cycle — fetch transfer list via EA services, relist expired,
 * clear sold, and report outcomes to the backend.
 *
 * No DOM interaction — all operations use EA's internal service layer.
 *
 * Error handling follows FUT Enhancer's pattern:
 * - EA calls return {success, error, data} — just use the data, don't check errors
 * - For transfer list reads: use empty array if it fails
 * - For relist/clearSold: fire and don't check errors
 * - Keep try/catch only around sendMessage calls (extension backend, not EA)
 */
import {
  getTransferList,
  relistAll,
  clearSold,
  refreshAuctions,
  type TransferListResult,
  type EAItem,
} from './ea-services';
import { jitter } from './automation';

// ── Types ────────────────────────────────────────────────────────────────────

/** Full result of a complete transfer list cycle. */
export type TransferListCycleResult = {
  groups: TransferListResult;
  relistedCount: number;
  soldCleared: number;
  isCapped: boolean;
};

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Execute the full transfer list cycle:
 * 1. Fetch transfer list and refresh auction data.
 * 2. Relist expired cards and report to backend.
 * 3. Report sold cards and clear them.
 * 4. Check daily cap.
 *
 * @param sendMessage - Callback to send messages to the service worker.
 * @returns TransferListCycleResult with groups, counts, and cap status.
 */
export async function executeTransferListCycle(
  sendMessage: (msg: any) => Promise<any>,
): Promise<TransferListCycleResult> {
  // Step 1 — Fetch transfer list (just use groups, ignore success/error)
  let { groups } = await getTransferList();

  // Refresh auction data if there are any items, then re-fetch for updated statuses
  if (groups.all.length > 0) {
    await jitter(1000, 2000);
    await refreshAuctions(groups.all);
    await jitter(1000, 2000);
    ({ groups } = await getTransferList());
  }

  // Step 2 — Relist expired cards (fire and ignore result, like FUT Enhancer)
  let relistedCount = 0;
  if (groups.expired.length > 0) {
    await jitter(1000, 2000);
    await relistAll();
    relistedCount = groups.expired.length;
  }

  // Step 3 — Report expired to backend
  if (groups.expired.length > 0) {
    try {
      await sendMessage({
        type: 'TRADE_REPORT_BATCH',
        reports: groups.expired.map((item: EAItem) => ({
          ea_id: item.definitionId,
          price: item.getAuctionData().buyNowPrice,
          outcome: 'expired' as const,
        })),
      });
    } catch {
      console.warn('[transfer-list-cycle] TRADE_REPORT_BATCH failed for expired items');
    }
  }

  // Step 4 — Report sold to backend
  if (groups.sold.length > 0) {
    try {
      await sendMessage({
        type: 'TRADE_REPORT_BATCH',
        reports: groups.sold.map((item: EAItem) => ({
          ea_id: item.definitionId,
          price: item.getAuctionData().buyNowPrice,
          outcome: 'sold' as const,
        })),
      });
    } catch {
      console.warn('[transfer-list-cycle] TRADE_REPORT_BATCH failed for sold items');
    }
  }

  // Step 5 — Clear sold cards (fire and ignore result, like FUT Enhancer)
  let soldCleared = 0;
  if (groups.sold.length > 0) {
    await jitter(1000, 2000);
    await clearSold();
    soldCleared = groups.sold.length;
  }

  // Step 6 — Check daily cap
  let isCapped = false;
  try {
    const capResult = await sendMessage({ type: 'DAILY_CAP_REQUEST' });
    if (capResult && capResult.capped === true) {
      isCapped = true;
    }
  } catch {
    console.warn('[transfer-list-cycle] DAILY_CAP_REQUEST failed — assuming not capped');
  }

  return { groups, relistedCount, soldCleared, isCapped };
}

/**
 * Read-only transfer list scan — fetch and categorize without any mutations.
 */
export async function scanTransferList(): Promise<TransferListResult> {
  const { groups } = await getTransferList();
  return groups;
}
