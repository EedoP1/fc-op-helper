/**
 * MV3 Service Worker — OP Seller background entrypoint.
 *
 * Lifecycle: Chrome re-executes this module on every worker wake (alarm fire, message, etc.).
 * All state that must survive termination lives in chrome.storage.local (via storage items).
 *
 * Key behaviors:
 *   - D-01: Alarm-based polling at 1-minute intervals (alarms survive worker termination)
 *   - D-02: Immediate poll on wake (before resuming the alarm cycle)
 *   - D-03: Polling gated by enabled flag in storage (Phase 8 UI wires the toggle)
 *   - D-04: Backend URL hardcoded to localhost:8000 (v1.1 is localhost-only)
 */
import { enabledItem, lastActionItem, portfolioItem, PortfolioPlayer, ConfirmedPortfolio } from '../src/storage';
import { ExtensionMessage } from '../src/messages';

const BACKEND_URL = 'http://localhost:8000';
const POLL_ALARM = 'poll';

export default defineBackground({
  type: 'module',
  main() {
    // Check-and-recreate alarm (per D-01: 1-minute interval).
    // Alarm state survives worker termination but listeners do not — must re-register on every wake.
    // Using get+create instead of always-create ensures we don't reset the schedule on each wake.
    // Use Promise-based chrome.alarms.get (Chrome MV3 supports both callback and Promise forms).
    chrome.alarms.get(POLL_ALARM).then((alarm) => {
      if (!alarm) {
        chrome.alarms.create(POLL_ALARM, { periodInMinutes: 1 });
      }
    });

    // Re-register alarm listener on every wake (listeners don't persist across termination).
    chrome.alarms.onAlarm.addListener(async (alarm) => {
      if (alarm.name === POLL_ALARM) {
        await maybePoll();
      }
    });

    // D-02: Poll immediately on wake — worker may have been terminated during a cycle.
    maybePoll();

    // Portfolio message handlers — proxy requests from content script to backend.
    // All handlers return true to signal async response (Chrome MV3 requirement).
    chrome.runtime.onMessage.addListener((msg: ExtensionMessage, _sender, sendResponse) => {
      switch (msg.type) {
        case 'PORTFOLIO_GENERATE':
          handlePortfolioGenerate(msg.budget, msg.banned_ea_ids ?? [], msg.exclude_card_types ?? []).then(sendResponse);
          return true; // async response
        case 'PORTFOLIO_CONFIRM':
          handlePortfolioConfirm(msg.players).then(sendResponse);
          return true;
        case 'PORTFOLIO_SWAP':
          handlePortfolioSwap(msg.ea_id, msg.freed_budget, msg.excluded_ea_ids, msg.current_count).then(sendResponse);
          return true;
        case 'PORTFOLIO_LOAD':
          handlePortfolioLoad().then(sendResponse);
          return true;
        case 'TRADE_REPORT':
          handleTradeReport(msg.ea_id, msg.price, msg.outcome).then(sendResponse);
          return true; // async response
        case 'TRADE_REPORT_BATCH':
          handleTradeReportBatch(msg.reports).then(sendResponse);
          return true;
        case 'DASHBOARD_STATUS_REQUEST':
          handleDashboardStatus().then(sendResponse);
          return true; // async response
        case 'ACTIONS_NEEDED_REQUEST':
          handleActionsNeeded().then(sendResponse);
          return true;
        case 'DAILY_CAP_REQUEST':
          handleDailyCap().then(sendResponse);
          return true;
        case 'DAILY_CAP_INCREMENT':
          handleDailyCapIncrement().then(sendResponse);
          return true;
        case 'FRESH_PRICE_REQUEST':
          handleFreshPrice(msg.ea_id).then(sendResponse);
          return true;
        case 'ALGO_START':
          handleAlgoStart(msg.budget).then(sendResponse);
          return true;
        case 'ALGO_STOP':
          handleAlgoStop().then(sendResponse);
          return true;
        case 'ALGO_STATUS_REQUEST':
          handleAlgoStatus().then(sendResponse);
          return true;
        case 'ALGO_SIGNAL_REQUEST':
          handleAlgoSignalRequest().then(sendResponse);
          return true;
        case 'ALGO_SIGNAL_COMPLETE':
          handleAlgoSignalComplete(msg.signal_id, msg.outcome, msg.price, msg.quantity).then(sendResponse);
          return true;
        case 'ALGO_POSITION_SOLD':
          handleAlgoPositionSold(msg.ea_id, msg.sell_price, msg.quantity).then(sendResponse);
          return true;
        case 'ALGO_POSITION_RELIST':
          handleAlgoPositionRelist(msg.ea_id, msg.price, msg.quantity).then(sendResponse);
          return true;
        case 'ALGO_START_RESULT':
        case 'ALGO_STOP_RESULT':
        case 'ALGO_STATUS_RESULT':
        case 'ALGO_SIGNAL_RESULT':
        case 'ALGO_SIGNAL_COMPLETE_RESULT':
        case 'ALGO_POSITION_SOLD_RESULT':
        case 'ALGO_POSITION_RELIST_RESULT':
          return false;
        case 'AUTOMATION_STATUS_REQUEST':
        case 'AUTOMATION_START':
        case 'AUTOMATION_STOP':
        case 'AUTOMATION_START_RESULT':
        case 'AUTOMATION_STOP_RESULT':
        case 'AUTOMATION_STATUS_RESULT':
        case 'DAILY_CAP_RESULT':
        case 'DAILY_CAP_INCREMENT_RESULT':
        case 'FRESH_PRICE_RESULT':
          // Automation control handled by content script directly; result types not received here
          return false;
        default:
          // PING/PONG and other types not handled here — content script handles those
          return false;
      }
    });
  },
});

