import { describe, it, expect, beforeEach } from 'vitest';
import { fakeBrowser } from 'wxt/testing';
import { algoMasterStateItem } from '../src/storage';

describe('algoMasterStateItem', () => {
  beforeEach(() => {
    fakeBrowser.reset();
  });

  it('includes mode field defaulting to "algo"', async () => {
    const state = await algoMasterStateItem.getValue();
    expect(state.mode).toBe('algo');
  });

  it('persists mode changes', async () => {
    const state = await algoMasterStateItem.getValue();
    await algoMasterStateItem.setValue({ ...state, mode: 'op-selling' });
    const reloaded = await algoMasterStateItem.getValue();
    expect(reloaded.mode).toBe('op-selling');
  });
});
