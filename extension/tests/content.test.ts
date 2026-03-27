/**
 * Unit tests for the EA Web App content script.
 * Uses WxtVitest + fakeBrowser for in-memory chrome.* API mocks.
 * Tests cover: PING/PONG message handling, assertNever exhaustiveness,
 * SPA re-initialization on locationchange, and auto-reconnect loop.
 *
 * Note: The content script uses defineContentScript — the exported default
 * has a main(ctx) function that must be called with a mock ContentScriptContext.
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { fakeBrowser } from 'wxt/testing';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

// Resolve the content script source path relative to this test file
const __dirname = dirname(fileURLToPath(import.meta.url));
const CONTENT_SCRIPT_PATH = resolve(__dirname, '../entrypoints/ea-webapp.content.ts');

/**
 * Track registered wxt:locationchange handlers so we can clean them up between tests.
 */
const registeredLocationChangeHandlers: EventListener[] = [];

/**
 * Minimal mock for WXT ContentScriptContext.
 * Provides ctx.isInvalid, ctx.setTimeout, ctx.onInvalidated, ctx.addEventListener.
 */
function createMockCtx(overrides: { isInvalid?: boolean } = {}) {
  const state = {
    isInvalid: overrides.isInvalid ?? false,
    invalidatedCallbacks: [] as (() => void)[],
    timeoutCalls: [] as Array<{ fn: () => void; delay: number }>,
  };

  return {
    get isInvalid() {
      return state.isInvalid;
    },
    onInvalidated(cb: () => void) {
      state.invalidatedCallbacks.push(cb);
    },
    addEventListener(target: EventTarget, type: string, listener: EventListener) {
      target.addEventListener(type, listener);
      // Track locationchange handlers for cleanup
      if (type === 'wxt:locationchange') {
        registeredLocationChangeHandlers.push(listener);
      }
    },
    setTimeout(fn: () => void, delay: number) {
      state.timeoutCalls.push({ fn, delay });
      // Do NOT auto-execute — tests verify timeout was scheduled
    },
    setInterval(fn: () => void, _delay: number) {
      // Return a fake interval ID — tests don't need real polling
      return 0 as unknown as ReturnType<typeof setInterval>;
    },
    _state: state,
    _invalidate() {
      state.isInvalid = true;
      state.invalidatedCallbacks.forEach(cb => cb());
    },
  };
}