/**
 * Load portfolio — check storage first, fall back to backend /portfolio/confirmed.
 * After a fresh extension install, storage is empty but the backend may have a
 * confirmed portfolio from a previous session. Fetching it restores the state
 * so the trade observer can start matching immediately.
 */
async function handlePortfolioLoad(): Promise<ExtensionMessage> {
  // Try storage first (fast, survives page refresh)
  const stored = await portfolioItem.getValue();
  if (stored && stored.players.length > 0) {
    return { type: 'PORTFOLIO_LOAD_RESULT', portfolio: stored };
  }

  // Storage empty — try backend (new shape: {portfolio, leftovers})
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/portfolio/confirmed`);
    if (!res.ok) {
      return { type: 'PORTFOLIO_LOAD_RESULT', portfolio: null };
    }
    const json = await res.json();
    // Handle both old (data) and new (portfolio) response shapes
    const portfolioData = json.portfolio ?? json.data ?? [];
    const leftoverData = json.leftovers ?? [];
    if (portfolioData.length === 0 && leftoverData.length === 0) {
      return { type: 'PORTFOLIO_LOAD_RESULT', portfolio: null };
    }

    const players = portfolioData.map(mapToPortfolioPlayer);
    const portfolio: ConfirmedPortfolio = {
      players,
      budget: players.reduce((s: number, p: PortfolioPlayer) => s + p.price, 0),
      confirmed_at: new Date().toISOString(),
    };

    // Persist to storage so future loads are instant
    await portfolioItem.setValue(portfolio);
    return { type: 'PORTFOLIO_LOAD_RESULT', portfolio };
  } catch {
    return { type: 'PORTFOLIO_LOAD_RESULT', portfolio: null };
  }
}

/**
 * Map raw backend JSON to a typed PortfolioPlayer.
 * Handles both `price` and `buy_price` field names from different endpoints.
 */
function mapToPortfolioPlayer(p: any): PortfolioPlayer {
  return {
    ea_id: p.ea_id,
    name: p.name,
    rating: p.rating,
    position: p.position,
    price: p.price ?? p.buy_price,
    sell_price: p.sell_price,
    margin_pct: p.margin_pct,
    expected_profit: p.expected_profit ?? 0,
    op_ratio: p.op_ratio ?? 0,
    efficiency: p.efficiency ?? 0,
    futgg_url: p.futgg_url ?? null,
  };
}

/**
 * Request the backend to generate a portfolio for the given budget, excluding banned players.
 * POST /api/v1/portfolio/generate → PORTFOLIO_GENERATE_RESULT
 */
async function handlePortfolioGenerate(budget: number, banned_ea_ids: number[] = [], exclude_card_types: string[] = []): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/portfolio/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ budget, banned_ea_ids, exclude_card_types }),
    });
    if (!res.ok) {
      return {
        type: 'PORTFOLIO_GENERATE_RESULT',
        data: [],
        budget_used: 0,
        budget_remaining: budget,
        error: `Backend error: ${res.status}`,
      };
    }
    const json = await res.json();
    return {
      type: 'PORTFOLIO_GENERATE_RESULT',
      data: json.data.map(mapToPortfolioPlayer),
      budget_used: json.budget_used,
      budget_remaining: json.budget_remaining,
    };
  } catch (e) {
    return {
      type: 'PORTFOLIO_GENERATE_RESULT',
      data: [],
      budget_used: 0,
      budget_remaining: budget,
      error: String(e),
    };
  }
}

/**
 * Confirm a portfolio — persist to backend and store locally.
 * POST /api/v1/portfolio/confirm → PORTFOLIO_CONFIRM_RESULT
 * On success, writes to portfolioItem so the overlay can show it without a backend round-trip.
 */
async function handlePortfolioConfirm(players: PortfolioPlayer[]): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/portfolio/confirm`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        players: players.map(p => ({
          ea_id: p.ea_id,
          buy_price: p.price,
          sell_price: p.sell_price,
        })),
      }),
    });
    if (!res.ok) {
      return { type: 'PORTFOLIO_CONFIRM_RESULT', confirmed: 0, error: `Backend error: ${res.status}` };
    }
    const json = await res.json();
    await portfolioItem.setValue({
      players,
      budget: players.reduce((s, p) => s + p.price, 0),
      confirmed_at: new Date().toISOString(),
    });
    return { type: 'PORTFOLIO_CONFIRM_RESULT', confirmed: json.confirmed };
  } catch (e) {
    return { type: 'PORTFOLIO_CONFIRM_RESULT', confirmed: 0, error: String(e) };
  }
}

