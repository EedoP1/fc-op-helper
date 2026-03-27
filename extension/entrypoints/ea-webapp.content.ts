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

    /**
     * Scan the Transfer List DOM for portfolio player outcomes.
     * Matches detected items against the confirmed portfolio by player name (D-03).
     * Reports new outcomes to the service worker for backend relay (D-06).
     * Deduplicates using reportedOutcomesItem (D-07).
     */
    async function scanTransferList() {
      const portfolio = await portfolioItem.getValue();
      if (!portfolio || portfolio.players.length === 0) return;

      const items = readTransferList(document);
      if (items.length === 0) return;

      // Match DOM items to portfolio using composite key:
      // name (endsWith) + rating + position + price
      // DOM shows short names ("Lo Celso") while portfolio has full names ("Giovani Lo Celso"),
      // so we use endsWith for name matching and rating+position to disambiguate.

      // Load dedup set
      const reported = new Set(await reportedOutcomesItem.getValue());

      for (const item of items) {
        const domName = item.playerName.toLowerCase();
        const match = portfolio.players.find(p =>
          p.name.toLowerCase().endsWith(domName) &&
          p.rating === item.rating &&
          p.position === item.position,
        );
        if (!match) continue; // Not a portfolio player (D-03)
        const player = { ea_id: match.ea_id };

        const dedupKey = `${player.ea_id}:${item.status}:${item.price}`;
        if (reported.has(dedupKey)) continue; // Already reported (D-07)

        // Report to service worker (D-06: silent auto-report)
        // Retry once on failure (service worker may still be waking up)
        for (let attempt = 0; attempt < 2; attempt++) {
          try {
            const response = await chrome.runtime.sendMessage({
              type: 'TRADE_REPORT',
              ea_id: player.ea_id,
              price: item.price,
              outcome: item.status,
            } satisfies ExtensionMessage);

            if (response && response.type === 'TRADE_REPORT_RESULT' && response.success) {
              reported.add(dedupKey);
              console.log(`[OP Seller CS] Reported ${item.status} for ${item.playerName} (${player.ea_id})`);
              break;
            }
            // Backend returned error (e.g. 404) — don't retry
            if (response && response.type === 'TRADE_REPORT_RESULT' && !response.success) {
              console.warn(`[OP Seller CS] Backend rejected ${item.playerName}: ${response.error}`);
              break;
            }
          } catch (e) {
            if (attempt === 0) {
              // First failure — wait 1s for service worker to wake up, then retry
              await new Promise(r => setTimeout(r, 1000));
            } else {
              console.error(`[OP Seller CS] Failed to report trade for ${item.playerName}:`, e);
            }
          }
        }
      }

      // Persist updated dedup set
      await reportedOutcomesItem.setValue([...reported]);
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

      tradeObserver = new MutationObserver(() => {
        // Re-scan on mutations (new items, status changes)
        scanTransferList();
      });

      // Observe subtree for child and character data changes (item status updates)
      tradeObserver.observe(container, { childList: true, subtree: true, characterData: true });
      console.log('[OP Seller CS] Trade observer activated on Transfer List');
    }

    // Clean up trade observer on invalidation
    ctx.onInvalidated(() => {
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
