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

/**
 * Shape of a single player in the confirmed portfolio.
 * Mirrors the scored player dict from the backend optimizer.
 */
export type PortfolioPlayer = {
  ea_id: number;
  name: string;
  rating: number;
  position: string;
  price: number;          // buy_price
  sell_price: number;
  margin_pct: number;
  expected_profit: number;
  op_ratio: number;
  efficiency: number;
  futgg_url?: string | null;
};

/**
 * A portfolio that the user has confirmed — stored locally so the overlay
 * can display it immediately without a backend round-trip.
 */
export type ConfirmedPortfolio = {
  players: PortfolioPlayer[];
  budget: number;
  confirmed_at: string;  // ISO timestamp
};

/**
 * Confirmed portfolio persisted across service worker termination.
 * Written on PORTFOLIO_CONFIRM success, read by PORTFOLIO_LOAD handler.
 */
export const portfolioItem = storage.defineItem<ConfirmedPortfolio | null>(
  'local:portfolio',
  { fallback: null },
);

/**
 * Deduplication set for reported trade outcomes (D-07).
 * Keys are composite: "{ea_id}:{outcome}:{price}" to uniquely identify events.
 * Checked before every report; written after successful report.
 * Survives service worker termination and page refreshes.
 */
export const reportedOutcomesItem = storage.defineItem<string[]>(
  'local:reportedOutcomes',
  { fallback: [] },
);