describe('content script message handling', () => {
  beforeEach(() => {
    fakeBrowser.reset();
    vi.restoreAllMocks();
    vi.resetModules();
    // Clean up any wxt:locationchange listeners from previous tests
    registeredLocationChangeHandlers.splice(0).forEach(handler => {
      window.removeEventListener('wxt:locationchange', handler);
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns PONG when receiving PING message', async () => {
    // Spy on addListener to capture the registered handleMessage function
    const addListenerSpy = vi.spyOn(fakeBrowser.runtime.onMessage, 'addListener');

    const mod = await import('../entrypoints/ea-webapp.content');
    const ctx = createMockCtx();
    mod.default.main(ctx as any);

    // The first addListener call registers handleMessage
    const registeredHandler = addListenerSpy.mock.calls[0]?.[0];
    expect(registeredHandler).toBeDefined();

    // Call the handler directly simulating Chrome's message dispatch
    // Chrome passes (message, sender, sendResponse) — sendResponse is a function
    const sendResponse = vi.fn();
    const returnValue = registeredHandler(
      { type: 'PING' },
      {} as chrome.runtime.MessageSender,
      sendResponse,
    );

    // Handler should call sendResponse with PONG
    expect(sendResponse).toHaveBeenCalledWith({ type: 'PONG' });
    // Handler returns true to signal async response
    expect(returnValue).toBe(true);
  });

  it('has assertNever in the default branch for exhaustive switch', () => {
    // Structural (source-level) test — assertNever must appear in the default switch branch
    const source = readFileSync(CONTENT_SCRIPT_PATH, 'utf-8');
    expect(source).toContain('assertNever(msg)');
    // Verify it appears in a default case context
    expect(source).toMatch(/default:\s*\n?\s*assertNever\(msg\)/);
  });

  it('re-initializes message listeners on SPA navigation (wxt:locationchange)', async () => {
    // Spy on fakeBrowser.runtime.onMessage before calling main
    const addListenerSpy = vi.spyOn(fakeBrowser.runtime.onMessage, 'addListener');
    const removeListenerSpy = vi.spyOn(fakeBrowser.runtime.onMessage, 'removeListener');

    const mod = await import('../entrypoints/ea-webapp.content');
    const ctx = createMockCtx();
    mod.default.main(ctx as any);

    // At this point: initListeners() was called once during main() → addListener count = 1
    // Reset the spy counts to baseline (ignoring initial setup calls)
    addListenerSpy.mockClear();
    removeListenerSpy.mockClear();

    // Simulate SPA navigation — triggers the wxt:locationchange handler
    window.dispatchEvent(new Event('wxt:locationchange'));

    // After one navigation: teardownListeners() calls removeListener once,
    // then initListeners() calls addListener once
    expect(removeListenerSpy).toHaveBeenCalledTimes(1);
    expect(addListenerSpy).toHaveBeenCalledTimes(1);
  });

  it('stops reconnect loop when ctx.isInvalid is true', async () => {
    const mod = await import('../entrypoints/ea-webapp.content');
    const ctx = createMockCtx({ isInvalid: true });

    const sendMessageSpy = vi.spyOn(fakeBrowser.runtime, 'sendMessage');

    mod.default.main(ctx as any);

    // Allow microtasks to settle
    await new Promise((r) => setTimeout(r, 10));

    // sendMessage should NOT be called when ctx.isInvalid === true
    expect(sendMessageSpy).not.toHaveBeenCalled();
  });

  it('schedules retry via ctx.setTimeout when sendMessage fails and ctx is valid', async () => {
    const mod = await import('../entrypoints/ea-webapp.content');
    const ctx = createMockCtx({ isInvalid: false });
    const setTimeoutSpy = vi.spyOn(ctx, 'setTimeout');

    // Make sendMessage reject (service worker not ready)
    vi.spyOn(fakeBrowser.runtime, 'sendMessage').mockRejectedValueOnce(
      new Error('Could not establish connection'),
    );

    mod.default.main(ctx as any);

    // Allow the rejected promise to settle
    await new Promise((r) => setTimeout(r, 50));

    // ctx.setTimeout should have been called to schedule a 2s retry
    expect(setTimeoutSpy).toHaveBeenCalledTimes(1);
    const [retryFn, retryDelay] = setTimeoutSpy.mock.calls[0];
    expect(typeof retryFn).toBe('function');
    expect(retryDelay).toBe(2000);
  });

  it('returns false (not handled) when content script receives PORTFOLIO_GENERATE request type', async () => {
    // PORTFOLIO_GENERATE is a request type sent TO the service worker.
    // Content script switch handles it with return false — no error thrown.
    const addListenerSpy = vi.spyOn(fakeBrowser.runtime.onMessage, 'addListener');

    const mod = await import('../entrypoints/ea-webapp.content');
    const ctx = createMockCtx();
    mod.default.main(ctx as any);

    const registeredHandler = addListenerSpy.mock.calls[0]?.[0];
    expect(registeredHandler).toBeDefined();

    const sendResponse = vi.fn();
    const returnValue = registeredHandler(
      { type: 'PORTFOLIO_GENERATE', budget: 100000 },
      {} as chrome.runtime.MessageSender,
      sendResponse,
    );

    // Handler returns false — message not handled by content script
    expect(returnValue).toBe(false);
    // sendResponse should NOT be called
    expect(sendResponse).not.toHaveBeenCalled();
  });

  it('returns false (not handled) when content script receives PORTFOLIO_LOAD_RESULT response type', async () => {
    // PORTFOLIO_LOAD_RESULT is a response type returned from service worker sendMessage calls,
    // not dispatched via onMessage to the content script.
    const addListenerSpy = vi.spyOn(fakeBrowser.runtime.onMessage, 'addListener');

    const mod = await import('../entrypoints/ea-webapp.content');
    const ctx = createMockCtx();
    mod.default.main(ctx as any);

    const registeredHandler = addListenerSpy.mock.calls[0]?.[0];
    const sendResponse = vi.fn();
    const returnValue = registeredHandler(
      { type: 'PORTFOLIO_LOAD_RESULT', portfolio: null },
      {} as chrome.runtime.MessageSender,
      sendResponse,
    );

    expect(returnValue).toBe(false);
    expect(sendResponse).not.toHaveBeenCalled();
  });

  it('returns false for TRADE_REPORT request type (sent to service worker)', async () => {
    // TRADE_REPORT is a request type sent TO the service worker.
    // Content script switch handles it with return false — no error thrown.
    const addListenerSpy = vi.spyOn(fakeBrowser.runtime.onMessage, 'addListener');

    const mod = await import('../entrypoints/ea-webapp.content');
    const ctx = createMockCtx();
    mod.default.main(ctx as any);

    const registeredHandler = addListenerSpy.mock.calls[0]?.[0];
    expect(registeredHandler).toBeDefined();

    const sendResponse = vi.fn();
    const returnValue = registeredHandler(
      { type: 'TRADE_REPORT', ea_id: 12345, price: 15000, outcome: 'sold' },
      {} as chrome.runtime.MessageSender,
      sendResponse,
    );

    expect(returnValue).toBe(false);
    expect(sendResponse).not.toHaveBeenCalled();
  });

  it('returns false for TRADE_REPORT_RESULT response type', async () => {
    // TRADE_REPORT_RESULT is a response type from service worker — not dispatched via onMessage.
    const addListenerSpy = vi.spyOn(fakeBrowser.runtime.onMessage, 'addListener');

    const mod = await import('../entrypoints/ea-webapp.content');
    const ctx = createMockCtx();
    mod.default.main(ctx as any);

    const registeredHandler = addListenerSpy.mock.calls[0]?.[0];
    const sendResponse = vi.fn();
    const returnValue = registeredHandler(
      { type: 'TRADE_REPORT_RESULT', success: true },
      {} as chrome.runtime.MessageSender,
      sendResponse,
    );

    expect(returnValue).toBe(false);
    expect(sendResponse).not.toHaveBeenCalled();
  });
});
