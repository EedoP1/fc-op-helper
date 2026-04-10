/**
 * Algo Master — background service worker state machine for session management.
 *
 * Monitors the EA web app tab (worker), detects session death via 3 methods
 * (tab events, worker failure reports, periodic health checks), and recovers
 * automatically including credential-based auto-login.
 *
 * State machine:
 *   IDLE → SPAWNING → MONITORING → RECOVERING → SPAWNING
 *                         ↑                         |
 *                         └─────────────────────────┘
 */
import { algoMasterStateItem, algoCredentialsItem, type AlgoMasterState, type AlgoMasterStatus } from './storage';

// ── Constants ────────────────────────────────────────────────────────────────

const EA_WEBAPP_URL = 'https://www.ea.com/ea-sports-fc/ultimate-team/web-app/';
const EA_DOMAIN_PATTERN = 'https://www.ea.com/*';
const HEALTH_CHECK_ALARM = 'algo-health-check';
const HEALTH_CHECK_INTERVAL_MINUTES = 5;
const PING_INTERVAL_MS = 2_000;
const PING_TIMEOUT_MS = 30_000;
const PAGE_LOAD_TIMEOUT_MS = 30_000;
const LOGIN_TIMEOUT_MS = 15_000;
const MAX_RECOVERY_ATTEMPTS = 3;
const RETRY_AFTER_ERROR_MS = 5 * 60_000; // 5 minutes
const HEALTH_CHECK_RETRY_MS = 10_000;

// ── State ────────────────────────────────────────────────────────────────────

let currentState: AlgoMasterState = {
  status: 'IDLE',
  tabId: null,
  recoveryAttempts: 0,
  lastHealthCheck: null,
  errorMessage: null,
};

// ── State Persistence ────────────────────────────────────────────────────────

async function loadState(): Promise<void> {
  currentState = await algoMasterStateItem.getValue();
}

async function saveState(): Promise<void> {
  await algoMasterStateItem.setValue(currentState);
}

