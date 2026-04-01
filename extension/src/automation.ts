/**
 * Automation engine for the OP Sell cycle.
 *
 * Exports:
 *   - AutomationError       — typed error with selectorName (AUTO-07)
 *   - requireElement()      — querySelector + throw on miss (AUTO-07, D-23)
 *   - jitter()              — random delay 800-2500ms, no two consecutive identical (AUTO-05, D-28)
 *   - typePrice()           — per-digit input event dispatch (D-27)
 *   - clickElement()        — full PointerEvent+MouseEvent sequence (verified live, selectors.ts)
 *   - waitForElement()      — polling querySelector with timeout (AUTO-07)
 *   - AutomationEngine      — state machine for buy/list/relist cycle (AUTO-05, AUTO-06)
 *
 * Click note (from selectors.ts): EA Web App ignores programmatic .click() on most buttons.
 * Use the full pointer event sequence (implemented in clickElement):
 *   pointerdown → mousedown → pointerup → mouseup → click
 * This was verified live: search, buy, list, clear sold, re-list all confirmed working.
 */
import type { AutomationStatusData } from './messages';
import type { AutomationStatus, ActivityLogEntry } from './storage';
import { automationStatusItem, activityLogItem } from './storage';

// ── Error type ────────────────────────────────────────────────────────────────

/**
 * Error thrown when a required DOM element cannot be found.
 * selectorName is the human-readable constant name (e.g. "SEARCH_PLAYER_NAME_INPUT")
 * for error messages — NOT the raw CSS selector string.
 * (AUTO-07, D-23)
 */
export class AutomationError extends Error {
  constructor(public selectorName: string, message: string) {
    super(message);
    this.name = 'AutomationError';
  }
}

// ── DOM helpers ───────────────────────────────────────────────────────────────

/**
 * Query a CSS selector and throw AutomationError if not found.
 * selectorName is the human-readable constant name (e.g. "SEARCH_PLAYER_NAME_INPUT")
 * for error messages. selector is the actual CSS string.
 * (AUTO-07, D-23)
 */
export function requireElement<T extends Element = Element>(
  selectorName: string,
  selector: string,
  root: Document | Element = document,
): T {
  const el = root.querySelector<T>(selector);
  if (!el) {
    throw new AutomationError(
      selectorName,
      `DOM mismatch: element not found for "${selectorName}" (selector: ${selector})`,
    );
  }
  return el;
}

/**
 * Return a promise that resolves after a random delay between minMs and maxMs.
 * Default range: 800-2500ms (AUTO-05).
 * Guarantees no two consecutive calls return the same delay (AUTO-05: no two identical).
 */
let lastJitterDelay = 0;
export function jitter(minMs = 800, maxMs = 2500): Promise<void> {
  let delay: number;
  do {
    delay = Math.floor(Math.random() * (maxMs - minMs + 1)) + minMs;
  } while (delay === lastJitterDelay);  // AUTO-05: no two consecutive identical
  lastJitterDelay = delay;
  return new Promise(resolve => setTimeout(resolve, delay));
}

/**
 * Clear the input and type each digit with small delays (D-27).
 * Dispatches per-digit input events + final change event so EA's SPA framework
 * picks up the value. Does NOT use clipboardData or keyboard events.
 */
export async function typePrice(input: HTMLInputElement, price: number): Promise<void> {
  input.focus();
  input.value = '';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  await jitter(100, 300);

  for (const char of String(price)) {
    input.value += char;
    input.dispatchEvent(new Event('input', { bubbles: true }));
    await jitter(50, 150);
  }
  input.dispatchEvent(new Event('change', { bubbles: true }));
}

/**
 * Click an element using the full PointerEvent+MouseEvent sequence.
 * EA Web App ignores programmatic .click() on most buttons — this sequence
 * was verified live for search, buy, list, clear sold, and re-list actions.
 * (selectors.ts click note)
 */
export async function clickElement(el: Element): Promise<void> {
  await jitter();
  el.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true }));
  el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
  el.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true }));
  el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
  el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
}

/**
 * Poll for a selector to appear, with timeout.
 * Used after clicking buttons to wait for the next DOM state.
 * Returns the element if found, throws AutomationError with timeout message on timeout.
 * (AUTO-07)
 */
export async function waitForElement<T extends Element = Element>(
  selectorName: string,
  selector: string,
  root: Document | Element = document,
  timeoutMs = 5000,
  pollMs = 200,
): Promise<T> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const el = root.querySelector<T>(selector);
    if (el) return el;
    await new Promise(r => setTimeout(r, pollMs));
  }
  throw new AutomationError(
    selectorName,
    `Timeout waiting for "${selectorName}" (selector: ${selector}) after ${timeoutMs}ms — possible CAPTCHA or DOM change`,
  );
}

// ── State machine ─────────────────────────────────────────────────────────────

/** All valid automation engine states. */
export type AutomationState = 'IDLE' | 'BUYING' | 'LISTING' | 'SCANNING' | 'RELISTING' | 'STOPPED' | 'ERROR';