/**
 * Request swap suggestions for a removed player.
 * POST /api/v1/portfolio/swap-preview → PORTFOLIO_SWAP_RESULT
 */
async function handlePortfolioSwap(
  ea_id: number,
  freed_budget: number,
  excluded_ea_ids: number[],
  current_count: number,
): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/portfolio/swap-preview`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ freed_budget, excluded_ea_ids, current_count }),
    });
    if (!res.ok) {
      return { type: 'PORTFOLIO_SWAP_RESULT', replacements: [], error: `Backend error: ${res.status}` };
    }
    const json = await res.json();
    return {
      type: 'PORTFOLIO_SWAP_RESULT',
      replacements: json.replacements.map(mapToPortfolioPlayer),
    };
  } catch (e) {
    return { type: 'PORTFOLIO_SWAP_RESULT', replacements: [], error: String(e) };
  }
}

/**
 * Report a trade outcome to the backend via POST /trade-records/direct.
 * Called by the content script trade observer when it detects a portfolio player
 * on the Transfer List with a known outcome.
 *
 * Uses the direct endpoint (not /actions/{id}/complete) because the observer
 * may detect outcomes before any TradeAction exists (bootstrap scenario per D-09).
 */
async function handleTradeReport(
  ea_id: number,
  price: number,
  outcome: string,
): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/trade-records/direct`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ea_id, price, outcome }),
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => `${res.status}`);
      return { type: 'TRADE_REPORT_RESULT', success: false, error: `Backend error: ${detail}` };
    }
    return { type: 'TRADE_REPORT_RESULT', success: true };
  } catch (e) {
    return { type: 'TRADE_REPORT_RESULT', success: false, error: String(e) };
  }
}

/**
 * Report multiple trade outcomes in a single backend call.
 * Falls back to individual reports if the batch endpoint is unavailable.
 */
async function handleTradeReportBatch(
  reports: Array<{ ea_id: number; price: number; outcome: string }>,
): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/trade-records/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ records: reports }),
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => `${res.status}`);
      return { type: 'TRADE_REPORT_BATCH_RESULT', succeeded: [], failed: reports.map(r => r.ea_id), error: detail };
    }
    const data = await res.json();
    return { type: 'TRADE_REPORT_BATCH_RESULT', succeeded: data.succeeded || reports.map(r => r.ea_id), failed: data.failed || [] };
  } catch (e) {
    return { type: 'TRADE_REPORT_BATCH_RESULT', succeeded: [], failed: reports.map(r => r.ea_id), error: String(e) };
  }
}