async function transition(status: AlgoMasterStatus, extra?: Partial<AlgoMasterState>): Promise<void> {
  currentState.status = status;
  if (extra) Object.assign(currentState, extra);
  await saveState();
  console.log(`[algo-master] → ${status}`, extra ?? '');
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Initialize the master on service worker wake.
 * Reads persisted state and re-registers listeners if algo is active.
 */
export async function initAlgoMaster(): Promise<void> {
  await loadState();

  // Register tab event listeners (always, so we catch events even after worker restart)
  chrome.tabs.onRemoved.addListener(onTabRemoved);
  chrome.tabs.onUpdated.addListener(onTabUpdated);

  // Register health check alarm listener
  chrome.alarms.onAlarm.addListener(onAlarm);

  // If we were active before worker restart, resume
  if (currentState.status === 'MONITORING') {
    ensureHealthCheckAlarm();
  } else if (currentState.status === 'SPAWNING' || currentState.status === 'RECOVERING') {
    // Worker restarted mid-recovery — retry
    await startRecovery();
  }
}

/**
 * Start the algo master — called when user starts algo trading.
 * Finds or creates the EA tab, waits for worker ready, then signals algo-start.
 */
export async function startAlgoMaster(): Promise<void> {
  await transition('SPAWNING', { recoveryAttempts: 0, errorMessage: null });
  await spawnWorker();
}

/**
 * Stop the algo master — called when user stops algo trading.
 * Clears alarms, resets state. Does NOT close the EA tab.
 */
export async function stopAlgoMaster(): Promise<void> {
  await chrome.alarms.clear(HEALTH_CHECK_ALARM);
  await transition('IDLE', { tabId: null, recoveryAttempts: 0, errorMessage: null });
}

/**
 * Handle ALGO_SESSION_DEAD message from the worker (Method 2).
 * Worker detected 3 consecutive EA failures.
 */
export async function onSessionDead(): Promise<void> {
  if (currentState.status !== 'MONITORING') return;
  console.log('[algo-master] Worker reported session dead — starting recovery');
  await startRecovery();
}

/** Get current master state for status display. */
export function getMasterState(): AlgoMasterState {
  return { ...currentState };
}

// ── Tab Event Handlers (Method 1) ────────────────────────────────────────────

function onTabRemoved(tabId: number): void {
  if (tabId !== currentState.tabId) return;
  if (currentState.status === 'IDLE') return;
  console.log('[algo-master] EA tab closed — starting recovery');
  currentState.tabId = null;
  startRecovery();
}

function onTabUpdated(tabId: number, changeInfo: { url?: string; status?: string }): void {
  if (tabId !== currentState.tabId) return;
  if (currentState.status !== 'MONITORING') return;
  if (!changeInfo.url) return;

  // URL drifted away from the web app — session redirected
  if (!changeInfo.url.includes('/ultimate-team/web-app/')) {
    console.log(`[algo-master] EA tab navigated away: ${changeInfo.url} — starting recovery`);
    startRecovery();
  }
}

// ── Health Check Alarm (Method 3) ────────────────────────────────────────────

function ensureHealthCheckAlarm(): void {
  chrome.alarms.get(HEALTH_CHECK_ALARM).then(alarm => {
    if (!alarm) {
      chrome.alarms.create(HEALTH_CHECK_ALARM, { periodInMinutes: HEALTH_CHECK_INTERVAL_MINUTES });
    }
  });
}

async function onAlarm(alarm: chrome.alarms.Alarm): Promise<void> {
  if (alarm.name !== HEALTH_CHECK_ALARM) return;
  if (currentState.status !== 'MONITORING') return;
  if (currentState.tabId == null) return;

  console.log('[algo-master] Running health check');

  const healthy = await performHealthCheck(currentState.tabId);
  if (healthy) {
    await transition('MONITORING', { lastHealthCheck: new Date().toISOString() });
    return;
  }

  // Retry once after 10s before declaring dead (false positive protection)
  console.log('[algo-master] Health check failed — retrying in 10s');
  await new Promise(r => setTimeout(r, HEALTH_CHECK_RETRY_MS));

  // Re-check state — user may have stopped algo during the wait
  await loadState();
  if (currentState.status !== 'MONITORING') return;

  const healthyRetry = await performHealthCheck(currentState.tabId!);
  if (healthyRetry) {
    await transition('MONITORING', { lastHealthCheck: new Date().toISOString() });
    return;
  }

  console.log('[algo-master] Health check failed twice — starting recovery');
  await startRecovery();
}

async function performHealthCheck(tabId: number): Promise<boolean> {
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: 'ALGO_HEALTH_CHECK' });
    if (response?.type === 'ALGO_HEALTH_CHECK_RESULT') {
      if (response.relisted_algo > 0 || response.relisted_other > 0) {
        console.log(`[algo-master] Health check OK — relisted ${response.relisted_algo} algo, ${response.relisted_other} other`);
      }
      return response.healthy === true;
    }
    return false;
  } catch {
    // Content script not responding — tab dead or navigated away
    return false;
  }
}

// ── Recovery Flow ────────────────────────────────────────────────────────────

async function startRecovery(): Promise<void> {
  await chrome.alarms.clear(HEALTH_CHECK_ALARM);

  currentState.recoveryAttempts++;
  if (currentState.recoveryAttempts > MAX_RECOVERY_ATTEMPTS) {
    await transition('ERROR', {
      errorMessage: `Recovery failed ${MAX_RECOVERY_ATTEMPTS} times — please log in manually`,
    });
    // Schedule a retry after 5 minutes
    setTimeout(() => {
      loadState().then(() => {
        if (currentState.status === 'ERROR') {
          currentState.recoveryAttempts = 0;
          startRecovery();
        }
      });
    }, RETRY_AFTER_ERROR_MS);
    return;
  }

  await transition('RECOVERING');
  await spawnWorker();
}

