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
 */
import { ExtensionMessage, assertNever } from '../src/messages';

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

    // Primary SPA detection: WXT locationchange fires on History API navigation (D-04/D-08)
    ctx.addEventListener(window, 'wxt:locationchange', () => {
      teardownListeners();
      initListeners();
      console.log('[OP Seller CS] Re-initialized after SPA navigation to', location.href);
    });

    // D-08 Fallback: MutationObserver on document.body for EA SPA container replacement
    // Shallow observe — only fires on direct children swap (EA SPA container swap)
    const observer = new MutationObserver(() => {
      console.log('[OP Seller CS] DOM mutation detected on body');
    });
    observer.observe(document.body, { childList: true, subtree: false });
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
  },
});
