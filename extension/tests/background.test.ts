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
import { enabledItem, lastActionItem, portfolioItem, algoMasterStateItem } from '../src/storage';

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

// ── Portfolio message handler tests ───────────────────────────────────────────

const MOCK_PORTFOLIO_PLAYERS = [
  {
    ea_id: 111,
    name: 'Test Player',
    rating: 88,
    position: 'ST',
    price: 20000,
    sell_price: 28000,
    margin_pct: 0.4,
    expected_profit: 5000,
    op_ratio: 0.3,
    efficiency: 0.25,
  },
];

/**
 * Capture the portfolio onMessage listener registered by background.ts main().
 * The listener is registered after alarm setup so we need the spy in place before main() runs.
 */
async function runBackgroundAndCapture(): Promise<{
  portfolioListener: (msg: any, sender: any, sendResponse: (r: any) => void) => boolean | void;
}> {
  vi.resetModules();
  const addListenerSpy = vi.spyOn(fakeBrowser.runtime.onMessage, 'addListener');

  const mod = await import('../entrypoints/background');
  const bg = mod.default;
  bg.main();
  await new Promise((r) => setTimeout(r, 50));

  // The portfolio listener is the last one registered (after alarm listener)
  const allCalls = addListenerSpy.mock.calls;
  const portfolioListener = allCalls[allCalls.length - 1]?.[0] as any;
  return { portfolioListener };
}

describe('portfolio message handlers', () => {
  beforeEach(async () => {
    fakeBrowser.reset();
    vi.restoreAllMocks();
    await enabledItem.setValue(false);
    await lastActionItem.setValue(null);
    await portfolioItem.setValue(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('PORTFOLIO_GENERATE proxies POST to backend and returns PORTFOLIO_GENERATE_RESULT', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        data: MOCK_PORTFOLIO_PLAYERS.map(p => ({ ...p, buy_price: p.price })),
        budget_used: 20000,
        budget_remaining: 80000,
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { portfolioListener } = await runBackgroundAndCapture();
    expect(portfolioListener).toBeDefined();

    const sendResponse = vi.fn();
    const returnVal = portfolioListener({ type: 'PORTFOLIO_GENERATE', budget: 100000 }, {}, sendResponse);

    // Handler must return true for async response
    expect(returnVal).toBe(true);

    // Allow the async promise to resolve
    await new Promise((r) => setTimeout(r, 50));

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/portfolio/generate',
      expect.objectContaining({ method: 'POST' }),
    );
    expect(sendResponse).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'PORTFOLIO_GENERATE_RESULT', budget_used: 20000 }),
    );
  });

  it('PORTFOLIO_GENERATE with banned_ea_ids passes them in POST body to backend', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        data: MOCK_PORTFOLIO_PLAYERS.map(p => ({ ...p, buy_price: p.price })),
        budget_used: 20000,
        budget_remaining: 80000,
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { portfolioListener } = await runBackgroundAndCapture();
    expect(portfolioListener).toBeDefined();

    const sendResponse = vi.fn();
    const returnVal = portfolioListener(
      { type: 'PORTFOLIO_GENERATE', budget: 100000, banned_ea_ids: [111, 222] },
      {},
      sendResponse,
    );

    expect(returnVal).toBe(true);
    await new Promise((r) => setTimeout(r, 50));

    // The POST body must include banned_ea_ids
    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/portfolio/generate',
      expect.objectContaining({
        method: 'POST',
        body: expect.stringContaining('"banned_ea_ids":[111,222]'),
      }),
    );
    expect(sendResponse).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'PORTFOLIO_GENERATE_RESULT' }),
    );
  });

  it('PORTFOLIO_CONFIRM proxies POST to backend and stores portfolio in portfolioItem', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ confirmed: 1 }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { portfolioListener } = await runBackgroundAndCapture();

    const sendResponse = vi.fn();
    const returnVal = portfolioListener({ type: 'PORTFOLIO_CONFIRM', players: MOCK_PORTFOLIO_PLAYERS }, {}, sendResponse);
    expect(returnVal).toBe(true);

    await new Promise((r) => setTimeout(r, 50));

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/portfolio/confirm',
      expect.objectContaining({ method: 'POST' }),
    );

    // portfolioItem should be stored
    const stored = await portfolioItem.getValue();
    expect(stored).not.toBeNull();
    expect(stored?.players).toHaveLength(1);
    expect(stored?.players[0].ea_id).toBe(111);

    expect(sendResponse).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'PORTFOLIO_CONFIRM_RESULT', confirmed: 1 }),
    );
  });

  it('PORTFOLIO_LOAD returns the stored portfolio from portfolioItem', async () => {
    const storedPortfolio = {
      players: MOCK_PORTFOLIO_PLAYERS,
      budget: 20000,
      confirmed_at: '2026-03-27T00:00:00.000Z',
    };
    await portfolioItem.setValue(storedPortfolio);

    // fetch mock not needed for PORTFOLIO_LOAD — it reads from storage
    vi.stubGlobal('fetch', vi.fn());

    const { portfolioListener } = await runBackgroundAndCapture();

    const sendResponse = vi.fn();
    const returnVal = portfolioListener({ type: 'PORTFOLIO_LOAD' }, {}, sendResponse);
    expect(returnVal).toBe(true);

    await new Promise((r) => setTimeout(r, 50));

    expect(sendResponse).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'PORTFOLIO_LOAD_RESULT',
        portfolio: expect.objectContaining({ budget: 20000 }),
      }),
    );
  });

  it('PORTFOLIO_GENERATE returns error field when fetch throws', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network error')));

    const { portfolioListener } = await runBackgroundAndCapture();

    const sendResponse = vi.fn();
    portfolioListener({ type: 'PORTFOLIO_GENERATE', budget: 50000 }, {}, sendResponse);

    await new Promise((r) => setTimeout(r, 50));

    expect(sendResponse).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'PORTFOLIO_GENERATE_RESULT',
        error: expect.stringContaining('Network error'),
        data: [],
      }),
    );
  });
});

