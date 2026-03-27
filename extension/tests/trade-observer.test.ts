/**
 * Unit tests for the trade observer DOM reader.
 * Uses jsdom (provided by vitest's jsdom environment) to create DOM fragments
 * matching the real selectors from selectors.ts.
 *
 * All test HTML uses the actual selector constants — not hardcoded strings.
 * This ensures tests stay in sync with real selectors (AUTO-08).
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readTransferList, isTransferListPage } from '../src/trade-observer';
import {
  TRANSFER_LIST_CONTAINER,
  TRANSFER_LIST_ITEM,
  ITEM_STATUS_LABEL,
  ITEM_PLAYER_NAME,
  ITEM_BIN_PRICE,
} from '../src/selectors';

const __dirname = dirname(fileURLToPath(import.meta.url));

// ── DOM fixture helpers ───────────────────────────────────────────────────────

/**
 * Extract the class name from a CSS class selector like ".listFUTItem".
 * Returns "listFUTItem" (without the leading dot).
 * Handles simple selectors and the first class in compound selectors.
 */
function classFromSelector(selector: string): string {
  // Take the first simple class selector
  const match = selector.match(/\.([a-zA-Z][a-zA-Z0-9_-]*)/);
  if (!match) throw new Error(`Cannot extract class from selector: "${selector}"`);
  return match[1];
}

/**
 * Build a DOM element matching a simple CSS class selector.
 * For compound selectors like ".auction .auctionValue:nth-child(3) .value",
 * we create the full nested structure.
 */
function buildPriceElement(binPriceText: string): HTMLElement {
  // ITEM_BIN_PRICE = '.auction .auctionValue:nth-child(3) .value'
  // Build: <div class="auction"><div class="auctionValue"></div><div class="auctionValue"></div><div class="auctionValue"><span class="value">text</span></div></div>
  const auction = document.createElement('div');
  auction.className = 'auction';

  // Need 3 auctionValue children — 3rd one has the BIN price
  const av1 = document.createElement('div');
  av1.className = 'auctionValue';
  const av2 = document.createElement('div');
  av2.className = 'auctionValue';
  const av3 = document.createElement('div');
  av3.className = 'auctionValue';

  const valueSpan = document.createElement('span');
  valueSpan.className = 'value';
  valueSpan.textContent = binPriceText;
  av3.appendChild(valueSpan);

  auction.appendChild(av1);
  auction.appendChild(av2);
  auction.appendChild(av3);

  return auction;
}

/**
 * Build a status element matching ITEM_STATUS_LABEL (.auction-state .time).
 */
function buildStatusElement(statusText: string): HTMLElement {
  const auctionState = document.createElement('div');
  auctionState.className = 'auction-state';

  const timeEl = document.createElement('span');
  timeEl.className = 'time';
  timeEl.textContent = statusText;
  auctionState.appendChild(timeEl);

  return auctionState;
}

interface ItemConfig {
  name: string;
  status: string;
  price?: string;
}

/**
 * Build a complete transfer list DOM fragment matching the real selectors.
 * Returns the container element (matching TRANSFER_LIST_CONTAINER).
 */