/**
 * Automation engine state machine for the buy/list/relist cycle.
 *
 * Manages state transitions, persists status to chrome.storage.local so the
 * overlay panel can read it after service worker restarts, and keeps an
 * activity log capped at 200 entries.
 *
 * Actual cycle methods (executeBuyCycle, executeListCycle, etc.) will be
 * added in Plans 03 and 04.
 *
 * (AUTO-05, AUTO-06)
 */
export class AutomationEngine {
  private state: AutomationState = 'IDLE';
  private isRunning = false;
  private currentAction: string | null = null;
  private lastEvent: string | null = null;
  private sessionProfit = 0;
  private errorMessage: string | null = null;
  private abortController: AbortController | null = null;

  /**
   * @param sendMessage — callback to send messages to the service worker.
   *   Injected by the content script so the engine is testable without a real
   *   chrome.runtime reference.
   */
  constructor(
    private sendMessage: (msg: any) => Promise<any>,
  ) {}

  /** Return current status as an AutomationStatusData snapshot. */
  getStatus(): AutomationStatusData {
    return {
      isRunning: this.isRunning,
      state: this.state,
      currentAction: this.currentAction,
      lastEvent: this.lastEvent,
      sessionProfit: this.sessionProfit,
      errorMessage: this.errorMessage,
    };
  }

  /**
   * Start the automation engine.
   * Sets isRunning=true, creates a fresh AbortController, persists status.
   * Returns { success: false, error } if already running.
   * The actual cycle loop is wired in Plan 05 (content script integration).
   */
  async start(): Promise<{ success: boolean; error?: string }> {
    if (this.isRunning) return { success: false, error: 'Already running' };
    // Abort any lingering controller from a previous error/stop so old loop
    // timeouts see isStopping=true when they resolve (prevents ghost loops).
    this.abortController?.abort();
    this.isRunning = true;
    this.state = 'IDLE';
    this.errorMessage = null;
    this.abortController = new AbortController();
    await this.persistStatus();
    await this.log('Automation started');
    return { success: true };
  }

  /**
   * Stop the automation engine.
   * Signals the AbortController so the cycle loop can exit gracefully (D-17).
   * Returns { success: true } even if already stopped.
   */
  async stop(): Promise<{ success: boolean }> {
    // Always abort the controller — even if isRunning is already false (e.g., after
    // setError), there may be pending timeouts from the old loop that need to see
    // the signal as aborted. Also reset error state so the UI shows STOPPED, not ERROR.
    this.abortController?.abort();
    if (!this.isRunning && this.state !== 'ERROR') return { success: true };
    this.isRunning = false;
    this.state = 'STOPPED';
    this.currentAction = null;
    this.errorMessage = null;
    await this.persistStatus();
    await this.log('Automation stopped');
    return { success: true };
  }

  /** True when a stop has been requested but the cycle has not yet exited. */
  get isStopping(): boolean {
    return this.abortController?.signal.aborted ?? false;
  }

  /**
   * Return the current AbortSignal so the loop can capture it at start.
   * Each loop invocation checks ITS OWN captured signal — not the engine's
   * current one — preventing ghost loops when start() replaces the controller.
   */
  getAbortSignal(): AbortSignal | undefined {
    return this.abortController?.signal;
  }

  /** Record an error, set state to ERROR, abort the loop, and persist. */
  async setError(message: string): Promise<void> {
    this.abortController?.abort();
    this.isRunning = false;
    this.state = 'ERROR';
    this.errorMessage = message;
    this.currentAction = null;
    await this.persistStatus();
    await this.log(`ERROR: ${message}`);
  }

  /** Transition to a new state, optionally updating currentAction. */
  async setState(state: AutomationState, action?: string): Promise<void> {
    this.state = state;
    if (action !== undefined) this.currentAction = action;
    await this.persistStatus();
  }

  /** Update lastEvent, persist, and append to activity log. */
  async setLastEvent(event: string): Promise<void> {
    this.lastEvent = event;
    await this.persistStatus();
    await this.log(event);
  }

  /** Accumulate realized profit for this session. */
  addProfit(amount: number): void {
    this.sessionProfit += amount;
  }

  /** Write current state to chrome.storage.local so the overlay can read it. */
  private async persistStatus(): Promise<void> {
    const status: AutomationStatus = {
      isRunning: this.isRunning,
      state: this.state,
      currentAction: this.currentAction,
      lastEvent: this.lastEvent,
      sessionProfit: this.sessionProfit,
      errorMessage: this.errorMessage,
    };
    await automationStatusItem.setValue(status);
  }

  /** Append a timestamped message to the activity log, capped at 200 entries. */
  async log(message: string): Promise<void> {
    const entries: ActivityLogEntry[] = await activityLogItem.getValue();
    entries.push({ timestamp: new Date().toISOString(), message });
    // Keep last 200 entries to avoid storage bloat
    if (entries.length > 200) entries.splice(0, entries.length - 200);
    await activityLogItem.setValue(entries);
  }
}
