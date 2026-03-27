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
 */

import type { PortfolioPlayer } from '../storage';
import type { ExtensionMessage } from '../messages';

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
  let draftBudget = 0;
  let draftBudgetUsed = 0;
  let draftBudgetRemaining = 0;
  let sortKey: SortKey = 'efficiency';
  let sortDir: SortDir = 'desc';

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

      chrome.runtime.sendMessage({ type: 'PORTFOLIO_GENERATE', budget } satisfies ExtensionMessage)
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
        const nameEl = document.createElement('strong');
        nameEl.textContent = player.name;
        nameEl.style.color = '#fff';
        const ratingPos = document.createElement('span');
        ratingPos.textContent = ` ${player.rating} ${player.position}`;
        ratingPos.style.color = '#aaa';
        ratingPos.style.fontSize = '12px';
        topLine.appendChild(nameEl);
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
          const freed_budget = player.price;
          // Exclude all remaining players AND the removed player so it can't come back
          const excluded_ea_ids = draftPlayers.map(p => p.ea_id);

          // Instant remove from in-memory draft (D-09)
          draftPlayers.splice(idx, 1);
          renderPlayerList();

          chrome.runtime.sendMessage({
            type: 'PORTFOLIO_SWAP',
            ea_id: player.ea_id,
            freed_budget,
            excluded_ea_ids,
          } satisfies ExtensionMessage)
            .then((res: ExtensionMessage) => {
              if (res.type === 'PORTFOLIO_SWAP_RESULT') {
                if (res.replacements && res.replacements.length > 0) {
                  const insertIdx = Math.min(idx, draftPlayers.length);
                  draftPlayers.splice(insertIdx, 0, ...res.replacements);
                  draftBudgetUsed = draftPlayers.reduce((s, p) => s + p.price, 0);
                  draftBudgetRemaining = Math.max(0, draftBudget - draftBudgetUsed);
                  summary.textContent = `Used: ${fmt(draftBudgetUsed)} / Budget: ${fmt(draftBudget)} (${fmt(draftBudgetRemaining)} remaining)`;
                  renderPlayerList();
                }
              }
            })
            .catch(() => {
              // Swap failed — draft already updated (player removed), continue without replacement
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

  /** Render CONFIRMED state: read-only player list + Regenerate button */
  function renderConfirmed(): void {
    container.innerHTML = '';

    const header = document.createElement('h3');
    header.textContent = `Portfolio (${draftPlayers.length} players)`;
    Object.assign(header.style, { margin: '0 0 8px', fontSize: '18px', color: '#fff' });
    container.appendChild(header);

    const sortBarSlot = document.createElement('div');
    container.appendChild(sortBarSlot);

    const listEl = document.createElement('div');
    container.appendChild(listEl);

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
        const nameEl = document.createElement('strong');
        nameEl.textContent = player.name;
        nameEl.style.color = '#fff';
        const ratingPos = document.createElement('span');
        ratingPos.textContent = ` ${player.rating} ${player.position}`;
        ratingPos.style.color = '#aaa';
        ratingPos.style.fontSize = '12px';
        topLine.appendChild(nameEl);
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

        container.appendChild(row);
      });
    }

    renderPlayerList();

    const regenBtn = document.createElement('button');
    regenBtn.textContent = 'Regenerate';
    styleButton(regenBtn, '#2196F3', { marginTop: '16px' });
    container.appendChild(regenBtn);

    regenBtn.addEventListener('click', () => {
      setState('empty');
    });
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