// ── Trade report handler tests ─────────────────────────────────────────────

describe('trade report handler', () => {
  beforeEach(async () => {
    fakeBrowser.reset();
    vi.restoreAllMocks();
    await enabledItem.setValue(false);
    await lastActionItem.setValue(null);
    await portfolioItem.setValue(null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('TRADE_REPORT handler calls POST /trade-records/direct with correct body and returns TRADE_REPORT_RESULT success=true', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'ok', trade_record_id: 42 }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const { portfolioListener } = await runBackgroundAndCapture();
    expect(portfolioListener).toBeDefined();

    const sendResponse = vi.fn();
    const returnVal = portfolioListener(
      { type: 'TRADE_REPORT', ea_id: 12345, price: 15000, outcome: 'sold' },
      {},
      sendResponse,
    );

    // Handler must return true for async response
    expect(returnVal).toBe(true);

    // Allow the async promise to resolve
    await new Promise((r) => setTimeout(r, 50));

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8000/api/v1/trade-records/direct',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ ea_id: 12345, price: 15000, outcome: 'sold' }),
      }),
    );
    expect(sendResponse).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'TRADE_REPORT_RESULT', success: true }),
    );
  });

  it('TRADE_REPORT handler returns TRADE_REPORT_RESULT success=false when backend returns error', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      text: async () => 'ea_id 99999 not in portfolio',
    });
    vi.stubGlobal('fetch', fetchMock);

    const { portfolioListener } = await runBackgroundAndCapture();

    const sendResponse = vi.fn();
    portfolioListener(
      { type: 'TRADE_REPORT', ea_id: 99999, price: 5000, outcome: 'expired' },
      {},
      sendResponse,
    );

    await new Promise((r) => setTimeout(r, 50));

    expect(sendResponse).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'TRADE_REPORT_RESULT',
        success: false,
        error: expect.stringContaining('Backend error'),
      }),
    );
  });

  it('TRADE_REPORT handler returns TRADE_REPORT_RESULT success=false on network error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Network failure')));

    const { portfolioListener } = await runBackgroundAndCapture();

    const sendResponse = vi.fn();
    portfolioListener(
      { type: 'TRADE_REPORT', ea_id: 12345, price: 15000, outcome: 'listed' },
      {},
      sendResponse,
    );

    await new Promise((r) => setTimeout(r, 50));

    expect(sendResponse).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'TRADE_REPORT_RESULT',
        success: false,
        error: expect.stringContaining('Network failure'),
      }),
    );
  });
});

// ── Mode selection via message handlers tests ─────────────────────────────────

describe('mode selection via message handlers', () => {
  beforeEach(async () => {
    fakeBrowser.reset();
    vi.restoreAllMocks();
    // Stub backend + master-side chrome calls so handlers can run
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ budget: 100000, cash: 100000 }),
    }) as any;
    vi.spyOn(chrome.tabs, 'query').mockResolvedValue([] as any);
    vi.spyOn(chrome.tabs, 'create').mockResolvedValue({ id: 1, status: 'complete' } as any);
    vi.spyOn(chrome.tabs, 'get').mockResolvedValue({ id: 1, url: 'https://www.ea.com/ea-sports-fc/ultimate-team/web-app/', status: 'complete' } as any);
    vi.spyOn(chrome.tabs, 'sendMessage').mockResolvedValue({ type: 'PONG' } as any);
  });

  it('ALGO_START sets mode to "algo" on master state', async () => {
    const { portfolioListener } = await runBackgroundAndCapture();

    const sendResponse = vi.fn();
    const returnVal = portfolioListener({ type: 'ALGO_START', budget: 100000 }, {}, sendResponse);
    expect(returnVal).toBe(true);

    await new Promise(r => setTimeout(r, 50));

    const state = await algoMasterStateItem.getValue();
    expect(state.mode).toBe('algo');
  });

  it('AUTOMATION_START sets mode to "op-selling" on master state', async () => {
    const { portfolioListener } = await runBackgroundAndCapture();

    const sendResponse = vi.fn();
    const returnVal = portfolioListener({ type: 'AUTOMATION_START' }, {}, sendResponse);
    expect(returnVal).toBe(true);

    await new Promise(r => setTimeout(r, 50));

    const state = await algoMasterStateItem.getValue();
    expect(state.mode).toBe('op-selling');
  });
});