/**
 * Fetch portfolio status from backend for the dashboard panel.
 * Returns per-player trade status, cumulative stats, and profit summary.
 * GET /api/v1/portfolio/status → DASHBOARD_STATUS_RESULT
 */
async function handleDashboardStatus(): Promise<ExtensionMessage> {
  try {
    const resp = await fetch(`${BACKEND_URL}/api/v1/portfolio/status`);
    if (!resp.ok) {
      return { type: 'DASHBOARD_STATUS_RESULT', data: null, error: `Backend returned ${resp.status}` };
    }
    const data = await resp.json();
    return { type: 'DASHBOARD_STATUS_RESULT', data, error: undefined };
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return { type: 'DASHBOARD_STATUS_RESULT', data: null, error: message };
  }
}

/**
 * Fetch the unified actions-needed list from the backend.
 * GET /api/v1/portfolio/actions-needed → ACTIONS_NEEDED_RESULT
 */
async function handleActionsNeeded(): Promise<ExtensionMessage> {
  try {
    const resp = await fetch(`${BACKEND_URL}/api/v1/portfolio/actions-needed`);
    if (!resp.ok) {
      return { type: 'ACTIONS_NEEDED_RESULT', data: null, error: `Backend returned ${resp.status}` };
    }
    const data = await resp.json();
    return { type: 'ACTIONS_NEEDED_RESULT', data, error: undefined };
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return { type: 'ACTIONS_NEEDED_RESULT', data: null, error: message };
  }
}

/**
 * Fetch the current daily transaction cap status from the backend.
 * GET /api/v1/automation/daily-cap → DAILY_CAP_RESULT
 */
async function handleDailyCap(): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/automation/daily-cap`);
    if (!res.ok) {
      return { type: 'DAILY_CAP_RESULT', count: 0, cap: 500, capped: false, error: `Backend ${res.status}` };
    }
    const data = await res.json();
    return { type: 'DAILY_CAP_RESULT', count: data.count, cap: data.cap, capped: data.capped };
  } catch (e) {
    return { type: 'DAILY_CAP_RESULT', count: 0, cap: 500, capped: false, error: String(e) };
  }
}

/**
 * Increment the daily transaction cap counter by 1.
 * POST /api/v1/automation/daily-cap/increment → DAILY_CAP_INCREMENT_RESULT
 */
async function handleDailyCapIncrement(): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/automation/daily-cap/increment`, { method: 'POST' });
    if (!res.ok) {
      return { type: 'DAILY_CAP_INCREMENT_RESULT', count: 0, cap: 500, capped: false, error: `Backend ${res.status}` };
    }
    const data = await res.json();
    return { type: 'DAILY_CAP_INCREMENT_RESULT', count: data.count, cap: data.cap, capped: data.capped };
  } catch (e) {
    return { type: 'DAILY_CAP_INCREMENT_RESULT', count: 0, cap: 500, capped: false, error: String(e) };
  }
}

/**
 * Fetch the latest buy and sell prices for a player from the backend.
 * GET /api/v1/portfolio/player-price/{ea_id} → FRESH_PRICE_RESULT
 * Used by the automation engine's price guard (skip if BIN > expected buy price).
 */
async function handleFreshPrice(ea_id: number): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/portfolio/player-price/${ea_id}`);
    if (!res.ok) {
      return { type: 'FRESH_PRICE_RESULT', ea_id, buy_price: 0, sell_price: 0, error: `Backend ${res.status}` };
    }
    const data = await res.json();
    return { type: 'FRESH_PRICE_RESULT', ea_id, buy_price: data.buy_price, sell_price: data.sell_price };
  } catch (e) {
    return { type: 'FRESH_PRICE_RESULT', ea_id, buy_price: 0, sell_price: 0, error: String(e) };
  }
}

// ── Algo trading handlers ────────────────────────────────────────────────────

async function handleAlgoStart(budget: number): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ budget }),
    });
    if (!res.ok) {
      return { type: 'ALGO_START_RESULT', success: false, error: `Backend ${res.status}` };
    }
    const data = await res.json();
    return { type: 'ALGO_START_RESULT', success: true, budget: data.budget, cash: data.cash };
  } catch (e) {
    return { type: 'ALGO_START_RESULT', success: false, error: String(e) };
  }
}

async function handleAlgoStop(): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/stop`, { method: 'POST' });
    if (!res.ok) {
      return { type: 'ALGO_STOP_RESULT', success: false, error: `Backend ${res.status}` };
    }
    return { type: 'ALGO_STOP_RESULT', success: true };
  } catch (e) {
    return { type: 'ALGO_STOP_RESULT', success: false, error: String(e) };
  }
}

