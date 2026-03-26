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

/**
 * Minimal mock for WXT ContentScriptContext.
 * Provides ctx.isInvalid, ctx.setTimeout, ctx.onInvalidated, ctx.addEventListener.
 */
function createMockCtx(overrides: Partial<{
  isInvalid: boolean;
  onInvalidatedCallback: (() => void) | null;
}> = {}) {
  const state = {
    isInvalid: overrides.isInvalid ?? false,
    invalidatedCallbacks: [] as (() => void)[],
    eventListeners: [] as Array<{ target: EventTarget; type: string; listener: EventListener }>,
    timeouts: [] as Array<{ fn: () => void; delay: number }>,
  };

  return {
    get isInvalid() {
      return state.isInvalid;
    },
    onInvalidated(cb: () => void) {
      state.invalidatedCallbacks.push(cb);
    },
    addEventListener(target: EventTarget, type: string, listener: EventListener) {
      state.eventListeners.push({ target, type, listener });
      target.addEventListener(type, listener);
    },
    setTimeout(fn: () => void, delay: number) {
      state.timeouts.push({ fn, delay });
      // Execute immediately in tests (don't actually wait)
      // Tests verify the timeout was scheduled, not that it fires
    },
    // Expose internals for assertions
    _state: state,
    // Invalidate the context
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
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns PONG when receiving PING message', async () => {
    // Import the content script
    const mod = await import('../entrypoints/ea-webapp.content');
    const cs = mod.default;

    const ctx = createMockCtx();
    // Call main with mock ctx
    cs.main(ctx as any);

    // Simulate receiving a PING via chrome.runtime.onMessage
    let capturedResponse: any = undefined;
    const sendResponse = vi.fn((res: any) => { capturedResponse = res; });

    // fakeBrowser tracks listeners — trigger onMessage with PING
    const listeners = fakeBrowser.runtime.onMessage.addListener.mock?.calls ?? [];
    // Use the real chrome.runtime.onMessage dispatch mechanism
    // In jsdom, we can invoke registered listeners directly
    const onMessageListeners = (fakeBrowser.runtime.onMessage as any)._listeners ?? [];

    // Dispatch message through the fakeBrowser
    await fakeBrowser.runtime.onMessage.callListeners(
      { type: 'PING' },
      { id: 'test-sender' } as chrome.runtime.MessageSender,
      sendResponse,
    );

    expect(sendResponse).toHaveBeenCalledWith({ type: 'PONG' });
  });

  it('has assertNever in the default branch for exhaustive switch', async () => {
    // This is a structural (source-level) test — assertNever must appear in the switch default
    const fs = await import('node:fs');
    const source = fs.readFileSync(
      new URL('../entrypoints/ea-webapp.content.ts', import.meta.url).pathname.replace(/^\/([A-Z]:)/, '$1'),
      'utf-8'
    );
    expect(source).toContain('assertNever(msg)');
    // Verify it's in a default case context
    expect(source).toMatch(/default:\s*\n?\s*assertNever\(msg\)/);
  });

  it('re-initializes message listeners on SPA navigation (wxt:locationchange)', async () => {
    const mod = await import('../entrypoints/ea-webapp.content');
    const cs = mod.default;

    const ctx = createMockCtx();
    cs.main(ctx as any);

    // Track how many times onMessage.addListener and removeListener are called
    const addListenerSpy = vi.spyOn(fakeBrowser.runtime.onMessage, 'addListener');
    const removeListenerSpy = vi.spyOn(fakeBrowser.runtime.onMessage, 'removeListener');

    // Simulate SPA navigation by dispatching the wxt:locationchange event on window
    const locationChangeEvent = new Event('wxt:locationchange');
    window.dispatchEvent(locationChangeEvent);

    // After navigation: removeListener called once (teardown), addListener called once (re-init)
    expect(removeListenerSpy).toHaveBeenCalledTimes(1);
    expect(addListenerSpy).toHaveBeenCalledTimes(1);
  });

  it('stops reconnect loop when ctx.isInvalid is true', async () => {
    const mod = await import('../entrypoints/ea-webapp.content');
    const cs = mod.default;

    // Create a ctx that starts as invalid
    const ctx = createMockCtx({ isInvalid: true });

    // Mock sendMessage to track if it's called
    const sendMessageSpy = vi.spyOn(fakeBrowser.runtime, 'sendMessage');

    cs.main(ctx as any);

    // Allow any microtasks to settle
    await new Promise((r) => setTimeout(r, 10));

    // sendMessage should NOT be called when ctx.isInvalid === true
    expect(sendMessageSpy).not.toHaveBeenCalled();
  });

  it('schedules retry when sendMessage fails and ctx is still valid', async () => {
    const mod = await import('../entrypoints/ea-webapp.content');
    const cs = mod.default;

    const ctx = createMockCtx({ isInvalid: false });
    const setTimeoutSpy = vi.spyOn(ctx, 'setTimeout');

    // Make sendMessage reject (service worker not ready)
    vi.spyOn(fakeBrowser.runtime, 'sendMessage').mockRejectedValueOnce(
      new Error('Could not establish connection'),
    );

    cs.main(ctx as any);

    // Allow the rejected promise to settle
    await new Promise((r) => setTimeout(r, 50));

    // ctx.setTimeout should have been called to schedule a retry
    expect(setTimeoutSpy).toHaveBeenCalled();
    const [retryFn, retryDelay] = setTimeoutSpy.mock.calls[0];
    expect(typeof retryFn).toBe('function');
    expect(retryDelay).toBe(2000);
  });
});
