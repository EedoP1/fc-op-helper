/**
 * Content script for the EA Sports FC Web App.
 *
 * Injected on all EA Web App pages (broad match per D-07).
 * Lightweight — just listens for messages from the service worker.
 * Phase 7/8 will add DOM automation on top of this foundation.
 *
 * Key behaviors:
 *   - ARCH-03: Handles PING message with PONG response (typed discriminated union)
 *   - ARCH-04: Re-initializes listeners on SPA navigation (wxt:locationchange primary)
 *   - D-08: MutationObserver on document.body as SPA navigation fallback
 *   - D-09: Auto-reconnect loop retries on service worker disconnection
 *   - D-01: Overlay panel injected as collapsible right sidebar
 *   - D-11: Confirmed portfolio loaded from backend on page mount
 */
import { ExtensionMessage, assertNever } from '../src/messages';
import { createOverlayPanel } from '../src/overlay/panel';
import { readTransferList, isTransferListPage } from '../src/trade-observer';
import { portfolioItem, reportedOutcomesItem } from '../src/storage';
import { TRANSFER_LIST_CONTAINER } from '../src/selectors';

export default defineContentScript({
  matches: ['https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*'],
  runAt: 'document_idle',
  main(ctx) {
    /**
     * Handle incoming messages from the service worker.
     * Returns true to signal async response (required for Chrome MV3).
     * Exhaustive switch — assertNever in default forces compile error on unhandled types.
     */
    function handleMessage(
      msg: ExtensionMessage,
      _sender: chrome.runtime.MessageSender,
      sendResponse: (response?: any) => void,
    ): boolean {
      switch (msg.type) {
        case 'PING':
          sendResponse({ type: 'PONG' } satisfies ExtensionMessage);
          return true;
        case 'PONG':
          // PONG is a response type — content script should not receive it;
          // handle explicitly to satisfy exhaustive switch (D-05 compile-time safety).
          return false;
        case 'PORTFOLIO_GENERATE':
        case 'PORTFOLIO_CONFIRM':
        case 'PORTFOLIO_SWAP':
        case 'PORTFOLIO_LOAD':
          // These are request types sent TO the service worker, not TO the content script.
          // Content script should not receive these — return false (no response).
          return false;
        case 'PORTFOLIO_GENERATE_RESULT':
        case 'PORTFOLIO_CONFIRM_RESULT':
        case 'PORTFOLIO_SWAP_RESULT':
        case 'PORTFOLIO_LOAD_RESULT':
          // These are response types — content script receives them as sendMessage return values,
          // not via onMessage listener. Handle explicitly for assertNever exhaustiveness.
          return false;
        case 'TRADE_REPORT':
          // Request type sent TO the service worker — content script should not receive it via onMessage.
          return false;
        case 'TRADE_REPORT_RESULT':
        case 'TRADE_REPORT_BATCH':
        case 'TRADE_REPORT_BATCH_RESULT':
          return false;
        case 'DASHBOARD_STATUS_REQUEST':
          // Request type sent TO the service worker — content script should not receive it via onMessage.
          return false;
        case 'DASHBOARD_STATUS_RESULT':
          // Response type — received as sendMessage return value, not via onMessage.
          return false;
        default:
          assertNever(msg);
      }
    }

    function initListeners() {
      chrome.runtime.onMessage.addListener(handleMessage);
      console.log('[OP Seller CS] Listeners initialized');
    }

    function teardownListeners() {
      chrome.runtime.onMessage.removeListener(handleMessage);
    }

    // ── Overlay panel injection (D-01: right sidebar) ──────────────────────
    // Created before event handlers so the wxt:locationchange handler can reference it.
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);
    document.body.appendChild(panel.toggle);

    // Clean up on content script invalidation
    ctx.onInvalidated(() => panel.destroy());

    // ── Trade Observer (Phase 07.1: passive DOM reading per D-01) ──────────
    let tradeObserver: MutationObserver | null = null;

    // In-memory dedup map — tracks last reported status per player (ea_id → status).
    // A trade is only reported when the status *changes* for a player, so:
    //   LISTED → SOLD → LISTED → SOLD = 4 reports (2 full sell cycles)
    //   LISTED → SOLD → SOLD = 2 reports (second SOLD deduped)
    // Persisted to chrome.storage.local so it survives service worker restarts
    // and page refreshes.
    const lastReportedStatus = new Map<string, string>();
    let reportedSetLoaded = false;
    let scanInProgress = false;

    // Pending queue for failed reports — retried every 10s until backend is reachable
    type TradeOutcome = 'bought' | 'listed' | 'sold' | 'expired';
    const pendingReports: Array<{ ea_id: number; price: number; outcome: TradeOutcome; playerName: string }> = [];
    let retryTimerId: ReturnType<typeof setTimeout> | null = null;

    async function retryPendingReports() {
      if (pendingReports.length === 0) return;
      const batch = [...pendingReports];
      pendingReports.length = 0;
      for (const report of batch) {
        try {
          const response = await chrome.runtime.sendMessage({
            type: 'TRADE_REPORT',
            ea_id: report.ea_id,
            price: report.price,
            outcome: report.outcome,
          } satisfies ExtensionMessage);
          if (response && response.type === 'TRADE_REPORT_RESULT' && response.success) {
            lastReportedStatus.set(String(report.ea_id), report.outcome);
            console.log(`[OP Seller CS] Retry OK: ${report.outcome} for ${report.playerName} (${report.ea_id})`);
            continue;
          }
        } catch { /* still down */ }
        pendingReports.push(report); // re-queue
      }
      // Persist dedup map after successful retries
      if (batch.length !== pendingReports.length) {
        await reportedOutcomesItem.setValue(
          [...lastReportedStatus.entries()].map(([id, status]) => `${id}:${status}`),
        );
      }
      // Schedule next retry if still pending
      if (pendingReports.length > 0) {
        retryTimerId = setTimeout(retryPendingReports, 10_000);
      }
    }

    /**
     * Scan the Transfer List DOM for portfolio player outcomes.
     * Matches detected items against the confirmed portfolio by player name (D-03).
     * Reports new outcomes to the service worker for backend relay (D-06).
     * Deduplicates using in-memory set + storage persistence (D-07).
     */
    async function scanTransferList() {
      // Prevent concurrent scans from MutationObserver rapid-fire
      if (scanInProgress) return;
      scanInProgress = true;

      try {
        await scanTransferListInner();
      } finally {
        scanInProgress = false;
      }
    }

    async function scanTransferListInner() {
      const portfolio = await portfolioItem.getValue();
      if (!portfolio || portfolio.players.length === 0) return;

      const items = readTransferList(document);
      if (items.length === 0) return;

      // Load persisted dedup map on first scan
      if (!reportedSetLoaded) {
        const stored = await reportedOutcomesItem.getValue();
        let migrated = false;
        for (const key of stored) {
          // New format: "ea_id:status" (exactly one colon)
          // Old format: "ea_id:status:price" (two colons) — discard on migration
          const parts = key.split(':');
          if (parts.length === 2 && parts[0] && parts[1]) {
            lastReportedStatus.set(parts[0], parts[1]);
          } else {
            migrated = true; // old format entry — skip it
          }
        }
        if (migrated) {
          // Persist cleaned map so old entries don't reload next time
          await reportedOutcomesItem.setValue(
            [...lastReportedStatus.entries()].map(([id, status]) => `${id}:${status}`),
          );
        }
        reportedSetLoaded = true;
      }

      // Match DOM items to portfolio using composite key:
      // name (endsWith) + rating + position
      // DOM shows short names ("Lo Celso") while portfolio has full names ("Giovani Lo Celso"),
      // so we use endsWith for name matching and rating+position to disambiguate.

      // Collect all new reports, then send as a single batch
      const batch: Array<{ ea_id: number; price: number; outcome: TradeOutcome; playerName: string }> = [];

      for (const item of items) {
        const domName = item.playerName.toLowerCase();
        const match = portfolio.players.find(p =>
          p.name.toLowerCase().endsWith(domName) &&
          p.rating === item.rating &&
          p.position === item.position,
        );
        if (!match) continue; // Not a portfolio player (D-03)

        const eaId = String(match.ea_id);
        if (lastReportedStatus.get(eaId) === item.status) continue; // Same status — dedup (D-07)

        batch.push({ ea_id: match.ea_id, price: item.price, outcome: item.status, playerName: item.playerName });
      }

      if (batch.length === 0) return;

      // Send all reports in one message → one backend call → one DB transaction
      try {
        const response = await chrome.runtime.sendMessage({
          type: 'TRADE_REPORT_BATCH',
          reports: batch.map(r => ({ ea_id: r.ea_id, price: r.price, outcome: r.outcome })),
        } satisfies ExtensionMessage);

        if (response && response.type === 'TRADE_REPORT_BATCH_RESULT') {
          const succeededSet = new Set(response.succeeded);
          for (const report of batch) {
            if (succeededSet.has(report.ea_id)) {
              lastReportedStatus.set(String(report.ea_id), report.outcome);
            }
          }
          const ok = response.succeeded?.length ?? 0;
          const fail = response.failed?.length ?? 0;
          console.log(`[OP Seller CS] Batch reported ${ok} trades (${fail} failed)`);
          if (fail > 0) {
            // Queue failed ones for retry
            for (const report of batch) {
              if (!succeededSet.has(report.ea_id)) {
                pendingReports.push(report);
              }
            }
            if (pendingReports.length > 0 && !retryTimerId) {
              retryTimerId = setTimeout(retryPendingReports, 10_000);
            }
          }
        }
      } catch {
        // Backend completely unreachable — queue everything
        pendingReports.push(...batch);
        if (!retryTimerId) {
          retryTimerId = setTimeout(retryPendingReports, 10_000);
          console.log(`[OP Seller CS] Backend down, queued ${batch.length} report(s) for retry`);
        }
      }

      // Persist dedup map to storage (survives page refresh)
      await reportedOutcomesItem.setValue(
        [...lastReportedStatus.entries()].map(([id, status]) => `${id}:${status}`),
      );
    }

    /**
     * Activate the trade observer if the user is on the Transfer List page.
     * Sets up a MutationObserver on the transfer list container to re-scan
     * when items change (new cards appear, status changes).
     * Per D-01: passive scan only when user is already on this page.
     */
    function maybeStartTradeObserver() {
      // Clean up previous observer if any
      if (tradeObserver) {
        tradeObserver.disconnect();
        tradeObserver = null;
      }

      if (!isTransferListPage(document)) return;

      // Initial scan (D-09: bootstrap — detect current state)
      scanTransferList();

      // Observe the transfer list container for new/changed items
      const container = document.querySelector(TRANSFER_LIST_CONTAINER);
      if (!container) return;

      let debounceTimer: ReturnType<typeof setTimeout> | null = null;
      tradeObserver = new MutationObserver(() => {
        // Debounce re-scans — DOM mutations fire rapidly (item renders, status updates).
        // Wait 500ms of quiet before re-scanning to avoid duplicate reports.
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => scanTransferList(), 500);
      });

      // Observe subtree for child and character data changes (item status updates)
      tradeObserver.observe(container, { childList: true, subtree: true, characterData: true });
      console.log('[OP Seller CS] Trade observer activated on Transfer List');
    }

    // Clean up trade observer and retry timer on invalidation
    ctx.onInvalidated(() => {
      if (retryTimerId) clearTimeout(retryTimerId);
      if (tradeObserver) {
        tradeObserver.disconnect();
        tradeObserver = null;
      }
    });

    // Primary SPA detection: WXT locationchange fires on History API navigation (D-04/D-08)
    ctx.addEventListener(window, 'wxt:locationchange', () => {
      teardownListeners();
      initListeners();
      console.log('[OP Seller CS] Re-initialized after SPA navigation to', location.href);
      // Re-inject panel if removed by SPA navigation (Pitfall 5 from research)
      if (!document.body.contains(panel.container)) {
        document.body.appendChild(panel.container);
        document.body.appendChild(panel.toggle);
      }
      // Check if we're now on the Transfer List page (Phase 07.1)
      maybeStartTradeObserver();
    });

    // D-08 Fallback: MutationObserver on document.body for EA SPA container replacement
    // Shallow observe — only fires on direct children swap (EA SPA container swap)
    const observer = new MutationObserver(() => {
      maybeStartTradeObserver();
    });
    observer.observe(document.body, { childList: true, subtree: false });

    // EA SPA renders Transfer List deep in the DOM after page load.
    // Neither wxt:locationchange nor the shallow body observer catch it.
    // Poll every 2s until the trade observer activates, then stop polling.
    const pollId = ctx.setInterval(() => {
      if (isTransferListPage(document)) {
        console.log('[OP Seller CS] Transfer List detected via poll');
        maybeStartTradeObserver();
        clearInterval(pollId);
      }
    }, 2000);
    ctx.onInvalidated(() => observer.disconnect());

    // D-09: Auto-reconnect loop — retries until ctx is invalidated
    function tryReconnect() {
      if (ctx.isInvalid) return;
      chrome.runtime.sendMessage({ type: 'PING' } satisfies ExtensionMessage)
        .then(() => {
          console.log('[OP Seller CS] Connected to service worker');
        })
        .catch(() => {
          console.log('[OP Seller CS] Service worker not ready, retrying in 2s');
          ctx.setTimeout(tryReconnect, 2000);
        });
    }

    // Initial setup
    initListeners();
    tryReconnect();
    console.log('[OP Seller CS] Content script loaded');

    // Load confirmed portfolio on mount (D-11: backend is source of truth)
    // Guard: skip if content script is already invalidated before this runs
    if (!ctx.isInvalid) {
      chrome.runtime.sendMessage({ type: 'PORTFOLIO_LOAD' } satisfies ExtensionMessage)
        .then((res: ExtensionMessage) => {
          if (res.type === 'PORTFOLIO_LOAD_RESULT' && res.portfolio) {
            panel.setState('confirmed', {
              players: res.portfolio.players,
              budget: res.portfolio.budget,
              budget_used: res.portfolio.players.reduce((s, p) => s + p.price, 0),
              budget_remaining: 0,
            });
          }
          // If null, panel stays in EMPTY state
        })
        .catch(() => {
          // Service worker not ready — panel stays in EMPTY state
        });
    }

    // Initial trade observer check (user may have loaded directly on Transfer List page)
    maybeStartTradeObserver();
  },
});
