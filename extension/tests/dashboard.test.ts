/**
 * Unit tests for the Dashboard tab in the overlay panel.
 * Verifies tab bar rendering, tab switching, dashboard data display,
 * color coding, refresh behavior, and state isolation (D-01 through D-13).
 *
 * Runs in jsdom environment (configured in vitest.config.ts).
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { createOverlayPanel } from '../src/overlay/panel';
import type { DashboardData } from '../src/messages';

// ── Mock chrome.runtime.sendMessage ───────────────────────────────────────────

const mockSendMessage = vi.fn();

vi.stubGlobal('chrome', {
  runtime: {
    sendMessage: mockSendMessage,
  },
});

// ── Shared test data ──────────────────────────────────────────────────────────

const PLAYER_A = {
  ea_id: 100,
  name: 'Lionel Messi',
  rating: 91,
  position: 'RW',
  price: 50000,
  sell_price: 70000,
  margin_pct: 0.4,
  expected_profit: 14000,
  op_ratio: 0.5,
  efficiency: 0.28,
};

const CONFIRMED_DATA = {
  players: [PLAYER_A],
  budget: 100000,
  budget_used: 50000,
  budget_remaining: 50000,
};

const DASHBOARD_DATA: DashboardData = {
  summary: {
    realized_profit: 16500,
    unrealized_pnl: 3200,
    trade_counts: { bought: 12, sold: 8, expired: 3 },
  },
  players: [
    {
      ea_id: 100,
      name: 'Lionel Messi',
      futgg_url: '/players/158023-lionel-messi/26-100158023/',
      status: 'LISTED',
      times_sold: 2,
      realized_profit: 8000,
      unrealized_pnl: 1500,
      buy_price: 50000,
      sell_price: 65000,
      current_bin: 51500,
      is_leftover: false,
    },
  ],
};

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('dashboard tab', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    mockSendMessage.mockReset();
    // Default mock: handle both Actions and Dashboard tab fetches
    mockSendMessage.mockImplementation((msg: any) => {
      if (msg.type === 'ACTIONS_NEEDED_REQUEST') {
        return Promise.resolve({
          type: 'ACTIONS_NEEDED_RESULT',
          data: { actions: [], summary: { to_buy: 0, to_list: 0, to_relist: 0, waiting: 0 } },
        });
      }
      if (msg.type === 'DASHBOARD_STATUS_REQUEST') {
        return Promise.resolve({
          type: 'DASHBOARD_STATUS_RESULT',
          data: DASHBOARD_DATA,
        });
      }
      return Promise.resolve(undefined);
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders tab bar with Actions, Portfolio and Dashboard buttons in confirmed state', () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    panel.setState('confirmed', CONFIRMED_DATA);

    const tabBar = panel.container.querySelector('.op-seller-tab-bar');
    expect(tabBar).toBeTruthy();

    const buttons = Array.from(tabBar!.querySelectorAll('button'));
    const actionsBtn = buttons.find(b => b.textContent?.includes('Actions'));
    const portfolioBtn = buttons.find(b => b.textContent?.includes('Portfolio'));
    const dashboardBtn = buttons.find(b => b.textContent?.includes('Dashboard'));
    expect(actionsBtn).toBeTruthy();
    expect(portfolioBtn).toBeTruthy();
    expect(dashboardBtn).toBeTruthy();
  });

  it('does NOT render tab bar in empty state', () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    // Empty state is the initial default
    const tabBar = panel.container.querySelector('.op-seller-tab-bar');
    expect(tabBar).toBeNull();
  });

  it('does NOT render tab bar in draft state', () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    panel.setState('draft', {
      players: [PLAYER_A],
      budget: 100000,
      budget_used: 50000,
      budget_remaining: 50000,
    });

    const tabBar = panel.container.querySelector('.op-seller-tab-bar');
    expect(tabBar).toBeNull();
  });

  it('Actions tab is the default active tab with active background styling', () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    panel.setState('confirmed', CONFIRMED_DATA);

    const tabBar = panel.container.querySelector('.op-seller-tab-bar');
    const buttons = Array.from(tabBar!.querySelectorAll('button'));
    const actionsBtn = buttons.find(b => b.textContent?.includes('Actions')) as HTMLButtonElement;
    const portfolioBtn = buttons.find(b => b.textContent?.includes('Portfolio')) as HTMLButtonElement;
    const dashboardBtn = buttons.find(b => b.textContent?.includes('Dashboard')) as HTMLButtonElement;

    // jsdom normalizes hex colors to rgb format
    expect(actionsBtn.style.background).toBe('rgb(58, 58, 94)'); // #3a3a5e
    expect(portfolioBtn.style.background).not.toBe('rgb(58, 58, 94)');
    expect(dashboardBtn.style.background).not.toBe('rgb(58, 58, 94)');
  });

  it('clicking Dashboard tab sends DASHBOARD_STATUS_REQUEST message', async () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    panel.setState('confirmed', CONFIRMED_DATA);

    const tabBar = panel.container.querySelector('.op-seller-tab-bar');
    const dashboardBtn = Array.from(tabBar!.querySelectorAll('button'))
      .find(b => b.textContent?.includes('Dashboard')) as HTMLButtonElement;
    dashboardBtn.click();

    await new Promise(r => setTimeout(r, 10));

    expect(mockSendMessage).toHaveBeenCalledWith(
      expect.objectContaining({ type: 'DASHBOARD_STATUS_REQUEST' }),
    );
  });

  it('dashboard tab renders player status badges', async () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    panel.setState('confirmed', CONFIRMED_DATA);

    const tabBar = panel.container.querySelector('.op-seller-tab-bar');
    const dashboardBtn = Array.from(tabBar!.querySelectorAll('button'))
      .find(b => b.textContent?.includes('Dashboard')) as HTMLButtonElement;
    dashboardBtn.click();

    await new Promise(r => setTimeout(r, 10));

    const badges = panel.container.querySelectorAll('.op-seller-status-badge');
    expect(badges.length).toBeGreaterThan(0);

    const badgeTexts = Array.from(badges).map(b => b.textContent);
    expect(badgeTexts).toContain('LISTED');
  });

  it('dashboard tab renders cumulative stats with times sold and profit', async () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    panel.setState('confirmed', CONFIRMED_DATA);

    const tabBar = panel.container.querySelector('.op-seller-tab-bar');
    const dashboardBtn = Array.from(tabBar!.querySelectorAll('button'))
      .find(b => b.textContent?.includes('Dashboard')) as HTMLButtonElement;
    dashboardBtn.click();

    await new Promise(r => setTimeout(r, 10));

    const text = panel.container.textContent ?? '';
    expect(text).toContain('2x sold');
    expect(text).toContain('8,000'); // realized_profit formatted
  });

  it('dashboard tab renders summary bar with Realized and Unrealized labels', async () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    panel.setState('confirmed', CONFIRMED_DATA);

    const tabBar = panel.container.querySelector('.op-seller-tab-bar');
    const dashboardBtn = Array.from(tabBar!.querySelectorAll('button'))
      .find(b => b.textContent?.includes('Dashboard')) as HTMLButtonElement;
    dashboardBtn.click();

    await new Promise(r => setTimeout(r, 10));

    const summaryBar = panel.container.querySelector('.op-seller-dashboard-summary');
    expect(summaryBar).toBeTruthy();

    const text = summaryBar!.textContent ?? '';
    expect(text).toContain('Realized');
    expect(text).toContain('Unrealized');
  });

  it('Refresh button triggers a second fetch', async () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    panel.setState('confirmed', CONFIRMED_DATA);

    // Wait for initial Actions tab fetch
    await new Promise(r => setTimeout(r, 10));

    const tabBar = panel.container.querySelector('.op-seller-tab-bar');
    const dashboardBtn = Array.from(tabBar!.querySelectorAll('button'))
      .find(b => b.textContent?.includes('Dashboard')) as HTMLButtonElement;
    dashboardBtn.click();

    // Wait for the first dashboard fetch to complete and render the refresh button
    await new Promise(r => setTimeout(r, 10));

    const refreshBtn = panel.container.querySelector('.op-seller-dashboard-refresh') as HTMLButtonElement;
    expect(refreshBtn).toBeTruthy();

    // Count calls before refresh
    const callsBefore = mockSendMessage.mock.calls.filter(
      (c: any[]) => c[0]?.type === 'DASHBOARD_STATUS_REQUEST'
    ).length;

    refreshBtn.click();
    await new Promise(r => setTimeout(r, 10));

    const callsAfter = mockSendMessage.mock.calls.filter(
      (c: any[]) => c[0]?.type === 'DASHBOARD_STATUS_REQUEST'
    ).length;

    // Should have one more DASHBOARD_STATUS_REQUEST after refresh
    expect(callsAfter).toBe(callsBefore + 1);
  });

  it('negative profit values are rendered with red color', async () => {
    const negativeData: DashboardData = {
      ...DASHBOARD_DATA,
      players: [
        {
          ...DASHBOARD_DATA.players[0],
          realized_profit: -5000,
        },
      ],
    };

    mockSendMessage.mockImplementation((msg: any) => {
      if (msg.type === 'ACTIONS_NEEDED_REQUEST') {
        return Promise.resolve({
          type: 'ACTIONS_NEEDED_RESULT',
          data: { actions: [], summary: { to_buy: 0, to_list: 0, to_relist: 0, waiting: 0 } },
        });
      }
      return Promise.resolve({
        type: 'DASHBOARD_STATUS_RESULT',
        data: negativeData,
      });
    });

    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    panel.setState('confirmed', CONFIRMED_DATA);

    const tabBar = panel.container.querySelector('.op-seller-tab-bar');
    const dashboardBtn = Array.from(tabBar!.querySelectorAll('button'))
      .find(b => b.textContent?.includes('Dashboard')) as HTMLButtonElement;
    dashboardBtn.click();

    await new Promise(r => setTimeout(r, 10));

    const profitEl = panel.container.querySelector('.op-seller-player-profit') as HTMLElement;
    expect(profitEl).toBeTruthy();
    // Negative profit should use red color (#f88 = rgb(255, 136, 136) in jsdom)
    expect(profitEl.style.color).toBe('rgb(255, 136, 136)');
  });
});
