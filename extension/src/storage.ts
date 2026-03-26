/**
 * Typed chrome.storage.local items for all state that must survive service worker termination.
 * All items defined centrally here to avoid scattered storage key strings across the codebase.
 */
import { storage } from 'wxt/utils/storage';

/**
 * Polling enabled/disabled gate (per D-03).
 * When false, alarm fires but maybePoll() returns early without fetching.
 * Phase 8 UI will wire the toggle; the gate mechanism exists from Phase 6.
 */
export const enabledItem = storage.defineItem<boolean>('local:enabled', {
  fallback: false,
});

/**
 * Shape of a pending action as returned by GET /api/v1/actions/pending.
 * Cached in storage so Phase 7 DOM automation can access it immediately on worker wake (D-02).
 */
export type PendingAction = {
  id: number;
  ea_id: number;
  action_type: 'BUY' | 'LIST' | 'RELIST';
  target_price: number;
  player_name: string;
};

/**
 * Last known pending action fetched from backend.
 * Survives service worker termination — Phase 7 reads this to drive DOM automation.
 */
export const lastActionItem = storage.defineItem<PendingAction | null>('local:lastAction', {
  fallback: null,
});