async function handleAlgoStatus(): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/status`);
    if (!res.ok) {
      return { type: 'ALGO_STATUS_RESULT', data: null, error: `Backend ${res.status}` };
    }
    const data = await res.json();
    return { type: 'ALGO_STATUS_RESULT', data };
  } catch (e) {
    return { type: 'ALGO_STATUS_RESULT', data: null, error: String(e) };
  }
}

async function handleAlgoSignalRequest(): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/signals/pending`);
    if (!res.ok) {
      return { type: 'ALGO_SIGNAL_RESULT', signal: null, error: `Backend ${res.status}` };
    }
    const data = await res.json();
    return { type: 'ALGO_SIGNAL_RESULT', signal: data.signal };
  } catch (e) {
    return { type: 'ALGO_SIGNAL_RESULT', signal: null, error: String(e) };
  }
}

async function handleAlgoSignalComplete(
  signal_id: number, outcome: string, price: number, quantity: number,
): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/signals/${signal_id}/complete`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ outcome, price, quantity }),
    });
    if (!res.ok) {
      return { type: 'ALGO_SIGNAL_COMPLETE_RESULT', success: false, error: `Backend ${res.status}` };
    }
    return { type: 'ALGO_SIGNAL_COMPLETE_RESULT', success: true };
  } catch (e) {
    return { type: 'ALGO_SIGNAL_COMPLETE_RESULT', success: false, error: String(e) };
  }
}

async function handleAlgoPositionSold(
  ea_id: number, sell_price: number, quantity: number,
): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/positions/${ea_id}/sold`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sell_price, quantity }),
    });
    if (!res.ok) {
      return { type: 'ALGO_POSITION_SOLD_RESULT', success: false, error: `Backend ${res.status}` };
    }
    const data = await res.json();
    return { type: 'ALGO_POSITION_SOLD_RESULT', success: true, pnl: data.pnl };
  } catch (e) {
    return { type: 'ALGO_POSITION_SOLD_RESULT', success: false, error: String(e) };
  }
}

async function handleAlgoPositionRelist(
  ea_id: number, price: number, quantity: number,
): Promise<ExtensionMessage> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/algo/positions/${ea_id}/relist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ price, quantity }),
    });
    if (!res.ok) {
      return { type: 'ALGO_POSITION_RELIST_RESULT', success: false, error: `Backend ${res.status}` };
    }
    return { type: 'ALGO_POSITION_RELIST_RESULT', success: true };
  } catch (e) {
    return { type: 'ALGO_POSITION_RELIST_RESULT', success: false, error: String(e) };
  }
}

/**
 * Send a PING to the active EA Web App tab to confirm the content script is alive.
 * Returns the PONG response, or null if the content script is not ready (expected during navigation).
 * Wraps tabs.sendMessage in try/catch — rejects with "Could not establish connection" when
 * no content script listener is registered (Pitfall 4 from research).
 */
async function pingActiveTab(): Promise<ExtensionMessage | null> {
  try {
    const [tab] = await chrome.tabs.query({
      url: 'https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*',
      active: true,
    });
    if (!tab?.id) return null;
    return await chrome.tabs.sendMessage(tab.id, { type: 'PING' } satisfies ExtensionMessage);
  } catch {
    return null; // content script not ready — expected during navigation
  }
}

/**
 * Poll the backend for a pending action if the enabled flag is set.
 * Stores the fetched action in lastActionItem for Phase 7 DOM automation to consume.
 * After storing an action, pings the active EA tab to confirm content script is alive.
 * Handles all errors gracefully — never throws (worker errors are silent to the user).
 */
async function maybePoll(): Promise<void> {
  const enabled = await enabledItem.getValue();
  if (!enabled) return;

  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/actions/pending`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.action) {
      await lastActionItem.setValue(data.action);
      const pong = await pingActiveTab();
      if (pong) {
        console.log('[OP Seller] Content script alive');
      }
    }
  } catch (e) {
    console.error('[OP Seller] poll failed:', e);
  }
}
