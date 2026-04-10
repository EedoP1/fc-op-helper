/**
 * Overlay panel module for the EA Web App.
 *
 * Injects a collapsible right sidebar showing the OP seller portfolio.
 * Supports three states (empty / draft / confirmed) and proxies portfolio
 * operations to the service worker via chrome.runtime.sendMessage.
 *
 * Design references: D-01 (right sidebar), D-02 (dark theme), D-03 (player fields),
 * D-04 (budget input), D-05 (two-step flow), D-07/D-08/D-09 (swap), D-10 (ephemeral draft),
 * D-11 (confirmed from backend), D-12 (three states).
 * Phase 07.2 adds: tab bar (D-01), Dashboard tab (D-04–D-13), fetch on switch (D-12),
 * per-player status badges (D-06), summary bar (D-08/D-09), refresh button (D-13).
 */

import type { PortfolioPlayer } from '../storage';
import type { ExtensionMessage, DashboardData, DashboardPlayer, ActionsNeededData, ActionNeeded, AlgoStatusData } from '../messages';
import { automationStatusItem, activityLogItem, algoCredentialsItem } from '../storage';

// ── Non-blocking error notification ──────────────────────────────────────────

/**
 * Show a prominent but non-blocking error toast at the top of the viewport.
 * Replaces window.alert() which blocks the JS thread and prevents start/stop.
 * Auto-dismisses after 10 seconds or on click.
 */
function showErrorToast(message: string): void {
  const toast = document.createElement('div');
  Object.assign(toast.style, {
    position: 'fixed',
    top: '20px',
    left: '50%',
    transform: 'translateX(-50%)',
    background: '#e74c3c',
    color: '#fff',
    padding: '16px 24px',
    borderRadius: '8px',
    fontSize: '14px',
    fontWeight: 'bold',
    zIndex: '999999',
    maxWidth: '500px',
    textAlign: 'center',
    boxShadow: '0 4px 12px rgba(0,0,0,0.4)',
    cursor: 'pointer',
  });
  toast.textContent = message;
  toast.title = 'Click to dismiss';
  document.body.appendChild(toast);
  const remove = () => { if (document.body.contains(toast)) document.body.removeChild(toast); };
  toast.addEventListener('click', remove);
  setTimeout(remove, 10_000);
}

// ── Types ─────────────────────────────────────────────────────────────────────

export type PanelState = 'empty' | 'draft' | 'confirmed';

export interface PanelStateData {
  players: PortfolioPlayer[];
  budget: number;
  budget_used: number;
  budget_remaining: number;
}

export interface OverlayPanel {
  /** The panel container div (append to document.body) */
  container: HTMLDivElement;
  /** The toggle button (append to document.body) */
  toggle: HTMLButtonElement;
  /** Switch the panel to the given state, optionally supplying player data */
  setState(state: PanelState, data?: PanelStateData): void;
  /** Remove container and toggle from the DOM */
  destroy(): void;
}

// ── Sorting ───────────────────────────────────────────────────────────────────

type SortKey = 'name' | 'rating' | 'price' | 'sell_price' | 'margin_pct' | 'expected_profit' | 'op_ratio' | 'efficiency';
type SortDir = 'asc' | 'desc';

