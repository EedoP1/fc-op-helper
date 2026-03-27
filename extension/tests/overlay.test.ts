/**
 * Unit tests for the overlay panel module.
 * Runs in jsdom environment (configured in vitest.config.ts).
 * Tests cover panel creation, state transitions, player rendering, and destroy.
 */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { createOverlayPanel } from '../src/overlay/panel';

// ── Mock chrome.runtime.sendMessage ───────────────────────────────────────────

const mockSendMessage = vi.fn();

// WXT testing exposes fakeBrowser but overlay tests only need sendMessage.
// We stub the global chrome object directly since panel.ts calls
// chrome.runtime.sendMessage.
vi.stubGlobal('chrome', {
  runtime: {
    sendMessage: mockSendMessage,
  },
});

// ── Shared test player data ────────────────────────────────────────────────────

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

const PLAYER_B = {
  ea_id: 200,
  name: 'Cristiano Ronaldo',
  rating: 90,
  position: 'ST',
  price: 45000,
  sell_price: 63000,
  margin_pct: 0.4,
  expected_profit: 12600,
  op_ratio: 0.45,
  efficiency: 0.28,
};

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('overlay panel', () => {
  beforeEach(() => {
    // Reset DOM between tests
    document.body.innerHTML = '';
    mockSendMessage.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('creates container div with op-seller-panel class and toggle button', () => {
    const panel = createOverlayPanel();

    expect(panel.container).toBeInstanceOf(HTMLDivElement);
    expect(panel.toggle).toBeInstanceOf(HTMLButtonElement);
    expect(panel.container.className).toBe('op-seller-panel');
    expect(panel.toggle.className).toBe('op-seller-toggle');
  });

  it('container has z-index 999999 applied', () => {
    const panel = createOverlayPanel();
    // z-index is applied via inline style (string value)
    expect(panel.container.style.zIndex).toBe('999999');
  });

  it('panel starts in empty state with budget input and Generate button', () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    const input = panel.container.querySelector('input[type="number"]');
    expect(input).toBeTruthy();
    expect((input as HTMLInputElement).placeholder).toBe('Budget (coins)');

    const buttons = panel.container.querySelectorAll('button');
    const generateBtn = Array.from(buttons).find(b => b.textContent?.includes('Generate'));
    expect(generateBtn).toBeTruthy();
  });

  it('setState draft renders player rows with player name and details', () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    panel.setState('draft', {
      players: [PLAYER_A, PLAYER_B],
      budget: 200000,
      budget_used: 95000,
      budget_remaining: 105000,
    });

    const rows = panel.container.querySelectorAll('.op-seller-player-row');
    expect(rows.length).toBe(2);

    const text = panel.container.textContent ?? '';
    expect(text).toContain('Lionel Messi');
    expect(text).toContain('Cristiano Ronaldo');
    // Detail fields
    expect(text).toContain('91 RW');
    expect(text).toContain('50,000');  // buy price formatted
    expect(text).toContain('Confirm Portfolio');
  });

  it('setState draft includes X remove buttons on each player row', () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    panel.setState('draft', {
      players: [PLAYER_A, PLAYER_B],
      budget: 200000,
      budget_used: 95000,
      budget_remaining: 105000,
    });

    const rows = panel.container.querySelectorAll('.op-seller-player-row');
    rows.forEach(row => {
      const removeBtn = row.querySelector('button');
      expect(removeBtn).toBeTruthy();
      expect(removeBtn?.textContent).toBe('X');
    });
  });

  it('setState confirmed renders player list without X remove buttons', () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    panel.setState('confirmed', {
      players: [PLAYER_A],
      budget: 100000,
      budget_used: 50000,
      budget_remaining: 50000,
    });

    // No remove buttons in confirmed state
    const rows = panel.container.querySelectorAll('.op-seller-player-row');
    expect(rows.length).toBe(1);

    rows.forEach(row => {
      const removeBtn = row.querySelector('button');
      // confirmed rows have no buttons
      expect(removeBtn).toBeNull();
    });

    // Regenerate button should be present
    const allButtons = panel.container.querySelectorAll('button');
    const regenBtn = Array.from(allButtons).find(b => b.textContent?.includes('Regenerate'));
    expect(regenBtn).toBeTruthy();
  });

  it('setState confirmed shows Portfolio (Confirmed) header', () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    panel.setState('confirmed', {
      players: [PLAYER_A],
      budget: 100000,
      budget_used: 50000,
      budget_remaining: 50000,
    });

    const header = panel.container.querySelector('h3');
    expect(header?.textContent).toBe('Portfolio (1 players)');
  });

  it('destroy removes container and toggle from DOM', () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);
    document.body.appendChild(panel.toggle);

    expect(document.body.contains(panel.container)).toBe(true);
    expect(document.body.contains(panel.toggle)).toBe(true);

    panel.destroy();

    expect(document.body.contains(panel.container)).toBe(false);
    expect(document.body.contains(panel.toggle)).toBe(false);
  });

  it('toggle button opens and closes the panel by toggling transform', () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);
    document.body.appendChild(panel.toggle);

    // Initially closed — panel is off-screen
    expect(panel.container.style.transform).toBe('translateX(100%)');
    expect(panel.toggle.textContent).toBe('OP');

    // Click to open
    panel.toggle.click();
    expect(panel.container.style.transform).toBe('translateX(0)');
    expect(panel.toggle.textContent).toBe('X');

    // Click to close
    panel.toggle.click();
    expect(panel.container.style.transform).toBe('translateX(100%)');
    expect(panel.toggle.textContent).toBe('OP');
  });

  it('clicking X on draft player row sends PORTFOLIO_SWAP message', async () => {
    mockSendMessage.mockResolvedValue({
      type: 'PORTFOLIO_SWAP_RESULT',
      replacements: [],
    });

    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    panel.setState('draft', {
      players: [PLAYER_A, PLAYER_B],
      budget: 200000,
      budget_used: 95000,
      budget_remaining: 105000,
    });

    // Click the X button on the first player row
    const firstRow = panel.container.querySelector('.op-seller-player-row');
    const removeBtn = firstRow?.querySelector('button') as HTMLButtonElement;
    expect(removeBtn).toBeTruthy();
    removeBtn.click();

    // Player A row should be removed immediately (D-09 instant remove)
    const text = panel.container.textContent ?? '';
    expect(text).not.toContain('Lionel Messi');

    // Wait for the async sendMessage call
    await new Promise(r => setTimeout(r, 10));

    expect(mockSendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'PORTFOLIO_SWAP',
        ea_id: PLAYER_A.ea_id,
        freed_budget: PLAYER_A.price,
      }),
    );
  });

  it('clicking Regenerate in confirmed state returns to empty state', () => {
    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    panel.setState('confirmed', {
      players: [PLAYER_A],
      budget: 100000,
      budget_used: 50000,
      budget_remaining: 50000,
    });

    const allButtons = panel.container.querySelectorAll('button');
    const regenBtn = Array.from(allButtons).find(b => b.textContent?.includes('Regenerate')) as HTMLButtonElement;
    expect(regenBtn).toBeTruthy();

    regenBtn.click();

    // Should now be in empty state
    const input = panel.container.querySelector('input[type="number"]');
    expect(input).toBeTruthy();
  });

  it('panel sends PORTFOLIO_GENERATE on Generate button click with budget value', async () => {
    mockSendMessage.mockResolvedValue({
      type: 'PORTFOLIO_GENERATE_RESULT',
      data: [PLAYER_A],
      budget_used: 50000,
      budget_remaining: 150000,
    });

    const panel = createOverlayPanel();
    document.body.appendChild(panel.container);

    const input = panel.container.querySelector('input[type="number"]') as HTMLInputElement;
    input.value = '200000';

    const generateBtn = Array.from(panel.container.querySelectorAll('button'))
      .find(b => b.textContent?.includes('Generate')) as HTMLButtonElement;
    generateBtn.click();

    await new Promise(r => setTimeout(r, 10));

    expect(mockSendMessage).toHaveBeenCalledWith(
      expect.objectContaining({
        type: 'PORTFOLIO_GENERATE',
        budget: 200000,
      }),
    );

    // Panel should switch to draft state after successful generate
    const rows = panel.container.querySelectorAll('.op-seller-player-row');
    expect(rows.length).toBe(1);
  });
});