async function spawnWorker(): Promise<void> {
  // Step 1: Find or create the EA tab
  const tabId = await findOrCreateEaTab();
  await transition(currentState.status, { tabId });

  // Step 2: Wait for page to finish loading
  const loaded = await waitForPageLoad(tabId);
  if (!loaded) {
    console.warn('[algo-master] Page load timed out — retrying');
    await startRecovery();
    return;
  }

  // Step 3: Check what page we're on
  const tab = await chrome.tabs.get(tabId);
  const url = tab.url ?? '';

  if (url.includes('/ultimate-team/web-app/')) {
    // Session cookies valid — skip login, wait for content script
    console.log('[algo-master] Web app loaded — waiting for content script');
    await waitForWorkerAndStart(tabId);
    return;
  }

  // We're on a login page (or somewhere else)
  console.log(`[algo-master] Not on web app (${url}) — attempting login`);
  await attemptLogin(tabId);
}

async function findOrCreateEaTab(): Promise<number> {
  // Try to find an existing EA tab
  const tabs = await chrome.tabs.query({ url: EA_DOMAIN_PATTERN });
  if (tabs.length > 0 && tabs[0].id != null) {
    const tabId = tabs[0].id;
    // Navigate to the web app URL (may already be there)
    await chrome.tabs.update(tabId, { url: EA_WEBAPP_URL, active: true });
    return tabId;
  }

  // No EA tab found — create one
  const newTab = await chrome.tabs.create({ url: EA_WEBAPP_URL, active: true });
  return newTab.id!;
}

function waitForPageLoad(tabId: number): Promise<boolean> {
  return new Promise(resolve => {
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(false);
    }, PAGE_LOAD_TIMEOUT_MS);

    function listener(updatedTabId: number, changeInfo: { url?: string; status?: string }) {
      if (updatedTabId !== tabId) return;
      if (changeInfo.status !== 'complete') return;
      chrome.tabs.onUpdated.removeListener(listener);
      clearTimeout(timeout);
      resolve(true);
    }

    // Check if already loaded
    chrome.tabs.get(tabId).then(tab => {
      if (tab.status === 'complete') {
        chrome.tabs.onUpdated.removeListener(listener);
        clearTimeout(timeout);
        resolve(true);
      } else {
        chrome.tabs.onUpdated.addListener(listener);
      }
    }).catch(() => {
      clearTimeout(timeout);
      resolve(false);
    });
  });
}

async function attemptLogin(tabId: number): Promise<void> {
  const credentials = await algoCredentialsItem.getValue();

  if (!credentials) {
    // No credentials configured — wait for manual login
    await transition('WAITING_FOR_LOGIN', {
      errorMessage: 'Session expired — please log in manually',
    });
    pollForWebApp(tabId);
    return;
  }

  // Inject login script via chrome.scripting.executeScript
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      func: fillLoginForm,
      args: [credentials.email, credentials.password],
    });
  } catch (err) {
    console.error('[algo-master] Login script injection failed:', err);
    await transition('WAITING_FOR_LOGIN', {
      errorMessage: 'Login injection failed — please log in manually',
    });
    pollForWebApp(tabId);
    return;
  }

  // Wait for navigation back to web app
  const navigatedToWebApp = await waitForUrlMatch(tabId, '/ultimate-team/web-app/', LOGIN_TIMEOUT_MS);

  if (!navigatedToWebApp) {
    console.warn('[algo-master] Login did not redirect to web app — may have failed');
    // Check if we're still on login page
    const tab = await chrome.tabs.get(tabId);
    if (tab.url?.includes('/ultimate-team/web-app/')) {
      // Actually made it
      await waitForWorkerAndStart(tabId);
      return;
    }
    // Login failed — retry recovery
    await startRecovery();
    return;
  }

  // Wait for page to fully load after login redirect
  await waitForPageLoad(tabId);
  await waitForWorkerAndStart(tabId);
}

/**
 * Login form filler — injected into the login page via chrome.scripting.executeScript.
 * Finds email/password fields and submits. Runs in the target page's context.
 */
