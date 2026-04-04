/**
 * Unit tests for waitForSearchResults() in automation.ts.
 * Runs in jsdom environment (configured in vitest.config.ts).
 *
 * Tests cover:
 *   - 'results' outcome: .listFUTItem items present inside the results list
 *   - 'empty' outcome: EA no-results indicator (.ut-no-results-view) present
 *   - 'timeout' outcome: neither condition met within timeoutMs
 *   - Early exit: resolves well before timeout when condition is met immediately
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { waitForSearchResults } from '../src/automation';

// ── Helpers ─────────────────────────��──────────────────────────��──────────────

function makeContainer(): HTMLElement {
  const div = document.createElement('div');
  document.body.appendChild(div);
  return div;
}

// ── Tests ──────────────────────────────────────���──────────────────────────────

describe('waitForSearchResults', () => {
  let container: HTMLElement;

  beforeEach(() => {
    document.body.innerHTML = '';
    container = makeContainer();
  });

  it('returns { outcome: "results" } when .listFUTItem items appear inside the results list', async () => {
    // Build the EA results list structure
    const list = document.createElement('div');
    list.className = 'paginated-item-list ut-pinned-list';
    const item = document.createElement('div');
    item.className = 'listFUTItem';
    list.appendChild(item);
    container.appendChild(list);

    const result = await waitForSearchResults(container, 15000, 200);
    expect(result).toEqual({ outcome: 'results' });
  });

  it('returns { outcome: "empty" } when the no-results indicator exists', async () => {
    const noResults = document.createElement('div');
    noResults.className = 'ut-no-results-view';
    container.appendChild(noResults);

    const result = await waitForSearchResults(container, 15000, 200);
    expect(result).toEqual({ outcome: 'empty' });
  });

  it('returns { outcome: "timeout" } when neither condition is met within timeoutMs', async () => {
    // Empty container — no results list and no no-results indicator
    const result = await waitForSearchResults(container, 300, 100);
    expect(result).toEqual({ outcome: 'timeout' });
  });

  it('returns before the timeout expires when results are detected immediately', async () => {
    const list = document.createElement('div');
    list.className = 'paginated-item-list ut-pinned-list';
    const item = document.createElement('div');
    item.className = 'listFUTItem';
    list.appendChild(item);
    container.appendChild(list);

    const start = Date.now();
    await waitForSearchResults(container, 15000, 200);
    const elapsed = Date.now() - start;

    // Should resolve in first poll (200ms) — well under the 15s timeout
    expect(elapsed).toBeLessThan(1000);
  });
});