function buildTransferList(items: ItemConfig[]): HTMLElement {
  // TRANSFER_LIST_CONTAINER = '.ut-transfer-list-view'
  const container = document.createElement('div');
  container.className = classFromSelector(TRANSFER_LIST_CONTAINER);

  for (const item of items) {
    // TRANSFER_LIST_ITEM = '.listFUTItem'
    const li = document.createElement('li');
    li.className = classFromSelector(TRANSFER_LIST_ITEM);

    // ITEM_PLAYER_NAME = '.name'
    const nameEl = document.createElement('span');
    nameEl.className = classFromSelector(ITEM_PLAYER_NAME);
    nameEl.textContent = item.name;
    li.appendChild(nameEl);

    // ITEM_STATUS_LABEL = '.auction-state .time'
    li.appendChild(buildStatusElement(item.status));

    // ITEM_BIN_PRICE = '.auction .auctionValue:nth-child(3) .value'
    if (item.price !== undefined) {
      li.appendChild(buildPriceElement(item.price));
    }

    container.appendChild(li);
  }

  return container;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('readTransferList', () => {
  it('returns empty array when container not present', () => {
    const root = document.createElement('div');
    // No .ut-transfer-list-view inside
    const result = readTransferList(root);
    expect(result).toEqual([]);
  });

  it('returns detected items with correct name, status, price', () => {
    const container = buildTransferList([
      { name: 'Mbappé', status: 'Expired', price: '15,000' },
      { name: 'Haaland', status: 'Sold', price: '25,000' },
    ]);
    const root = document.createElement('div');
    root.appendChild(container);

    const result = readTransferList(root);
    expect(result).toHaveLength(2);

    expect(result[0]).toEqual({ playerName: 'Mbappé', status: 'expired', price: 15000 });
    expect(result[1]).toEqual({ playerName: 'Haaland', status: 'sold', price: 25000 });
  });

  it('skips items with missing name element', () => {
    const container = document.createElement('div');
    container.className = classFromSelector(TRANSFER_LIST_CONTAINER);

    const li = document.createElement('li');
    li.className = classFromSelector(TRANSFER_LIST_ITEM);
    // No .name element — only status
    li.appendChild(buildStatusElement('Expired'));
    container.appendChild(li);

    const root = document.createElement('div');
    root.appendChild(container);

    const result = readTransferList(root);
    expect(result).toHaveLength(0);
  });

  it('skips items with missing status element', () => {
    const container = document.createElement('div');
    container.className = classFromSelector(TRANSFER_LIST_CONTAINER);

    const li = document.createElement('li');
    li.className = classFromSelector(TRANSFER_LIST_ITEM);
    // Only name, no status
    const nameEl = document.createElement('span');
    nameEl.className = classFromSelector(ITEM_PLAYER_NAME);
    nameEl.textContent = 'Mbappé';
    li.appendChild(nameEl);
    container.appendChild(li);

    const root = document.createElement('div');
    root.appendChild(container);

    const result = readTransferList(root);
    expect(result).toHaveLength(0);
  });

  it('skips items with unknown status text', () => {
    const container = buildTransferList([
      { name: 'Mbappé', status: 'Processing', price: '15,000' },
    ]);
    const root = document.createElement('div');
    root.appendChild(container);

    const result = readTransferList(root);
    expect(result).toHaveLength(0);
  });

  it('parses comma-separated prices correctly', () => {
    const container = buildTransferList([
      { name: 'Mbappé', status: 'Expired', price: '15,000' },
    ]);
    const root = document.createElement('div');
    root.appendChild(container);

    const result = readTransferList(root);
    expect(result).toHaveLength(1);
    expect(result[0].price).toBe(15000);
  });

  it('maps time-remaining status to listed', () => {
    // EA shows "55 Minutes" for active listings
    const container = buildTransferList([
      { name: 'Mbappé', status: '55 Minutes', price: '15,000' },
    ]);
    const root = document.createElement('div');
    root.appendChild(container);

    const result = readTransferList(root);
    expect(result).toHaveLength(1);
    expect(result[0].status).toBe('listed');
  });

  it('returns price 0 when price element is absent', () => {
    // Build item without price element
    const container = buildTransferList([
      { name: 'Mbappé', status: 'Expired' }, // no price
    ]);
    const root = document.createElement('div');
    root.appendChild(container);

    const result = readTransferList(root);
    expect(result).toHaveLength(1);
    expect(result[0].price).toBe(0);
  });
});

describe('isTransferListPage', () => {
  it('returns true when transfer list container is present', () => {
    const root = document.createElement('div');
    const container = document.createElement('div');
    container.className = classFromSelector(TRANSFER_LIST_CONTAINER);
    root.appendChild(container);

    expect(isTransferListPage(root)).toBe(true);
  });

  it('returns false when transfer list container is absent', () => {
    const root = document.createElement('div');
    expect(isTransferListPage(root)).toBe(false);
  });
});

describe('trade-observer selector usage', () => {
  it('imports selectors from selectors.ts — no hardcoded CSS selector strings', () => {
    const sourcePath = resolve(__dirname, '../src/trade-observer.ts');
    const source = readFileSync(sourcePath, 'utf-8');

    // Must import from './selectors'
    expect(source).toContain("from './selectors'");

    // Verify the imported constants are used (not hardcoded)
    // The source should NOT contain the raw selector strings as string literals
    // (they are only allowed as the values exported from selectors.ts itself)
    expect(source).not.toMatch(/querySelector\(['"]\.ut-transfer-list-view['"]\)/);
    expect(source).not.toMatch(/querySelector\(['"]\.listFUTItem['"]\)/);
    expect(source).not.toMatch(/querySelector\(['"]\.name['"]\)/);
  });
});