function fillLoginForm(email: string, password: string): void {
  // Try common selectors for EA's login form
  const emailInput = document.querySelector<HTMLInputElement>(
    'input[type="email"], input[name="email"], input[id="email"], input[autocomplete="email"]'
  );
  const passwordInput = document.querySelector<HTMLInputElement>(
    'input[type="password"], input[name="password"], input[id="password"]'
  );

  if (!emailInput || !passwordInput) {
    console.error('[algo-master] Login form inputs not found');
    return;
  }

  // Set values using native input setter to trigger React/framework change handlers
  const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype, 'value'
  )?.set;

  if (nativeInputValueSetter) {
    nativeInputValueSetter.call(emailInput, email);
    emailInput.dispatchEvent(new Event('input', { bubbles: true }));
    emailInput.dispatchEvent(new Event('change', { bubbles: true }));

    nativeInputValueSetter.call(passwordInput, password);
    passwordInput.dispatchEvent(new Event('input', { bubbles: true }));
    passwordInput.dispatchEvent(new Event('change', { bubbles: true }));
  } else {
    emailInput.value = email;
    passwordInput.value = password;
  }

  // Find and click the submit button
  const submitBtn = document.querySelector<HTMLButtonElement>(
    'button[type="submit"], input[type="submit"], button[class*="login"], button[class*="submit"]'
  );
  if (submitBtn) {
    setTimeout(() => submitBtn.click(), 500);
  } else {
    // Try submitting the form directly
    const form = emailInput.closest('form');
    if (form) {
      setTimeout(() => form.submit(), 500);
    }
  }
}

function waitForUrlMatch(tabId: number, urlFragment: string, timeoutMs: number): Promise<boolean> {
  return new Promise(resolve => {
    const timeout = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(false);
    }, timeoutMs);

    function listener(updatedTabId: number, changeInfo: { url?: string; status?: string }) {
      if (updatedTabId !== tabId) return;
      if (!changeInfo.url) return;
      if (changeInfo.url.includes(urlFragment)) {
        chrome.tabs.onUpdated.removeListener(listener);
        clearTimeout(timeout);
        resolve(true);
      }
    }

    chrome.tabs.onUpdated.addListener(listener);
  });
}

/**
 * Poll for the web app URL when waiting for manual login.
 * Checks the tab URL every 5 seconds until it matches the web app pattern.
 */
function pollForWebApp(tabId: number): void {
  const intervalId = setInterval(async () => {
    // Re-check state — user may have stopped algo
    await loadState();
    if (currentState.status !== 'WAITING_FOR_LOGIN') {
      clearInterval(intervalId);
      return;
    }

    try {
      const tab = await chrome.tabs.get(tabId);
      if (tab.url?.includes('/ultimate-team/web-app/')) {
        clearInterval(intervalId);
        console.log('[algo-master] Manual login detected — resuming');
        await waitForPageLoad(tabId);
        await waitForWorkerAndStart(tabId);
      }
    } catch {
      // Tab gone — start recovery
      clearInterval(intervalId);
      await startRecovery();
    }
  }, 5_000);
}

/**
 * Wait for the content script to respond to PING, then start the algo loop.
 */
async function waitForWorkerAndStart(tabId: number): Promise<void> {
  const startTime = Date.now();

  while (Date.now() - startTime < PING_TIMEOUT_MS) {
    try {
      const response = await chrome.tabs.sendMessage(tabId, { type: 'PING' });
      if (response?.type === 'PONG') {
        console.log('[algo-master] Worker is alive — starting algo');

        // Small delay for main world script to fully initialize
        await new Promise(r => setTimeout(r, 2_000));

        // Dispatch the algo-start custom event that ea-webapp.content.ts listens for.
        // The isolated world content script hears this and relays to main world via bridge.
        await chrome.scripting.executeScript({
          target: { tabId },
          func: () => document.dispatchEvent(new CustomEvent('op-seller-algo-start')),
        });

        await transition('MONITORING', {
          tabId,
          recoveryAttempts: 0,
          errorMessage: null,
          lastHealthCheck: new Date().toISOString(),
        });
        ensureHealthCheckAlarm();
        return;
      }
    } catch {
      // Content script not ready yet
    }
    await new Promise(r => setTimeout(r, PING_INTERVAL_MS));
  }

  console.warn('[algo-master] Worker did not respond to PING in time');
  await startRecovery();
}
