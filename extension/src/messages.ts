/**
 * Shared discriminated union message types for service worker <-> content script communication.
 * Phase 6 defines PING/PONG to prove the channel works.
 * Phase 7 adds PORTFOLIO_* types for generate, confirm, swap, and load operations.
 * Phase 07.2 adds DASHBOARD_STATUS_* types for the portfolio dashboard panel.
 * Phase 08 adds AUTOMATION_*, DAILY_CAP_*, FRESH_PRICE_* types for the automation engine.
 */
import type { PortfolioPlayer, ConfirmedPortfolio } from './storage';

/** Per-player trade status row returned by GET /portfolio/status. */
export type DashboardPlayer = {
  ea_id: number;
  name: string;
  futgg_url: string | null;
  status: 'PENDING' | 'BOUGHT' | 'LISTED' | 'SOLD' | 'EXPIRED';
  times_sold: number;
  realized_profit: number;
  unrealized_pnl: number | null;
  buy_price: number;
  sell_price: number;
  current_bin: number | null;
  is_leftover: boolean;
};

/** Full response shape from GET /portfolio/status. */
export type DashboardData = {
  summary: {
    realized_profit: number;
    unrealized_pnl: number;
    trade_counts: { bought: number; sold: number; expired: number };
  };
  players: DashboardPlayer[];
};

/**
 * Automation engine status snapshot.
 * Returned by AUTOMATION_STATUS_RESULT and stored in automationStatusItem.
 */
export type AutomationStatusData = {
  isRunning: boolean;
  state: 'IDLE' | 'BUYING' | 'LISTING' | 'SCANNING' | 'RELISTING' | 'STOPPED' | 'ERROR';
  currentAction: string | null;
  lastEvent: string | null;
  sessionProfit: number;
  errorMessage: string | null;
};

/** A single action item from GET /portfolio/actions-needed. */
export type ActionNeeded = {
  ea_id: number;
  name: string;
  rating: number;
  position: string;
  card_type: string;
  action: 'BUY' | 'LIST' | 'RELIST' | 'WAIT';
  target_price: number;
  buy_price: number;
  sell_price: number;
  is_leftover: boolean;
  futgg_url: string | null;
};

/** Full response shape from GET /portfolio/actions-needed. */
export type ActionsNeededData = {
  actions: ActionNeeded[];
  summary: { to_buy: number; to_list: number; to_relist: number; waiting: number };
};

export type ExtensionMessage =
  | { type: 'PING' }
  | { type: 'PONG' }
  // Portfolio operations (content script -> service worker -> backend)
  | { type: 'PORTFOLIO_GENERATE'; budget: number }
  | { type: 'PORTFOLIO_GENERATE_RESULT'; data: PortfolioPlayer[]; budget_used: number; budget_remaining: number; error?: string }
  | { type: 'PORTFOLIO_CONFIRM'; players: PortfolioPlayer[] }
  | { type: 'PORTFOLIO_CONFIRM_RESULT'; confirmed: number; error?: string }
  | { type: 'PORTFOLIO_SWAP'; ea_id: number; freed_budget: number; excluded_ea_ids: number[]; current_count: number }
  | { type: 'PORTFOLIO_SWAP_RESULT'; replacements: PortfolioPlayer[]; error?: string }
  | { type: 'PORTFOLIO_LOAD' }
  | { type: 'PORTFOLIO_LOAD_RESULT'; portfolio: ConfirmedPortfolio | null }
  // Trade reporting (Phase 07.1: passive DOM reading → backend relay)
  | { type: 'TRADE_REPORT'; ea_id: number; price: number; outcome: 'bought' | 'listed' | 'sold' | 'expired' }
  | { type: 'TRADE_REPORT_RESULT'; success: boolean; error?: string }
  | { type: 'TRADE_REPORT_BATCH'; reports: Array<{ ea_id: number; price: number; outcome: 'bought' | 'listed' | 'sold' | 'expired' }> }
  | { type: 'TRADE_REPORT_BATCH_RESULT'; succeeded: number[]; failed: number[]; error?: string }
  // Dashboard status (Phase 07.2: portfolio dashboard)
  | { type: 'DASHBOARD_STATUS_REQUEST' }
  | { type: 'DASHBOARD_STATUS_RESULT'; data: DashboardData | null; error?: string }
  // Actions needed (unified buy/list/relist view)
  | { type: 'ACTIONS_NEEDED_REQUEST' }
  | { type: 'ACTIONS_NEEDED_RESULT'; data: ActionsNeededData | null; error?: string }
  // Automation control (overlay panel -> content script -> service worker)
  | { type: 'AUTOMATION_START' }
  | { type: 'AUTOMATION_START_RESULT'; success: boolean; error?: string }
  | { type: 'AUTOMATION_STOP' }
  | { type: 'AUTOMATION_STOP_RESULT'; success: boolean }
  | { type: 'AUTOMATION_STATUS_REQUEST' }
  | { type: 'AUTOMATION_STATUS_RESULT'; status: AutomationStatusData }
  // Daily cap (content script -> service worker -> backend)
  | { type: 'DAILY_CAP_REQUEST' }
  | { type: 'DAILY_CAP_RESULT'; count: number; cap: number; capped: boolean; error?: string }
  | { type: 'DAILY_CAP_INCREMENT' }
  | { type: 'DAILY_CAP_INCREMENT_RESULT'; count: number; cap: number; capped: boolean; error?: string }
  // Fresh price lookup (content script -> service worker -> backend)
  | { type: 'FRESH_PRICE_REQUEST'; ea_id: number }
  | { type: 'FRESH_PRICE_RESULT'; ea_id: number; buy_price: number; sell_price: number; error?: string };

/**
 * Compile-time exhaustiveness helper for switch statements over ExtensionMessage.
 * Add to the default branch of any switch on msg.type — TypeScript will emit a
 * compile error if a new message variant is added but not handled.
 */
export function assertNever(x: never): never {
  throw new Error(`Unhandled message type: ${(x as any).type}`);
}
