/**
 * Tests for algo-master state management, focusing on the upgrade migration
 * that backfills a missing `mode` field in persisted AlgoMasterState.
 *
 * Uses WxtVitest + fakeBrowser for in-memory chrome.* API mocks.
 * Exercises loadState() indirectly through initAlgoMaster() + getMasterState().
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { fakeBrowser } from 'wxt/testing';
import { algoMasterStateItem } from '../src/storage';

// Storage key used by algoMasterStateItem
const STORAGE_KEY = 'algoMasterState';

describe('AlgoMasterState migration — mode field backfill', () => {
  beforeEach(() => {
    fakeBrowser.reset();
    vi.resetModules();
  });

  it('defaults mode to "algo" when persisted state has no mode field', async () => {
    // Simulate a persisted state from an older version (no `mode` field).
    // Write directly via chrome.storage.local to bypass WXT defineItem's fallback,
    // which only fires when the key is absent entirely.
    await chrome.storage.local.set({
      [STORAGE_KEY]: {
        status: 'IDLE',
        tabId: null,
        recoveryAttempts: 0,
        lastHealthCheck: null,
        errorMessage: null,
        // NO mode field — this is what an older persisted version looks like
      },
    });

    // Import algo-master fresh after raw storage is set.
    // initAlgoMaster() calls loadState() which performs the migration.
    const { initAlgoMaster, getMasterState } = await import('../src/algo-master');
    await initAlgoMaster();

    const state = getMasterState();
    expect(state.mode).toBe('algo');
  });

  it('persists the repaired mode to storage so subsequent reads do not need migration', async () => {
    // Seed raw state without mode
    await chrome.storage.local.set({
      [STORAGE_KEY]: {
        status: 'IDLE',
        tabId: null,
        recoveryAttempts: 0,
        lastHealthCheck: null,
        errorMessage: null,
      },
    });

    const { initAlgoMaster } = await import('../src/algo-master');
    await initAlgoMaster();

    // Verify storage was updated with mode so the next read gets it natively
    const result = await chrome.storage.local.get(STORAGE_KEY);
    const stored = result[STORAGE_KEY] as Record<string, unknown>;
    expect(stored?.mode).toBe('algo');
  });

  it('does not overwrite an explicit mode when mode is already set', async () => {
    await chrome.storage.local.set({
      [STORAGE_KEY]: {
        status: 'IDLE',
        tabId: null,
        recoveryAttempts: 0,
        lastHealthCheck: null,
        errorMessage: null,
        mode: 'op-selling',
      },
    });

    const { initAlgoMaster, getMasterState } = await import('../src/algo-master');
    await initAlgoMaster();

    const state = getMasterState();
    expect(state.mode).toBe('op-selling');
  });
});

describe('startAlgoMaster mode', () => {
  beforeEach(() => {
    fakeBrowser.reset();
    vi.restoreAllMocks();
  });

  it('persists the mode passed in when starting', async () => {
    vi.resetModules();
    vi.useFakeTimers();
    const mod = await import('../src/algo-master');
    // Stub chrome.tabs/chrome.scripting to avoid real spawn work
    vi.spyOn(chrome.tabs, 'query').mockResolvedValue([] as any);
    vi.spyOn(chrome.tabs, 'create').mockResolvedValue({ id: 1, status: 'complete' } as any);
    vi.spyOn(chrome.tabs, 'get').mockResolvedValue({ id: 1, url: 'https://www.ea.com/ea-sports-fc/ultimate-team/web-app/', status: 'complete' } as any);
    vi.spyOn(chrome.tabs, 'sendMessage').mockResolvedValue({ type: 'PONG' } as unknown as void);
    // Stub scripting so checkSessionViaTab returns quickly (session alive = true)
    vi.spyOn(chrome.scripting, 'executeScript').mockResolvedValue([{ result: true }] as unknown as void);

    // Start async work then advance fake timers past the 3×3s session-check delays
    const startPromise = mod.startAlgoMaster('op-selling');
    await vi.runAllTimersAsync();
    await startPromise;

    vi.useRealTimers();
    const state = await algoMasterStateItem.getValue();
    expect(state.mode).toBe('op-selling');
  });
});
