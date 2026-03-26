/**
 * Unit tests for the background service worker.
 * Uses WxtVitest + fakeBrowser for in-memory chrome.* API mocks.
 * Tests cover: alarm creation, alarm idempotency, polling gate, immediate wake poll,
 * action storage, and error handling.
 *
 * Note: WXT's defineBackground() returns an object with a main() function —
 * it does not auto-call main(). Tests must call main() directly.
 */
import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { fakeBrowser } from 'wxt/testing';
import { enabledItem, lastActionItem } from '../src/storage';

const MOCK_ACTION = {
  id: 1,
  ea_id: 12345,
  action_type: 'BUY' as const,
  target_price: 15000,
  player_name: 'Player 12345',
};

async function runBackground() {
  // Reset module cache so each test gets fresh imports
  vi.resetModules();
  const mod = await import('../entrypoints/background');
  const bg = mod.default;
  // Call main() — WXT defineBackground returns { main, type } but doesn't auto-execute
  bg.main();
  // Allow the alarm.get callback and immediate maybePoll() to complete
  await new Promise((r) => setTimeout(r, 50));
}

describe('poll alarm', () => {
  beforeEach(() => {
    fakeBrowser.reset();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('creates poll alarm on startup with periodInMinutes=1 when no alarm exists', async () => {
    await runBackground();
    const alarm = await fakeBrowser.alarms.get('poll');
    expect(alarm).toBeDefined();
    expect(alarm?.periodInMinutes).toBe(1);
  });

  it('does NOT re-create poll alarm if it already exists', async () => {
    // Pre-create the alarm
    await fakeBrowser.alarms.create('poll', { periodInMinutes: 1 });
    const createSpy = vi.spyOn(fakeBrowser.alarms, 'create');

    await runBackground();

    // create should not have been called since alarm already exists
    expect(createSpy).not.toHaveBeenCalled();
  });
});

describe('maybePoll', () => {
  beforeEach(async () => {
    fakeBrowser.reset();
    vi.restoreAllMocks();
    // Reset storage to defaults
    await enabledItem.setValue(false);
    await lastActionItem.setValue(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('fetches from backend when enabled=true and stores action', async () => {
    await enabledItem.setValue(true);

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ action: MOCK_ACTION }),
    });
    vi.stubGlobal('fetch', fetchMock);

    await runBackground();

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/actions/pending',
    );

    const stored = await lastActionItem.getValue();
    expect(stored).toEqual(MOCK_ACTION);
  });

  it('skips fetch when enabled=false', async () => {
    await enabledItem.setValue(false);

    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    await runBackground();

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('stores fetched action in lastActionItem', async () => {
    await enabledItem.setValue(true);

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ action: MOCK_ACTION }),
    }));

    await runBackground();

    const stored = await lastActionItem.getValue();
    expect(stored).not.toBeNull();
    expect(stored?.id).toBe(1);
    expect(stored?.action_type).toBe('BUY');
    expect(stored?.ea_id).toBe(12345);
  });

  it('handles fetch error gracefully without throwing', async () => {
    await enabledItem.setValue(true);

    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network error')));

    // Should NOT throw
    await expect(runBackground()).resolves.not.toThrow();

    expect(errorSpy).toHaveBeenCalled();
  });

  it('does not store action when fetch returns non-ok response', async () => {
    await enabledItem.setValue(true);

    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
    }));

    await runBackground();

    const stored = await lastActionItem.getValue();
    expect(stored).toBeNull();
  });
});