function sortPlayers(players: PortfolioPlayer[], key: SortKey, dir: SortDir): PortfolioPlayer[] {
  const sorted = [...players];
  sorted.sort((a, b) => {
    const aVal = a[key];
    const bVal = b[key];
    if (typeof aVal === 'string' && typeof bVal === 'string') {
      return dir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
    }
    return dir === 'asc' ? (aVal as number) - (bVal as number) : (bVal as number) - (aVal as number);
  });
  return sorted;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmt(n: number): string {
  return n.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function pct(n: number): string {
  return (n * 100).toFixed(0) + '%';
}

// ── Factory ───────────────────────────────────────────────────────────────────

/**
 * Create the overlay panel and toggle button.
 *
 * Returns `{ container, toggle, setState, destroy }`.
 * Caller appends container and toggle to document.body.
 */
export function createOverlayPanel(): OverlayPanel {
  // ── State ──────────────────────────────────────────────────────────────────

  let currentState: PanelState = 'empty';
  let draftPlayers: PortfolioPlayer[] = [];  // D-10: ephemeral draft, in-memory only
  let removedEaIds: Set<number> = new Set();  // Track removed players within draft session
  let swapInFlight = false;  // Block removals while a regenerate request is in flight

  let draftBudget = 0;
  let draftBudgetUsed = 0;
  let draftBudgetRemaining = 0;
  let draftExcludedCardTypes: string[] = [];  // Persisted across draft regenerates
  let sortKey: SortKey = 'efficiency';
  let sortDir: SortDir = 'desc';
  let activeTab: 'actions' | 'portfolio' | 'dashboard' | 'algo' = 'actions';

  // ── Container (panel) ──────────────────────────────────────────────────────

  const container = document.createElement('div');
  container.className = 'op-seller-panel';
  Object.assign(container.style, {
    position: 'fixed',
    top: '0',
    right: '0',
    width: '320px',
    height: '100vh',
    background: '#1a1a2e',
    color: '#e0e0e0',
    zIndex: '999999',
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    overflowY: 'auto',
    transform: 'translateX(100%)',
    transition: 'transform 0.2s ease',
    boxSizing: 'border-box',
    padding: '16px',
  });

  // ── Toggle button ──────────────────────────────────────────────────────────

  const toggle = document.createElement('button');
  toggle.className = 'op-seller-toggle';
  toggle.textContent = 'OP';
  Object.assign(toggle.style, {
    position: 'fixed',
    top: '50%',
    right: '0',
    transform: 'translateY(-50%)',
    background: '#1a1a2e',
    color: '#e0e0e0',
    border: '1px solid #333',
    borderRight: 'none',
    cursor: 'pointer',
    zIndex: '999999',
    padding: '12px 6px',
    borderRadius: '4px 0 0 4px',
  });

  let isOpen = false;

  toggle.addEventListener('click', () => {
    isOpen = !isOpen;
    if (isOpen) {
      container.style.transform = 'translateX(0)';
      toggle.style.right = '320px';
      toggle.textContent = 'X';
    } else {
      container.style.transform = 'translateX(100%)';
      toggle.style.right = '0';
      toggle.textContent = 'OP';
    }
  });

  // ── Render helpers ─────────────────────────────────────────────────────────

  /** Apply shared style properties to a button element */
  function styleButton(
    btn: HTMLButtonElement,
    bg: string = '#4CAF50',
    extra: Partial<CSSStyleDeclaration> = {},
  ): void {
    Object.assign(btn.style, {
      background: bg,
      color: '#fff',
      border: 'none',
      padding: '10px 16px',
      width: '100%',
      cursor: 'pointer',
      borderRadius: '4px',
      marginTop: '8px',
      fontSize: '14px',
      ...extra,
    });
  }

  // ── Dashboard helpers ──────────────────────────────────────────────────────

  const STATUS_COLORS: Record<string, { bg: string; text: string }> = {
    PENDING:  { bg: '#3a2a00', text: '#FFB300' },
    BOUGHT:   { bg: '#1a3a1a', text: '#4CAF50' },
    LISTED:   { bg: '#1a2a3a', text: '#6cf' },
    SOLD:     { bg: '#1a3a1a', text: '#4CAF50' },
    EXPIRED:  { bg: '#3a1a1a', text: '#f88' },
  };

  /**
   * Render the tab bar for the confirmed state.
   * Tab bar appears ONLY inside renderConfirmed (D-02).
   * @param onSwitch - called with the selected tab name when user clicks a tab
   */
  function renderTabBar(onSwitch: (tab: 'actions' | 'portfolio' | 'dashboard' | 'algo') => void): HTMLDivElement {
    const bar = document.createElement('div');
    bar.className = 'op-seller-tab-bar';
    Object.assign(bar.style, {
      display: 'flex',
      gap: '0',
      marginBottom: '12px',
      borderBottom: '1px solid #444',
    });

    const tabLabels: Record<string, string> = { actions: 'Actions', portfolio: 'Portfolio', dashboard: 'Dashboard', algo: 'Algo' };
    (['actions', 'portfolio', 'dashboard', 'algo'] as const).forEach(tab => {
      const btn = document.createElement('button');
      btn.textContent = tabLabels[tab];
      btn.dataset.tab = tab;
      const isActive = tab === activeTab;
      Object.assign(btn.style, {
        flex: '1',
        background: isActive ? '#3a3a5e' : 'transparent',
        color: isActive ? '#fff' : '#aaa',
        border: 'none',
        borderBottom: isActive ? '2px solid #6cf' : '2px solid transparent',
        cursor: 'pointer',
        padding: '8px 0',
        fontSize: '13px',
      });
      btn.addEventListener('click', () => onSwitch(tab));
      bar.appendChild(btn);
    });

    return bar;
  }

  /**
   * Render the full dashboard content (summary bar + refresh + filter + per-player list)
   * into the given parent element.
   */
  function renderDashboardContent(parent: HTMLElement, data: DashboardData): void {
    // Summary bar (D-08)
    const summaryBar = document.createElement('div');
    summaryBar.className = 'op-seller-dashboard-summary';
    Object.assign(summaryBar.style, {
      display: 'flex',
      justifyContent: 'space-between',
      background: '#2a2a3e',
      padding: '10px',
      borderRadius: '4px',
      marginBottom: '12px',
      fontSize: '12px',
    });

    // Realized profit (D-09: separate label + color-coded)
    const realizedEl = document.createElement('div');
    const realizedColor = data.summary.realized_profit >= 0 ? '#4CAF50' : '#f88';
    realizedEl.innerHTML = `<div style="color:#aaa">Realized</div><div style="color:${realizedColor};font-weight:bold">${fmt(data.summary.realized_profit)}</div>`;
    summaryBar.appendChild(realizedEl);

    // Unrealized P&L (D-09: separate label + color-coded)
    const unrealizedEl = document.createElement('div');
    const unrealizedColor = data.summary.unrealized_pnl >= 0 ? '#4CAF50' : '#f88';
    unrealizedEl.innerHTML = `<div style="color:#aaa">Unrealized</div><div style="color:${unrealizedColor};font-weight:bold">${fmt(data.summary.unrealized_pnl)}</div>`;
    summaryBar.appendChild(unrealizedEl);

    // Trade counts (D-08)
    const countsEl = document.createElement('div');
    const tc = data.summary.trade_counts;
    countsEl.innerHTML = `<div style="color:#aaa">Trades</div><div style="color:#ccc">${tc.bought}B / ${tc.sold}S / ${tc.expired}E</div>`;
    summaryBar.appendChild(countsEl);

    parent.appendChild(summaryBar);

    // Refresh button (D-13: manual refresh)
    const refreshBtn = document.createElement('button');
    refreshBtn.textContent = 'Refresh';
    refreshBtn.className = 'op-seller-dashboard-refresh';
    styleButton(refreshBtn, '#2196F3', { marginTop: '0', marginBottom: '8px', padding: '6px 12px', fontSize: '12px' });
    refreshBtn.addEventListener('click', () => renderDashboard());
    parent.appendChild(refreshBtn);

    // ── Filter controls ────────────────────────────────────────────────────────

    let dashSearchText = '';
    let dashStatusFilter: string = 'ALL';

    const filterRow = document.createElement('div');
    Object.assign(filterRow.style, {
      display: 'flex',
      gap: '6px',
      marginBottom: '8px',
    });

    // Name search input
    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.placeholder = 'Search name...';
    Object.assign(searchInput.style, {
      flex: '1',
      background: '#2a2a3e',
      color: '#fff',
      border: '1px solid #444',
      borderRadius: '3px',
      padding: '4px 8px',
      fontSize: '12px',
      boxSizing: 'border-box',
    });

    // Status filter dropdown
    const statusSelect = document.createElement('select');
    Object.assign(statusSelect.style, {
      background: '#2a2a3e',
      color: '#ccc',
      border: '1px solid #444',
      borderRadius: '3px',
      padding: '4px 6px',
      fontSize: '12px',
    });
    (['ALL', 'PENDING', 'BOUGHT', 'LISTED', 'SOLD', 'EXPIRED'] as const).forEach(status => {
      const opt = document.createElement('option');
      opt.value = status;
      opt.textContent = status === 'ALL' ? 'All' : status;
      statusSelect.appendChild(opt);
    });

    filterRow.appendChild(searchInput);
    filterRow.appendChild(statusSelect);
    parent.appendChild(filterRow);

    // ── Sort controls ──────────────────────────────────────────────────────────

    type DashSortKey = 'name' | 'buy_price' | 'sell_price' | 'times_sold' | 'realized_profit' | 'unrealized_pnl';
    let dashSortKey: DashSortKey = 'realized_profit';
    let dashSortDir: SortDir = 'desc';

    const sortBarSlot = document.createElement('div');
    parent.appendChild(sortBarSlot);

    function renderDashSortBar(): void {
      sortBarSlot.innerHTML = '';
      const bar = document.createElement('div');
      Object.assign(bar.style, {
        display: 'flex',
        flexWrap: 'wrap',
        gap: '4px',
        marginBottom: '8px',
      });

      const columns: { label: string; key: DashSortKey }[] = [
        { label: 'Name', key: 'name' },
        { label: 'Buy', key: 'buy_price' },
        { label: 'Sell', key: 'sell_price' },
        { label: 'Times Sold', key: 'times_sold' },
        { label: 'Profit', key: 'realized_profit' },
        { label: 'Unreal.', key: 'unrealized_pnl' },
      ];

      columns.forEach(col => {
        const btn = document.createElement('button');
        const arrow = dashSortKey === col.key ? (dashSortDir === 'desc' ? ' \u25BC' : ' \u25B2') : '';
        btn.textContent = col.label + arrow;
        Object.assign(btn.style, {
          background: dashSortKey === col.key ? '#3a3a5e' : '#2a2a3e',
          color: dashSortKey === col.key ? '#fff' : '#aaa',
          border: '1px solid #444',
          borderRadius: '3px',
          cursor: 'pointer',
          padding: '3px 6px',
          fontSize: '11px',
          flex: '0 0 auto',
        });
        btn.addEventListener('click', () => {
          if (dashSortKey === col.key) {
            dashSortDir = dashSortDir === 'desc' ? 'asc' : 'desc';
          } else {
            dashSortKey = col.key;
            dashSortDir = 'desc';
          }
          renderDashboardPlayerList();
        });
        bar.appendChild(btn);
      });

      sortBarSlot.appendChild(bar);
    }

    // ── Player list (filtered + sorted) ───────────────────────────────────────

    const listEl = document.createElement('div');
    parent.appendChild(listEl);

    function renderDashboardPlayerList(): void {
      renderDashSortBar();
      listEl.innerHTML = '';

      const query = dashSearchText.toLowerCase();
      const filtered = data.players.filter((p: DashboardPlayer) => {
        const nameMatch = !query || p.name.toLowerCase().includes(query);
        const statusMatch = dashStatusFilter === 'ALL' || p.status === dashStatusFilter;
        return nameMatch && statusMatch;
      });

      // Sort filtered players
      filtered.sort((a: DashboardPlayer, b: DashboardPlayer) => {
        const aRaw = a[dashSortKey];
        const bRaw = b[dashSortKey];
        const aVal = aRaw ?? (dashSortDir === 'desc' ? -Infinity : Infinity);
        const bVal = bRaw ?? (dashSortDir === 'desc' ? -Infinity : Infinity);
        if (typeof aVal === 'string' && typeof bVal === 'string') {
          return dashSortDir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
        }
        return dashSortDir === 'asc' ? (aVal as number) - (bVal as number) : (bVal as number) - (aVal as number);
      });

      if (filtered.length === 0) {
        const empty = document.createElement('div');
        empty.textContent = 'No players match the filter.';
        empty.style.color = '#aaa';
        empty.style.fontSize = '12px';
        empty.style.padding = '8px 0';
        listEl.appendChild(empty);
        return;
      }

      filtered.forEach((player: DashboardPlayer) => {
        const row = document.createElement('div');
        row.className = 'op-seller-dashboard-player';
        Object.assign(row.style, {
          background: '#2a2a3e',
          padding: '8px 10px',
          marginBottom: '4px',
          borderRadius: '4px',
        });

        // Line 1: Name (clickable link) + status badge (D-06)
        const topLine = document.createElement('div');
        topLine.style.display = 'flex';
        topLine.style.justifyContent = 'space-between';
        topLine.style.alignItems = 'center';
        topLine.style.marginBottom = '2px';

        // Clickable link to fut.gg search for this player
        // Use explicit click handler + window.open instead of relying on target="_blank",
        // because the EA SPA intercepts <a> clicks at the document level for its own routing.
        const nameLink = document.createElement('a');
        const nameLinkUrl = player.futgg_url
          ? `https://www.fut.gg${player.futgg_url}`
          : `https://www.fut.gg/players/?search=${encodeURIComponent(player.name)}`;
        nameLink.href = nameLinkUrl;
        nameLink.target = '_blank';
        nameLink.rel = 'noopener';
        nameLink.textContent = player.name;
        Object.assign(nameLink.style, {
          color: '#6cf',
          textDecoration: 'none',
          fontWeight: 'bold',
          fontSize: '13px',
          cursor: 'pointer',
        });
        nameLink.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopImmediatePropagation();
          window.open(nameLinkUrl, '_blank', 'noopener');
        });
        nameLink.addEventListener('mouseenter', () => { nameLink.style.textDecoration = 'underline'; });
        nameLink.addEventListener('mouseleave', () => { nameLink.style.textDecoration = 'none'; });
        topLine.appendChild(nameLink);

        const badge = document.createElement('span');
        badge.textContent = player.status === 'PENDING' ? 'NEEDS BUY' : player.status;
        badge.className = 'op-seller-status-badge';
        const colors = STATUS_COLORS[player.status] || { bg: '#2a2a3e', text: '#888' };
        Object.assign(badge.style, {
          background: colors.bg,
          color: colors.text,
          padding: '2px 6px',
          borderRadius: '3px',
          fontSize: '10px',
          fontWeight: 'bold',
        });
        topLine.appendChild(badge);
        row.appendChild(topLine);

        // Line 2: Cumulative stats (D-05, D-06)
        const statsLine = document.createElement('div');
        statsLine.style.fontSize = '11px';
        const profitColor = player.realized_profit >= 0 ? '#4CAF50' : '#f88';
        statsLine.innerHTML = `<span style="color:#aaa">${player.times_sold}x sold</span> <span style="color:#555">\u2022</span> <span class="op-seller-player-profit" style="color:${profitColor}">${player.realized_profit >= 0 ? '+' : ''}${fmt(player.realized_profit)} profit</span>`;
        row.appendChild(statsLine);

        listEl.appendChild(row);
      });
    }

    searchInput.addEventListener('input', () => {
      dashSearchText = searchInput.value;
      renderDashboardPlayerList();
    });

    statusSelect.addEventListener('change', () => {
      dashStatusFilter = statusSelect.value;
      renderDashboardPlayerList();
    });

    renderDashboardPlayerList();
  }

  // ── Actions tab ────────────────────────────────────────────────────────────

  const ACTION_COLORS: Record<string, { bg: string; text: string }> = {
    BUY:    { bg: '#1a3a1a', text: '#4CAF50' },
    LIST:   { bg: '#1a2a3a', text: '#6cf' },
    RELIST: { bg: '#3a2a00', text: '#FFB300' },
    WAIT:   { bg: '#2a2a3e', text: '#888' },
  };

  /**
   * Render the actions-needed content into the given parent element.
   * Shows a summary bar + flat list of what to do for each player.
   */
  function renderActionsContent(parent: HTMLElement, data: ActionsNeededData): void {
    // Summary bar
    const summaryBar = document.createElement('div');
    summaryBar.className = 'op-seller-actions-summary';
    Object.assign(summaryBar.style, {
      display: 'flex',
      justifyContent: 'space-between',
      background: '#2a2a3e',
      padding: '10px',
      borderRadius: '4px',
      marginBottom: '12px',
      fontSize: '12px',
    });

    const s = data.summary;
    const items = [
      { label: 'Buy', count: s.to_buy, color: '#4CAF50' },
      { label: 'List', count: s.to_list, color: '#6cf' },
      { label: 'Relist', count: s.to_relist, color: '#FFB300' },
      { label: 'Waiting', count: s.waiting, color: '#888' },
    ];
    items.forEach(item => {
      const el = document.createElement('div');
      el.innerHTML = `<div style="color:#aaa">${item.label}</div><div style="color:${item.color};font-weight:bold">${item.count}</div>`;
      summaryBar.appendChild(el);
    });
    parent.appendChild(summaryBar);

    // Refresh button
    const refreshBtn = document.createElement('button');
    refreshBtn.textContent = 'Refresh';
    refreshBtn.className = 'op-seller-actions-refresh';
    styleButton(refreshBtn, '#2196F3', { marginTop: '0', marginBottom: '8px', padding: '6px 12px', fontSize: '12px' });
    refreshBtn.addEventListener('click', () => renderActions());
    parent.appendChild(refreshBtn);

    // Filter: hide WAIT by default
    let showWaiting = false;
    const toggleWaitBtn = document.createElement('button');
    toggleWaitBtn.textContent = showWaiting ? 'Hide Waiting' : 'Show Waiting';
    styleButton(toggleWaitBtn, '#444', { marginTop: '0', marginBottom: '8px', padding: '4px 10px', fontSize: '11px' });
    parent.appendChild(toggleWaitBtn);

    const listEl = document.createElement('div');
    parent.appendChild(listEl);

    function renderActionList(): void {
      listEl.innerHTML = '';
      toggleWaitBtn.textContent = showWaiting ? 'Hide Waiting' : `Show Waiting (${s.waiting})`;

      const filtered = showWaiting ? data.actions : data.actions.filter(a => a.action !== 'WAIT');

      if (filtered.length === 0) {
        const empty = document.createElement('div');
        empty.textContent = 'Nothing to do right now.';
        empty.style.color = '#aaa';
        empty.style.fontSize = '12px';
        empty.style.padding = '8px 0';
        listEl.appendChild(empty);
        return;
      }

      filtered.forEach((item: ActionNeeded) => {
        const row = document.createElement('div');
        row.className = 'op-seller-action-row';
        Object.assign(row.style, {
          background: '#2a2a3e',
          padding: '8px 10px',
          marginBottom: '4px',
          borderRadius: '4px',
          borderLeft: `3px solid ${(ACTION_COLORS[item.action] || ACTION_COLORS.WAIT).text}`,
        });

        // Line 1: Action badge + player name
        const topLine = document.createElement('div');
        topLine.style.display = 'flex';
        topLine.style.justifyContent = 'space-between';
        topLine.style.alignItems = 'center';
        topLine.style.marginBottom = '2px';

        const nameLink = document.createElement('a');
        const nameLinkUrl = item.futgg_url
          ? `https://www.fut.gg${item.futgg_url}`
          : `https://www.fut.gg/players/?search=${encodeURIComponent(item.name)}`;
        nameLink.href = nameLinkUrl;
        nameLink.target = '_blank';
        nameLink.rel = 'noopener';
        nameLink.textContent = item.name;
        Object.assign(nameLink.style, {
          color: '#6cf',
          textDecoration: 'none',
          fontWeight: 'bold',
          fontSize: '13px',
          cursor: 'pointer',
        });
        nameLink.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopImmediatePropagation();
          window.open(nameLinkUrl, '_blank', 'noopener');
        });
        nameLink.addEventListener('mouseenter', () => { nameLink.style.textDecoration = 'underline'; });
        nameLink.addEventListener('mouseleave', () => { nameLink.style.textDecoration = 'none'; });
        topLine.appendChild(nameLink);

        const badgeContainer = document.createElement('div');
        badgeContainer.style.display = 'flex';
        badgeContainer.style.gap = '4px';
        badgeContainer.style.alignItems = 'center';

        // Leftover badge
        if (item.is_leftover) {
          const leftoverBadge = document.createElement('span');
          leftoverBadge.textContent = 'OLD';
          Object.assign(leftoverBadge.style, {
            background: '#3a2a3a',
            color: '#c8a',
            padding: '2px 5px',
            borderRadius: '3px',
            fontSize: '9px',
            fontWeight: 'bold',
          });
          badgeContainer.appendChild(leftoverBadge);
        }

        // Action badge
        const badge = document.createElement('span');
        badge.textContent = item.action;
        const colors = ACTION_COLORS[item.action] || ACTION_COLORS.WAIT;
        Object.assign(badge.style, {
          background: colors.bg,
          color: colors.text,
          padding: '2px 6px',
          borderRadius: '3px',
          fontSize: '10px',
          fontWeight: 'bold',
        });
        badgeContainer.appendChild(badge);
        topLine.appendChild(badgeContainer);
        row.appendChild(topLine);

        // Line 2: Rating, position, target price
        const detailLine = document.createElement('div');
        detailLine.style.fontSize = '11px';
        detailLine.style.color = '#aaa';
        const priceLabel = item.action === 'BUY' ? 'Buy at' : 'Sell at';
        detailLine.textContent = `${item.rating} ${item.position} \u2022 ${priceLabel}: ${fmt(item.target_price)}`;
        row.appendChild(detailLine);

        listEl.appendChild(row);
      });
    }

    toggleWaitBtn.addEventListener('click', () => {
      showWaiting = !showWaiting;
      renderActionList();
    });

    renderActionList();
  }

  /**
   * Render the actions tab: fetch from backend, show loading, then render content.
   */
  function renderActions(): void {
    const contentArea = container.querySelector('.op-seller-tab-content') as HTMLElement | null;
    const content = contentArea || container;

    if (contentArea) contentArea.innerHTML = '';

    const loading = document.createElement('div');
    loading.textContent = 'Loading actions...';
    loading.style.color = '#aaa';
    loading.style.padding = '16px 0';
    content.appendChild(loading);

    chrome.runtime.sendMessage({ type: 'ACTIONS_NEEDED_REQUEST' } satisfies ExtensionMessage)
      .then((res: ExtensionMessage) => {
        if (res.type === 'ACTIONS_NEEDED_RESULT') {
          content.innerHTML = '';
          if (res.error || !res.data) {
            const errEl = document.createElement('div');
            errEl.textContent = `Error: ${res.error || 'No data'}`;
            errEl.style.color = '#f44';
            content.appendChild(errEl);
            return;
          }
          renderActionsContent(content, res.data);
        }
      })
      .catch((err: Error) => {
        content.innerHTML = '';
        const errEl = document.createElement('div');
        errEl.textContent = `Error: ${err.message}`;
        errEl.style.color = '#f44';
        content.appendChild(errEl);
      });
  }

  /**
   * Render the dashboard tab content area: fetch data from service worker,
   * show loading state, then render content or error. (D-12: fetch on tab switch)
   * Targets the .op-seller-tab-content element inside container.
   */
  function renderDashboard(): void {
    const contentArea = container.querySelector('.op-seller-tab-content') as HTMLElement | null;
    const content = contentArea || container;

    if (contentArea) contentArea.innerHTML = '';

    // Loading state
    const loading = document.createElement('div');
    loading.textContent = 'Loading dashboard...';
    loading.style.color = '#aaa';
    loading.style.padding = '16px 0';
    content.appendChild(loading);

    // Fetch dashboard data (D-12: fetch on tab switch)
    chrome.runtime.sendMessage({ type: 'DASHBOARD_STATUS_REQUEST' } satisfies ExtensionMessage)
      .then((res: ExtensionMessage) => {
        if (res.type === 'DASHBOARD_STATUS_RESULT') {
          content.innerHTML = '';
          if (res.error || !res.data) {
            const errEl = document.createElement('div');
            errEl.textContent = `Error: ${res.error || 'No data'}`;
            errEl.style.color = '#f44';
            content.appendChild(errEl);
            return;
          }
          renderDashboardContent(content, res.data);
        }
      })
      .catch((err: Error) => {
        content.innerHTML = '';
        const errEl = document.createElement('div');
        errEl.textContent = `Error: ${err.message}`;
        errEl.style.color = '#f44';
        content.appendChild(errEl);
      });
  }

  /** Render EMPTY state: budget input + Generate button */
  function renderEmpty(): void {
    container.innerHTML = '';

    const header = document.createElement('h3');
    header.textContent = 'OP Seller';
    Object.assign(header.style, {
      margin: '0 0 16px',
      fontSize: '18px',
      color: '#fff',
    });
    container.appendChild(header);

    const input = document.createElement('input');
    input.type = 'number';
    input.placeholder = 'Budget (coins)';
    Object.assign(input.style, {
      background: '#2a2a3e',
      color: '#fff',
      border: '1px solid #444',
      padding: '8px 12px',
      width: '100%',
      borderRadius: '4px',
      boxSizing: 'border-box',
      fontSize: '14px',
    });
    container.appendChild(input);

    // ── Exclude card types ────────────────────────────────────────────────────
    const excludeLabel = document.createElement('label');
    excludeLabel.textContent = 'Exclude card types:';
    Object.assign(excludeLabel.style, { color: '#aaa', fontSize: '12px', marginTop: '12px', display: 'block' });
    container.appendChild(excludeLabel);

    const CARD_TYPES = [
      'Rare', 'Team of the Week', 'FUT Birthday', 'FUT Birthday Icon', 'FUT Birthday Hero',
      'Trophy Titans ICON', 'Trophy Titans Hero', 'Star Performer', 'Fantasy UT', 'Fantasy UT Hero',
      'FoF: Answer the Call', 'Knockout Royalty', 'Knockout Royalty Icon', 'Thunderstruck',
      'Thunderstruck ICON', 'Winter Wildcards', 'Winter Wildcards Icon', 'Time Warp', 'Time Warp Icon',
      'Unbreakables', 'Unbreakables Icon', 'Champion Icon', 'TOTY ICON',
      'Future Stars', 'Future Stars Icon', 'Cornerstones', 'Joga Bonito', 'Joga Bonito Hero',
      'TOTY Honourable Mentions', 'Ratings Reload',
      'UCL Road to the Knockouts', 'UEFA Champions League Road to the Final',
      'UEL Road to the Final', 'UECL Road to the Final',
      'UEFA Women\'s Champions League Road to the Final',
      'Festival of Football: Captains', 'Ultimate Scream Hero',
    ];

    const excludedTypes: Set<string> = new Set();

    const excludeRow = document.createElement('div');
    Object.assign(excludeRow.style, { display: 'flex', gap: '4px', marginTop: '4px' });

    const excludeSelect = document.createElement('select');
    Object.assign(excludeSelect.style, {
      flex: '1',
      background: '#2a2a3e',
      color: '#ccc',
      border: '1px solid #444',
      borderRadius: '3px',
      padding: '4px 6px',
      fontSize: '12px',
    });
    const defaultOpt = document.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent = '+ Add exclusion...';
    excludeSelect.appendChild(defaultOpt);
    CARD_TYPES.forEach(ct => {
      const opt = document.createElement('option');
      opt.value = ct;
      opt.textContent = ct;
      excludeSelect.appendChild(opt);
    });
    excludeRow.appendChild(excludeSelect);
    container.appendChild(excludeRow);

    const excludeTags = document.createElement('div');
    Object.assign(excludeTags.style, {
      display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '4px', minHeight: '0',
    });
    container.appendChild(excludeTags);

    function renderExcludeTags(): void {
      excludeTags.innerHTML = '';
      excludedTypes.forEach(ct => {
        const tag = document.createElement('span');
        tag.textContent = ct + ' ×';
        Object.assign(tag.style, {
          background: '#3a1a1a',
          color: '#ff6b6b',
          padding: '2px 8px',
          borderRadius: '3px',
          fontSize: '11px',
          cursor: 'pointer',
          border: '1px solid #ff6b6b44',
        });
        tag.addEventListener('click', () => {
          excludedTypes.delete(ct);
          renderExcludeTags();
        });
        excludeTags.appendChild(tag);
      });
    }

    excludeSelect.addEventListener('change', () => {
      if (excludeSelect.value) {
        excludedTypes.add(excludeSelect.value);
        excludeSelect.value = '';
        renderExcludeTags();
      }
    });

    // ── Generate button ─────────────────────────────────────────────────────────
    const loading = document.createElement('div');
    loading.textContent = 'Generating...';
    loading.style.display = 'none';
    loading.style.color = '#aaa';
    loading.style.marginTop = '8px';
    container.appendChild(loading);

    const generateBtn = document.createElement('button');
    generateBtn.textContent = 'Generate Portfolio';
    styleButton(generateBtn, '#4CAF50');
    container.appendChild(generateBtn);

    generateBtn.addEventListener('click', () => {
      const budget = parseInt(input.value, 10);
      if (!budget || budget <= 0) return;

      loading.style.display = 'block';
      generateBtn.disabled = true;

      // Show elapsed time so user knows it's working
      const startTime = Date.now();
      const timer = setInterval(() => {
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(0);
        loading.textContent = `Generating... (${elapsed}s)`;
      }, 1000);

      draftExcludedCardTypes = excludedTypes.size > 0 ? [...excludedTypes] : [];
      const exclude_card_types = draftExcludedCardTypes.length > 0 ? draftExcludedCardTypes : undefined;
      chrome.runtime.sendMessage({ type: 'PORTFOLIO_GENERATE', budget, exclude_card_types } satisfies ExtensionMessage)
        .then((res: ExtensionMessage) => {
          clearInterval(timer);
          loading.style.display = 'none';
          generateBtn.disabled = false;

          if (res.type === 'PORTFOLIO_GENERATE_RESULT') {
            if (res.error) {
              renderError(res.error);
              return;
            }
            setState('draft', {
              players: res.data,
              budget,
              budget_used: res.budget_used,
              budget_remaining: res.budget_remaining,
            });
          }
        })
        .catch((err: Error) => {
          clearInterval(timer);
          loading.style.display = 'none';
          generateBtn.disabled = false;
          renderError(err.message ?? 'Unknown error');
        });
    });
  }

  /** Build sortable column header bar */
  function renderSortBar(onSort: () => void): HTMLDivElement {
    const bar = document.createElement('div');
    Object.assign(bar.style, {
      display: 'flex',
      flexWrap: 'wrap',
      gap: '4px',
      marginBottom: '8px',
    });

    const columns: { label: string; key: SortKey }[] = [
      { label: 'Name', key: 'name' },
      { label: 'OVR', key: 'rating' },
      { label: 'Buy', key: 'price' },
      { label: 'Sell', key: 'sell_price' },
      { label: 'Margin', key: 'margin_pct' },
      { label: 'Profit', key: 'expected_profit' },
      { label: 'OP%', key: 'op_ratio' },
      { label: 'Eff.', key: 'efficiency' },
    ];

    columns.forEach(col => {
      const btn = document.createElement('button');
      const arrow = sortKey === col.key ? (sortDir === 'desc' ? ' \u25BC' : ' \u25B2') : '';
      btn.textContent = col.label + arrow;
      Object.assign(btn.style, {
        background: sortKey === col.key ? '#3a3a5e' : '#2a2a3e',
        color: sortKey === col.key ? '#fff' : '#aaa',
        border: '1px solid #444',
        borderRadius: '3px',
        cursor: 'pointer',
        padding: '3px 6px',
        fontSize: '11px',
        flex: '0 0 auto',
      });
      btn.addEventListener('click', () => {
        if (sortKey === col.key) {
          sortDir = sortDir === 'desc' ? 'asc' : 'desc';
        } else {
          sortKey = col.key;
          sortDir = 'desc';
        }
        onSort();
      });
      bar.appendChild(btn);
    });

    return bar;
  }

  /** Render DRAFT state: player list + swap + Confirm button */
  function renderDraft(): void {
    container.innerHTML = '';

    const header = document.createElement('h3');
    header.textContent = `Draft Portfolio (${draftPlayers.length} players)`;
    Object.assign(header.style, { margin: '0 0 8px', fontSize: '18px', color: '#fff' });
    container.appendChild(header);

    const summary = document.createElement('div');
    summary.textContent = `Used: ${fmt(draftBudgetUsed)} / Budget: ${fmt(draftBudget)} (${fmt(draftBudgetRemaining)} remaining)`;
    Object.assign(summary.style, { fontSize: '12px', color: '#aaa', marginBottom: '8px' });
    container.appendChild(summary);

    const sortBarSlot = document.createElement('div');
    container.appendChild(sortBarSlot);

    const listEl = document.createElement('div');
    container.appendChild(listEl);

    function renderPlayerList(): void {
      // Update header count
      header.textContent = `Draft Portfolio (${draftPlayers.length} players)`;

      // Update sort bar
      sortBarSlot.innerHTML = '';
      sortBarSlot.appendChild(renderSortBar(renderPlayerList));

      listEl.innerHTML = '';
      const sorted = sortPlayers(draftPlayers, sortKey, sortDir);
      sorted.forEach((player) => {
        // Find real index in draftPlayers for removal
        const idx = draftPlayers.indexOf(player);
        const row = document.createElement('div');
        row.className = 'op-seller-player-row';
        Object.assign(row.style, {
          background: '#2a2a3e',
          padding: '10px',
          marginBottom: '6px',
          borderRadius: '4px',
          position: 'relative',
        });

        const topLine = document.createElement('div');
        topLine.style.marginBottom = '4px';
        const nameLink = document.createElement('a');
        const nameLinkUrl = player.futgg_url
          ? `https://www.fut.gg${player.futgg_url}`
          : `https://www.fut.gg/players/?search=${encodeURIComponent(player.name)}`;
        nameLink.href = nameLinkUrl;
        nameLink.target = '_blank';
        nameLink.rel = 'noopener';
        nameLink.textContent = player.name;
        Object.assign(nameLink.style, { color: '#6cf', textDecoration: 'none', fontWeight: 'bold', cursor: 'pointer' });
        nameLink.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopImmediatePropagation();
          window.open(nameLinkUrl, '_blank', 'noopener');
        });
        nameLink.addEventListener('mouseenter', () => { nameLink.style.textDecoration = 'underline'; });
        nameLink.addEventListener('mouseleave', () => { nameLink.style.textDecoration = 'none'; });
        const ratingPos = document.createElement('span');
        ratingPos.textContent = ` ${player.rating} ${player.position}`;
        ratingPos.style.color = '#aaa';
        ratingPos.style.fontSize = '12px';
        topLine.appendChild(nameLink);
        topLine.appendChild(ratingPos);
        row.appendChild(topLine);

        const detailLine = document.createElement('div');
        detailLine.style.fontSize = '12px';
        detailLine.style.color = '#ccc';
        detailLine.style.marginBottom = '2px';
        detailLine.textContent = `Buy: ${fmt(player.price)} | Sell: ${fmt(player.sell_price)} | Margin: ${pct(player.margin_pct)}`;
        row.appendChild(detailLine);

        const statsLine = document.createElement('div');
        statsLine.style.fontSize = '12px';
        statsLine.style.color = '#ccc';
        statsLine.textContent = `Profit: ${fmt(player.expected_profit)} | OP: ${pct(player.op_ratio)} | Eff: ${player.efficiency.toFixed(4)}`;
        row.appendChild(statsLine);

        // Remove (X) button — triggers swap (D-08/D-09)
        const removeBtn = document.createElement('button');
        removeBtn.textContent = 'X';
        Object.assign(removeBtn.style, {
          position: 'absolute',
          top: '6px',
          right: '6px',
          background: '#444',
          color: '#fff',
          border: 'none',
          borderRadius: '3px',
          cursor: 'pointer',
          padding: '2px 6px',
          fontSize: '11px',
        });

        removeBtn.addEventListener('click', () => {
          // Track this player as banned for the entire draft session
          removedEaIds.add(player.ea_id);

          // Instant remove from in-memory draft (D-09 instant visual feedback)
          draftPlayers.splice(idx, 1);
          renderPlayerList();

          if (swapInFlight) return; // Already in-flight — banned ID is tracked, skip request

          swapInFlight = true;
          chrome.runtime.sendMessage({
            type: 'PORTFOLIO_GENERATE',
            budget: draftBudget,
            banned_ea_ids: [...removedEaIds],
            exclude_card_types: draftExcludedCardTypes.length > 0 ? draftExcludedCardTypes : undefined,
          } satisfies ExtensionMessage)
            .then((res: ExtensionMessage) => {
              if (res.type === 'PORTFOLIO_GENERATE_RESULT') {
                if (!res.error && res.data) {
                  draftPlayers = [...res.data];
                  draftBudgetUsed = res.budget_used;
                  draftBudgetRemaining = res.budget_remaining;
                  summary.textContent = `Used: ${fmt(draftBudgetUsed)} / Budget: ${fmt(draftBudget)} (${fmt(draftBudgetRemaining)} remaining)`;
                  renderPlayerList();
                }
              }
            })
            .catch(() => {
              // Regenerate failed — draft already shows removal, continue without replacement
            })
            .finally(() => {
              swapInFlight = false;
            });
        });

        row.appendChild(removeBtn);
        listEl.appendChild(row);
      });
    }

    renderPlayerList();

    const confirmBtn = document.createElement('button');
    confirmBtn.textContent = 'Confirm Portfolio';
    styleButton(confirmBtn, '#4CAF50', { marginTop: '16px' });
    container.appendChild(confirmBtn);

    confirmBtn.addEventListener('click', () => {
      confirmBtn.disabled = true;
      confirmBtn.textContent = 'Confirming...';

      chrome.runtime.sendMessage({
        type: 'PORTFOLIO_CONFIRM',
        players: draftPlayers,
      } satisfies ExtensionMessage)
        .then((res: ExtensionMessage) => {
          if (res.type === 'PORTFOLIO_CONFIRM_RESULT') {
            if (res.error) {
              confirmBtn.disabled = false;
              confirmBtn.textContent = 'Confirm Portfolio';
              renderError(res.error);
              return;
            }
            setState('confirmed', {
              players: draftPlayers,
              budget: draftBudget,
              budget_used: draftBudgetUsed,
              budget_remaining: draftBudgetRemaining,
            });
          }
        })
        .catch(() => {
          confirmBtn.disabled = false;
          confirmBtn.textContent = 'Confirm Portfolio';
        });
    });
  }

  /**
   * Render the portfolio player list into the given parent element.
   * Extracted from renderConfirmed to support tab switching.
   * Includes sort bar, player rows (read-only), and Regenerate button.
   */
  function renderPortfolioContent(parent: HTMLElement): void {
    const sortBarSlot = document.createElement('div');
    parent.appendChild(sortBarSlot);

    const listEl = document.createElement('div');
    parent.appendChild(listEl);

    function renderPlayerList(): void {
      sortBarSlot.innerHTML = '';
      sortBarSlot.appendChild(renderSortBar(renderPlayerList));

      listEl.innerHTML = '';
      const sorted = sortPlayers(draftPlayers, sortKey, sortDir);
      sorted.forEach(player => {
        const row = document.createElement('div');
        row.className = 'op-seller-player-row';
        Object.assign(row.style, {
          background: '#2a2a3e',
          padding: '10px',
          marginBottom: '6px',
          borderRadius: '4px',
          position: 'relative',
        });

        const topLine = document.createElement('div');
        topLine.style.marginBottom = '4px';
        const nameLink = document.createElement('a');
        const nameLinkUrl = player.futgg_url
          ? `https://www.fut.gg${player.futgg_url}`
          : `https://www.fut.gg/players/?search=${encodeURIComponent(player.name)}`;
        nameLink.href = nameLinkUrl;
        nameLink.target = '_blank';
        nameLink.rel = 'noopener';
        nameLink.textContent = player.name;
        Object.assign(nameLink.style, { color: '#6cf', textDecoration: 'none', fontWeight: 'bold', cursor: 'pointer' });
        nameLink.addEventListener('click', (e) => {
          e.preventDefault();
          e.stopImmediatePropagation();
          window.open(nameLinkUrl, '_blank', 'noopener');
        });
        nameLink.addEventListener('mouseenter', () => { nameLink.style.textDecoration = 'underline'; });
        nameLink.addEventListener('mouseleave', () => { nameLink.style.textDecoration = 'none'; });
        const ratingPos = document.createElement('span');
        ratingPos.textContent = ` ${player.rating} ${player.position}`;
        ratingPos.style.color = '#aaa';
        ratingPos.style.fontSize = '12px';
        topLine.appendChild(nameLink);
        topLine.appendChild(ratingPos);
        row.appendChild(topLine);

        const detailLine = document.createElement('div');
        detailLine.style.fontSize = '12px';
        detailLine.style.color = '#ccc';
        detailLine.style.marginBottom = '2px';
        detailLine.textContent = `Buy: ${fmt(player.price)} | Sell: ${fmt(player.sell_price)} | Margin: ${pct(player.margin_pct)}`;
        row.appendChild(detailLine);

        const statsLine = document.createElement('div');
        statsLine.style.fontSize = '12px';
        statsLine.style.color = '#ccc';
        statsLine.textContent = `Profit: ${fmt(player.expected_profit)} | OP: ${pct(player.op_ratio)} | Eff: ${player.efficiency.toFixed(4)}`;
        row.appendChild(statsLine);

        listEl.appendChild(row);
      });
    }

    renderPlayerList();

    const regenBtn = document.createElement('button');
    regenBtn.textContent = 'Regenerate';
    styleButton(regenBtn, '#2196F3', { marginTop: '16px' });
    parent.appendChild(regenBtn);

    regenBtn.addEventListener('click', () => {
      setState('empty');
    });
  }

  // ── Automation controls ────────────────────────────────────────────────────

  /**
   * Render the automation start/stop button, status display, and activity log
   * into the given parent element.
   *
   * D-16: Start Automation button is separate from portfolio confirm — only
   * appears after portfolio is confirmed (inside renderConfirmed).
   * UI-04: Start button dispatches custom event; content script listens.
   * D-20 / UI-02: Status display shows state badge, current action, last event.
   * D-21 / UI-05: Activity log is collapsible with timestamped entries.
   * AUTO-06: window.alert() on ERROR state so user is notified even if panel collapsed.
   */
  function renderAutomationControls(parent: HTMLElement): void {
    // ── Styles ──────────────────────────────────────────────────────────────
    const styleId = 'op-seller-automation-styles';
    if (!document.getElementById(styleId)) {
      const style = document.createElement('style');
      style.id = styleId;
      style.textContent = `
        .op-seller-automation-controls { padding: 8px 0; border-top: 1px solid #444; margin-top: 8px; }
        .op-seller-automation-btn { width: 100%; padding: 10px; border: none; border-radius: 6px; color: white; font-weight: bold; cursor: pointer; font-size: 14px; }
        .op-seller-automation-status { padding: 8px 0; }
        .op-seller-status-badge { display: inline-block; padding: 2px 8px; border-radius: 4px; color: white; font-size: 11px; font-weight: bold; text-transform: uppercase; }
        .op-seller-current-action { margin-top: 4px; font-size: 13px; color: #ccc; }
        .op-seller-last-event { margin-top: 2px; font-size: 12px; color: #999; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .op-seller-session-profit { margin-top: 4px; font-size: 13px; color: #2ecc71; }
        .op-seller-log-toggle { background: none; border: 1px solid #555; color: #aaa; padding: 4px 8px; border-radius: 4px; cursor: pointer; font-size: 11px; margin-top: 6px; }
        .op-seller-activity-log { margin-top: 4px; background: #1a1a2e; border-radius: 4px; padding: 4px; max-height: 200px; overflow-y: auto; }
        .op-seller-log-entry { font-size: 11px; color: #888; padding: 1px 4px; }
        .op-seller-log-entry .op-seller-log-timestamp { color: #666; margin-right: 6px; }
      `;
      document.head.appendChild(style);
    }

    // ── Controls section ─────────────────────────────────────────────────────
    const automationSection = document.createElement('div');
    automationSection.className = 'op-seller-automation-controls';
    parent.appendChild(automationSection);

    // ── Start/Stop button (D-16, UI-04) ──────────────────────────────────────
    const startStopBtn = document.createElement('button');
    startStopBtn.className = 'op-seller-automation-btn';

    // ── Status display (D-20, UI-02) ─────────────────────────────────────────
    const statusDiv = document.createElement('div');
    statusDiv.className = 'op-seller-automation-status';

    const badgeEl = document.createElement('span');
    badgeEl.className = 'op-seller-status-badge';

    const currentActionEl = document.createElement('div');
    currentActionEl.className = 'op-seller-current-action';

    const lastEventEl = document.createElement('div');
    lastEventEl.className = 'op-seller-last-event';

    const sessionProfitEl = document.createElement('div');
    sessionProfitEl.className = 'op-seller-session-profit';

    statusDiv.appendChild(badgeEl);
    statusDiv.appendChild(currentActionEl);
    statusDiv.appendChild(lastEventEl);
    statusDiv.appendChild(sessionProfitEl);

    // ── Activity log toggle and container (D-21, UI-05) ──────────────────────
    const logToggle = document.createElement('button');
    logToggle.textContent = 'Activity Log';
    logToggle.className = 'op-seller-log-toggle';

    const logContainer = document.createElement('div');
    logContainer.className = 'op-seller-activity-log';
    logContainer.style.display = 'none'; // Collapsed by default

    automationSection.appendChild(startStopBtn);
    automationSection.appendChild(statusDiv);
    automationSection.appendChild(logToggle);
    automationSection.appendChild(logContainer);

    // ── Helpers ───────────────────────────────────────────────────────────────

    /** Get badge background color based on automation state */
    function getBadgeColor(state: string): string {
      switch (state) {
        case 'BUYING':
        case 'LISTING':
        case 'SCANNING':
        case 'RELISTING':
          return '#2ecc71';
        case 'ERROR':
          return '#e74c3c';
        default:
          return '#666';
      }
    }

    /** Extract HH:MM:SS from ISO timestamp */
    function formatTime(iso: string): string {
      try {
        return new Date(iso).toLocaleTimeString('en-US', {
          hour12: false,
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
        });
      } catch {
        return iso.slice(11, 19) || iso;
      }
    }

    /** Render current automation status into the status DOM elements */
    function updateStatusDisplay(isRunning: boolean, state: string, currentAction: string | null, lastEvent: string | null, sessionProfit: number): void {
      // Update start/stop button
      if (isRunning) {
        startStopBtn.textContent = 'Stop Automation';
        (startStopBtn.style as CSSStyleDeclaration).background = '#e74c3c';
      } else {
        startStopBtn.textContent = 'Start Automation';
        (startStopBtn.style as CSSStyleDeclaration).background = '#2ecc71';
      }

      // Update badge
      badgeEl.textContent = state;
      (badgeEl.style as CSSStyleDeclaration).background = getBadgeColor(state);

      // Update current action
      currentActionEl.textContent = currentAction || 'Idle';

      // Update last event
      lastEventEl.textContent = lastEvent || 'No events yet';

      // Update session profit
      sessionProfitEl.textContent = `Session profit: ${sessionProfit.toLocaleString()}`;
    }

    /** Re-render log entries into the log container */
    function renderLogEntries(entries: Array<{ timestamp: string; message: string }>): void {
      logContainer.innerHTML = '';
      for (const entry of entries) {
        const row = document.createElement('div');
        row.className = 'op-seller-log-entry';
        const ts = document.createElement('span');
        ts.className = 'op-seller-log-timestamp';
        ts.textContent = formatTime(entry.timestamp);
        row.appendChild(ts);
        row.appendChild(document.createTextNode(entry.message));
        logContainer.appendChild(row);
      }
      // Auto-scroll to bottom on new entries
      logContainer.scrollTop = logContainer.scrollHeight;
    }

    // ── Initial render from storage ───────────────────────────────────────────
    automationStatusItem.getValue().then(status => {
      if (status) {
        updateStatusDisplay(status.isRunning, status.state, status.currentAction, status.lastEvent, status.sessionProfit);
      } else {
        updateStatusDisplay(false, 'IDLE', null, null, 0);
      }
    }).catch(() => {
      updateStatusDisplay(false, 'IDLE', null, null, 0);
    });

    activityLogItem.getValue().then(entries => {
      renderLogEntries(entries);
    }).catch(() => {});

    // ── Reactive updates via storage watch ────────────────────────────────────

    // Track last error message to avoid duplicate alerts
    let lastAlertedError: string | null = null;

    automationStatusItem.watch((newStatus) => {
      if (!newStatus) return;
      updateStatusDisplay(
        newStatus.isRunning,
        newStatus.state,
        newStatus.currentAction,
        newStatus.lastEvent,
        newStatus.sessionProfit,
      );

      // AUTO-06: Prominent notification on CAPTCHA/DOM failure (D-22, D-23)
      // Non-blocking toast instead of window.alert() — alert blocks the JS thread
      // which prevents start/stop buttons from working and can leave automation stuck.
      if (newStatus.state === 'ERROR' && newStatus.errorMessage && newStatus.errorMessage !== lastAlertedError) {
        lastAlertedError = newStatus.errorMessage;
        showErrorToast('[OP Seller] Automation stopped: ' + newStatus.errorMessage);
      }
    });

    activityLogItem.watch((entries) => {
      if (!entries) return;
      renderLogEntries(entries);
    });

    // ── Button click handlers ─────────────────────────────────────────────────

    startStopBtn.addEventListener('click', () => {
      automationStatusItem.getValue().then(status => {
        if (status && status.isRunning) {
          // Dispatch stop event — content script listens (UI-04)
          document.dispatchEvent(new CustomEvent('op-seller-automation-stop'));
        } else {
          // Dispatch start event — content script listens (UI-04)
          document.dispatchEvent(new CustomEvent('op-seller-automation-start'));
        }
      }).catch(() => {
        // Default to start if status unavailable
        document.dispatchEvent(new CustomEvent('op-seller-automation-start'));
      });
    });

    // ── Activity log toggle ───────────────────────────────────────────────────
    logToggle.addEventListener('click', () => {
      if (logContainer.style.display === 'none') {
        logContainer.style.display = 'block';
        logToggle.textContent = 'Hide Activity Log';
        // Scroll to bottom when expanding
        logContainer.scrollTop = logContainer.scrollHeight;
      } else {
        logContainer.style.display = 'none';
        logToggle.textContent = 'Activity Log';
      }
    });
  }

  // ── Algo tab ───────────────────────────────────────────────────────────────

  /** Interval ID for algo status polling (cleared on tab switch / destroy). */
  let algoStatusIntervalId: ReturnType<typeof setInterval> | null = null;

  /**
   * Render the Algo trading tab content into the given container element.
   * Shows budget input, start/stop buttons, status display, and positions list.
   */
  function renderAlgoTab(parent: HTMLElement): void {
    parent.innerHTML = '';

    // Clean up previous polling interval
    if (algoStatusIntervalId !== null) {
      clearInterval(algoStatusIntervalId);
      algoStatusIntervalId = null;
    }

    // ── Budget input ──────────────────────────────────────────────────────
    const budgetInput = document.createElement('input');
    budgetInput.type = 'number';
    budgetInput.placeholder = 'Budget (coins)';
    Object.assign(budgetInput.style, {
      background: '#2a2a3e',
      color: '#fff',
      border: '1px solid #444',
      padding: '8px 12px',
      width: '100%',
      borderRadius: '4px',
      boxSizing: 'border-box',
      fontSize: '14px',
      marginBottom: '8px',
    });
    parent.appendChild(budgetInput);

    // ── Credentials section ──────────────────────────────────────────────
    const credsSection = document.createElement('div');
    Object.assign(credsSection.style, {
      background: '#1e1e2e',
      padding: '10px',
      borderRadius: '4px',
      marginBottom: '12px',
      border: '1px solid #333',
    });

    const credsTitle = document.createElement('div');
    credsTitle.textContent = 'EA Login (auto-recovery)';
    Object.assign(credsTitle.style, {
      fontSize: '11px',
      color: '#888',
      marginBottom: '8px',
      textTransform: 'uppercase',
      letterSpacing: '0.5px',
    });
    credsSection.appendChild(credsTitle);

    const emailInput = document.createElement('input');
    emailInput.type = 'email';
    emailInput.placeholder = 'EA Email';
    Object.assign(emailInput.style, {
      background: '#2a2a3e',
      color: '#fff',
      border: '1px solid #444',
      padding: '6px 10px',
      width: '100%',
      borderRadius: '4px',
      boxSizing: 'border-box',
      fontSize: '13px',
      marginBottom: '6px',
    });
    credsSection.appendChild(emailInput);

    const passwordInput = document.createElement('input');
    passwordInput.type = 'password';
    passwordInput.placeholder = 'EA Password';
    Object.assign(passwordInput.style, {
      background: '#2a2a3e',
      color: '#fff',
      border: '1px solid #444',
      padding: '6px 10px',
      width: '100%',
      borderRadius: '4px',
      boxSizing: 'border-box',
      fontSize: '13px',
      marginBottom: '6px',
    });
    credsSection.appendChild(passwordInput);

    const credsBtnRow = document.createElement('div');
    Object.assign(credsBtnRow.style, { display: 'flex', gap: '8px', alignItems: 'center' });

    const saveCredsBtn = document.createElement('button');
    saveCredsBtn.textContent = 'Save';
    Object.assign(saveCredsBtn.style, {
      background: '#3498db',
      color: '#fff',
      border: 'none',
      padding: '5px 14px',
      borderRadius: '4px',
      cursor: 'pointer',
      fontSize: '12px',
    });

    const credsStatus = document.createElement('span');
    Object.assign(credsStatus.style, { fontSize: '11px', color: '#888' });

    credsBtnRow.appendChild(saveCredsBtn);
    credsBtnRow.appendChild(credsStatus);
    credsSection.appendChild(credsBtnRow);

    // Load existing credentials status
    algoCredentialsItem.getValue().then(creds => {
      if (creds) {
        emailInput.value = creds.email;
        // Don't show password — just indicate it's saved
        passwordInput.placeholder = '••••••••';
        credsStatus.textContent = 'Credentials saved';
        credsStatus.style.color = '#2ecc71';
      } else {
        credsStatus.textContent = 'Not configured';
        credsStatus.style.color = '#e74c3c';
      }
    });

    saveCredsBtn.addEventListener('click', async () => {
      const email = emailInput.value.trim();
      const password = passwordInput.value;

      if (!email) {
        credsStatus.textContent = 'Email required';
        credsStatus.style.color = '#e74c3c';
        return;
      }

      // If password is empty but we already have creds, keep the old password
      if (!password) {
        const existing = await algoCredentialsItem.getValue();
        if (existing) {
          await algoCredentialsItem.setValue({ email, password: existing.password });
          credsStatus.textContent = 'Email updated';
          credsStatus.style.color = '#2ecc71';
          return;
        }
        credsStatus.textContent = 'Password required';
        credsStatus.style.color = '#e74c3c';
        return;
      }

      await algoCredentialsItem.setValue({ email, password });
      passwordInput.value = '';
      passwordInput.placeholder = '••••••••';
      credsStatus.textContent = 'Credentials saved';
      credsStatus.style.color = '#2ecc71';
    });

    parent.appendChild(credsSection);

    // ── Change budget link (shown when algo is active) ────────────────────
    const changeBudgetLink = document.createElement('a');
    changeBudgetLink.textContent = 'Change budget';
    changeBudgetLink.href = '#';
    Object.assign(changeBudgetLink.style, {
      color: '#6cf',
      fontSize: '12px',
      display: 'none',
      marginBottom: '8px',
      cursor: 'pointer',
    });
    changeBudgetLink.addEventListener('click', (e) => {
      e.preventDefault();
      budgetInput.dataset.forceShow = '1';
      budgetInput.style.display = '';
      changeBudgetLink.style.display = 'none';
      startBtn.textContent = 'Update Budget';
    });
    parent.appendChild(changeBudgetLink);

    // ── Start / Stop buttons ──────────────────────────────────────────────
    const btnRow = document.createElement('div');
    Object.assign(btnRow.style, { display: 'flex', gap: '8px', marginBottom: '12px' });

    const startBtn = document.createElement('button');
    startBtn.textContent = 'Start Algo';
    styleButton(startBtn, '#2ecc71', { flex: '1', marginTop: '0' });

    const stopBtn = document.createElement('button');
    stopBtn.textContent = 'Stop Algo';
    styleButton(stopBtn, '#e74c3c', { flex: '1', marginTop: '0' });

    btnRow.appendChild(startBtn);
    btnRow.appendChild(stopBtn);
    parent.appendChild(btnRow);

    // ── Status display ────────────────────────────────────────────────────
    const statusDiv = document.createElement('div');
    statusDiv.className = 'op-seller-algo-status';
    Object.assign(statusDiv.style, {
      background: '#2a2a3e',
      padding: '10px',
      borderRadius: '4px',
      marginBottom: '12px',
      fontSize: '12px',
    });
    statusDiv.textContent = 'Loading status...';
    parent.appendChild(statusDiv);

    // ── Positions list ────────────────────────────────────────────────────
    const positionsDiv = document.createElement('div');
    positionsDiv.className = 'op-seller-algo-positions';
    parent.appendChild(positionsDiv);

    // ── Status rendering helper ───────────────────────────────────────────
    function renderStatus(data: AlgoStatusData): void {
      backendActive = data.is_active;
      const activeColor = data.is_active ? '#2ecc71' : '#e74c3c';
      const activeLabel = data.is_active ? 'ACTIVE' : 'INACTIVE';
      const pnlColor = data.realized_pnl >= 0 ? '#2ecc71' : '#e74c3c';

      // Hide budget input when already active unless user toggled it open
      if (data.is_active && !budgetInput.dataset.forceShow) {
        budgetInput.style.display = 'none';
        changeBudgetLink.style.display = '';
        startBtn.textContent = 'Resume Algo';
      } else {
        budgetInput.style.display = '';
        changeBudgetLink.style.display = 'none';
        startBtn.textContent = data.is_active ? 'Update Budget' : 'Start Algo';
      }

      statusDiv.innerHTML = '';

      // Row 1: Active badge + pending signals
      const row1 = document.createElement('div');
      row1.style.display = 'flex';
      row1.style.justifyContent = 'space-between';
      row1.style.marginBottom = '6px';

      const badge = document.createElement('span');
      badge.textContent = activeLabel;
      Object.assign(badge.style, {
        background: activeColor,
        color: '#fff',
        padding: '2px 8px',
        borderRadius: '4px',
        fontSize: '11px',
        fontWeight: 'bold',
      });
      row1.appendChild(badge);

      const pendingEl = document.createElement('span');
      pendingEl.textContent = `${data.pending_signals} pending signal${data.pending_signals !== 1 ? 's' : ''}`;
      pendingEl.style.color = '#aaa';
      row1.appendChild(pendingEl);
      statusDiv.appendChild(row1);

      // Row 2: Budget / Cash / P&L
      const row2 = document.createElement('div');
      row2.style.display = 'flex';
      row2.style.justifyContent = 'space-between';

      const budgetEl = document.createElement('div');
      budgetEl.innerHTML = `<div style="color:#aaa">Budget</div><div style="color:#ccc;font-weight:bold">${fmt(data.budget)}</div>`;
      row2.appendChild(budgetEl);

      const cashEl = document.createElement('div');
      cashEl.innerHTML = `<div style="color:#aaa">Cash</div><div style="color:#ccc;font-weight:bold">${fmt(data.cash)}</div>`;
      row2.appendChild(cashEl);

      const pnlEl = document.createElement('div');
      pnlEl.innerHTML = `<div style="color:#aaa">P&L</div><div style="color:${pnlColor};font-weight:bold">${data.realized_pnl >= 0 ? '+' : ''}${fmt(data.realized_pnl)}</div>`;
      row2.appendChild(pnlEl);
      statusDiv.appendChild(row2);

      // Positions list
      positionsDiv.innerHTML = '';
      if (data.positions.length === 0) {
        const emptyEl = document.createElement('div');
        emptyEl.textContent = 'No open positions.';
        emptyEl.style.color = '#666';
        emptyEl.style.fontSize = '12px';
        emptyEl.style.padding = '8px 0';
        positionsDiv.appendChild(emptyEl);
        return;
      }

      const posHeader = document.createElement('div');
      posHeader.textContent = `Positions (${data.positions.length})`;
      Object.assign(posHeader.style, { color: '#aaa', fontSize: '12px', marginBottom: '6px', fontWeight: 'bold' });
      positionsDiv.appendChild(posHeader);

      for (const pos of data.positions) {
        const row = document.createElement('div');
        Object.assign(row.style, {
          background: '#2a2a3e',
          padding: '8px 10px',
          marginBottom: '4px',
          borderRadius: '4px',
        });

        const topLine = document.createElement('div');
        topLine.style.display = 'flex';
        topLine.style.justifyContent = 'space-between';
        topLine.style.marginBottom = '2px';

        const nameEl = document.createElement('span');
        nameEl.textContent = `${pos.player_name} x${pos.quantity}`;
        Object.assign(nameEl.style, { color: '#6cf', fontWeight: 'bold', fontSize: '13px' });
        topLine.appendChild(nameEl);

        const posPnlColor = pos.unrealized_pnl >= 0 ? '#2ecc71' : '#e74c3c';
        const pnlBadge = document.createElement('span');
        pnlBadge.textContent = `${pos.unrealized_pnl >= 0 ? '+' : ''}${fmt(pos.unrealized_pnl)}`;
        Object.assign(pnlBadge.style, { color: posPnlColor, fontSize: '12px', fontWeight: 'bold' });
        topLine.appendChild(pnlBadge);
        row.appendChild(topLine);

        const detailLine = document.createElement('div');
        detailLine.style.fontSize = '11px';
        detailLine.style.color = '#aaa';
        detailLine.textContent = `Buy: ${fmt(pos.buy_price)} | Current: ${fmt(pos.current_price)}`;
        row.appendChild(detailLine);

        positionsDiv.appendChild(row);
      }
    }

    // ── Fetch and display status ──────────────────────────────────────────
    function fetchStatus(): void {
      chrome.runtime.sendMessage({ type: 'ALGO_STATUS_REQUEST' } satisfies ExtensionMessage)
        .then((res: ExtensionMessage) => {
          if (res.type === 'ALGO_STATUS_RESULT') {
            if (res.error || !res.data) {
              statusDiv.textContent = `Error: ${res.error || 'No data'}`;
              statusDiv.style.color = '#f44';
              return;
            }
            renderStatus(res.data);
            // Update budget input placeholder with current budget
            if (res.data.budget > 0) {
              budgetInput.placeholder = `Budget: ${fmt(res.data.budget)}`;
            }
          }
        })
        .catch((err: Error) => {
          statusDiv.textContent = `Error: ${err.message}`;
          statusDiv.style.color = '#f44';
        });
    }

    fetchStatus();

    // Poll status every 15s
    algoStatusIntervalId = setInterval(fetchStatus, 15_000);

    // ── Track whether backend algo is active (updated by fetchStatus) ────
    let backendActive = false;

    // ── Button handlers ───────────────────────────────────────────────────
    startBtn.addEventListener('click', () => {
      if (backendActive) {
        // Already active on backend — just start the automation loop
        document.dispatchEvent(new CustomEvent('op-seller-algo-start'));
        fetchStatus();
        return;
      }

      const budget = parseInt(budgetInput.value, 10);
      if (!budget || budget <= 0) {
        showErrorToast('Enter a valid budget before starting');
        return;
      }

      startBtn.disabled = true;
      startBtn.textContent = 'Starting...';

      chrome.runtime.sendMessage({ type: 'ALGO_START', budget } satisfies ExtensionMessage)
        .then((res: ExtensionMessage) => {
          startBtn.disabled = false;
          startBtn.textContent = 'Start Algo';
          if (res.type === 'ALGO_START_RESULT') {
            if (res.success) {
              document.dispatchEvent(new CustomEvent('op-seller-algo-start'));
              fetchStatus();
            } else {
              showErrorToast(`Algo start failed: ${res.error || 'Unknown error'}`);
            }
          }
        })
        .catch((err: Error) => {
          startBtn.disabled = false;
          startBtn.textContent = 'Start Algo';
          showErrorToast(`Algo start error: ${err.message}`);
        });
    });

    stopBtn.addEventListener('click', () => {
      stopBtn.disabled = true;
      stopBtn.textContent = 'Stopping...';

      // Always stop the automation loop
      document.dispatchEvent(new CustomEvent('op-seller-algo-stop'));

      chrome.runtime.sendMessage({ type: 'ALGO_STOP' } satisfies ExtensionMessage)
        .then((res: ExtensionMessage) => {
          stopBtn.disabled = false;
          stopBtn.textContent = 'Stop Algo';
          if (res.type === 'ALGO_STOP_RESULT') {
            if (res.success) {
              fetchStatus();
            } else {
              showErrorToast(`Algo stop failed: ${res.error || 'Unknown error'}`);
            }
          }
        })
        .catch((err: Error) => {
          stopBtn.disabled = false;
          stopBtn.textContent = 'Stop Algo';
          showErrorToast(`Algo stop error: ${err.message}`);
        });
    });
  }

  /** Render CONFIRMED state: tab bar (Portfolio / Dashboard) + content area (D-01, D-02, D-03) */
  function renderConfirmed(): void {
    container.innerHTML = '';

    const header = document.createElement('h3');
    header.textContent = `Portfolio (${draftPlayers.length} players)`;
    Object.assign(header.style, { margin: '0 0 8px', fontSize: '18px', color: '#fff' });
    container.appendChild(header);

    // Tab bar (D-01: two tabs, D-02: confirmed state only, D-03: Portfolio default)
    const tabBar = renderTabBar((tab) => {
      activeTab = tab;
      renderTabContent();
    });
    container.appendChild(tabBar);

    // Tab content area
    const contentArea = document.createElement('div');
    contentArea.className = 'op-seller-tab-content';
    container.appendChild(contentArea);

    function renderTabContent(): void {
      contentArea.innerHTML = '';
      // Update tab bar active state styling
      tabBar.querySelectorAll('button').forEach(btn => {
        const tab = (btn as HTMLButtonElement).dataset.tab;
        const isActive = tab === activeTab;
        Object.assign((btn as HTMLElement).style, {
          background: isActive ? '#3a3a5e' : 'transparent',
          color: isActive ? '#fff' : '#aaa',
          borderBottom: isActive ? '2px solid #6cf' : '2px solid transparent',
        });
      });

      // Clean up algo polling when switching away from algo tab
      if (activeTab !== 'algo' && algoStatusIntervalId !== null) {
        clearInterval(algoStatusIntervalId);
        algoStatusIntervalId = null;
      }

      // Hide the Start/Stop Automation BUTTON when on algo tab (prevents
      // accidentally starting the OP loop), but keep the status + activity log visible.
      const automationBtn = container.querySelector('.op-seller-automation-btn') as HTMLElement | null;
      if (automationBtn) {
        automationBtn.style.display = activeTab === 'algo' ? 'none' : '';
      }

      if (activeTab === 'actions') {
        renderActions();
      } else if (activeTab === 'portfolio') {
        renderPortfolioContent(contentArea);
      } else if (activeTab === 'algo') {
        renderAlgoTab(contentArea);
      } else {
        renderDashboard();
      }
    }

    renderTabContent();

    // Automation controls — persistent footer visible in all confirmed tabs (D-16)
    renderAutomationControls(container);
  }

  /** Render an inline error message inside the panel */
  function renderError(message: string): void {
    const errEl = document.createElement('div');
    errEl.textContent = `Error: ${message}`;
    Object.assign(errEl.style, {
      color: '#f44',
      fontSize: '13px',
      marginTop: '8px',
      padding: '8px',
      background: '#2a1a1a',
      borderRadius: '4px',
    });
    container.appendChild(errEl);
  }

  // ── Public setState ────────────────────────────────────────────────────────

  /**
   * Switch the panel to the target state.
   *
   * @param state  - 'empty' | 'draft' | 'confirmed'
   * @param data   - Required for 'draft' and 'confirmed'; optional for 'empty'
   */
  function setState(state: PanelState, data?: PanelStateData): void {
    currentState = state;

    if (data) {
      draftPlayers = [...data.players];
      draftBudget = data.budget;
      draftBudgetUsed = data.budget_used;
      draftBudgetRemaining = data.budget_remaining;
    }

    switch (state) {
      case 'empty':
        draftPlayers = [];
        removedEaIds = new Set();  // Clear removed players for fresh generation
        swapInFlight = false;
        renderEmpty();
        break;
      case 'draft':
        renderDraft();
        break;
      case 'confirmed':
        renderConfirmed();
        break;
      default: {
        // Exhaustive guard — TypeScript will flag missing cases
        const _exhaustive: never = state;
        throw new Error(`Unknown panel state: ${_exhaustive}`);
      }
    }
  }

  // ── Destroy ────────────────────────────────────────────────────────────────

  function destroy(): void {
    container.remove();
    toggle.remove();
  }

  // ── Initial render ─────────────────────────────────────────────────────────

  renderEmpty();

  return { container, toggle, setState, destroy };
}
