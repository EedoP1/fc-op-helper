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
const SPAWN_RETRY_ALARM = 'algo-spawn-retry';
const HEALTH_CHECK_INTERVAL_MINUTES = 5;
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
  await chrome.alarms.clear(SPAWN_RETRY_ALARM);
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
  if (alarm.name === SPAWN_RETRY_ALARM) {
    await loadState();
    if (currentState.status === 'SPAWNING' || currentState.status === 'RECOVERING') {
      console.log('[algo-master] Spawn retry alarm fired — retrying');
      if (currentState.tabId != null) {
        await waitForWorkerAndStart(currentState.tabId);
      } else {
        await spawnWorker();
      }
    }
    return;
  }
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

/**
 * EA login flow — two-step process on signin.ea.com:
 *   Step 1: Fill email into input[placeholder*="email"], click #logInBtn (NEXT)
 *   Step 2: Fill password into input[type="password"], click #logInBtn (SIGN IN)
 *   Redirect: EA redirects back to /web-app/
 *
 * All interactions use chrome.scripting.executeScript with native value setter
 * + mousedown/mouseup/click sequence (proven to work with EA's form framework).
 *
 * Uses alarm-based retries to survive MV3 service worker termination.
 */
async function attemptLogin(tabId: number): Promise<void> {
  const credentials = await algoCredentialsItem.getValue();

  if (!credentials) {
    await transition('WAITING_FOR_LOGIN', {
      errorMessage: 'Session expired — please log in manually',
    });
    pollForWebApp(tabId);
    return;
  }

  // Check current page state and handle accordingly
  const tab = await chrome.tabs.get(tabId);
  const url = tab.url ?? '';

  if (url.includes('/ultimate-team/web-app/')) {
    // On web app login screen — click the Login button to navigate to signin.ea.com
    console.log('[algo-master] On web app login screen — clicking Login button');
    try {
      await chrome.scripting.executeScript({
        target: { tabId },
        func: clickWebAppLoginButton,
      });
    } catch (err) {
      console.error('[algo-master] Failed to click Login button:', err);
      await startRecovery();
      return;
    }
    // Schedule alarm to continue after navigation
    chrome.alarms.create(SPAWN_RETRY_ALARM, { delayInMinutes: 5 / 60 });
    return;
  }

  if (url.includes('signin.ea.com')) {
    // On EA sign-in page — detect which step we're on
    const hasPasswordField = await checkForPasswordField(tabId);

    if (!hasPasswordField) {
      // Step 1: Email page
      console.log('[algo-master] On email page — filling email');
      try {
        await chrome.scripting.executeScript({
          target: { tabId },
          func: fillEmailAndClickNext,
          args: [credentials.email],
        });
      } catch (err) {
        console.error('[algo-master] Email fill failed:', err);
        await startRecovery();
        return;
      }
      // Schedule alarm to continue after page advances to step 2
      chrome.alarms.create(SPAWN_RETRY_ALARM, { delayInMinutes: 5 / 60 });
      return;
    } else {
      // Step 2: Password page
      console.log('[algo-master] On password page — filling password');
      try {
        await chrome.scripting.executeScript({
          target: { tabId },
          func: fillPasswordAndClickSignIn,
          args: [credentials.password],
        });
      } catch (err) {
        console.error('[algo-master] Password fill failed:', err);
        await startRecovery();
        return;
      }
      // Schedule alarm to continue after redirect back to web app
      chrome.alarms.create(SPAWN_RETRY_ALARM, { delayInMinutes: 8 / 60 });
      return;
    }
  }

  // Unknown page — wait and retry
  console.log(`[algo-master] Unknown page during login: ${url}`);
  chrome.alarms.create(SPAWN_RETRY_ALARM, { delayInMinutes: 5 / 60 });
}

async function checkForPasswordField(tabId: number): Promise<boolean> {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => !!document.querySelector('input[type="password"]'),
    });
    return results?.[0]?.result ?? false;
  } catch {
    return false;
  }
}

/** Injected into web app — clicks the "Login" button via mousedown+mouseup+click. */
function clickWebAppLoginButton(): void {
  const buttons = document.querySelectorAll('button');
  for (const btn of buttons) {
    if (btn.textContent?.trim() === 'Login') {
      btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
      btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
      btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
      return;
    }
  }
}

/** Injected into signin.ea.com step 1 — fills email, clicks NEXT (#logInBtn). */
function fillEmailAndClickNext(email: string): void {
  const input = document.querySelector<HTMLInputElement>('input[placeholder*="email"]');
  if (!input) return;

  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  if (setter) {
    setter.call(input, email);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  const nextBtn = document.getElementById('logInBtn');
  if (nextBtn) {
    nextBtn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    nextBtn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
    nextBtn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  }
}

/** Injected into signin.ea.com step 2 — fills password, clicks SIGN IN (#logInBtn). */
function fillPasswordAndClickSignIn(password: string): void {
  const input = document.querySelector<HTMLInputElement>('input[type="password"]');
  if (!input) return;

  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  if (setter) {
    setter.call(input, password);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  const signInBtn = document.getElementById('logInBtn');
  if (signInBtn) {
    signInBtn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    signInBtn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
    signInBtn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
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
 * Wait for the content script to respond to PING, then verify the session
 * is alive before starting the algo loop.
 *
 * If PONG but session is dead (in-app login screen at same URL), click
 * the Login button and go through the login flow.
 */
/**
 * Non-blocking worker readiness check.
 *
 * MV3 service workers terminate during long async loops (setTimeout doesn't
 * keep them alive). Instead of a PING loop, we:
 *   1. Try a single PING
 *   2. If PONG: check session, handle login or start algo
 *   3. If no PONG: schedule a retry alarm in 5 seconds
 *
 * The alarm fires onAlarm → onSpawnRetryAlarm → retries this function.
 */
async function waitForWorkerAndStart(tabId: number): Promise<void> {
  // Single PING attempt
  let gotPong = false;
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: 'PING' });
    gotPong = response?.type === 'PONG';
  } catch {
    // Content script not ready
  }

  if (!gotPong) {
    // Check if we've been trying too long (use recoveryAttempts as proxy)
    if (currentState.recoveryAttempts > MAX_RECOVERY_ATTEMPTS) {
      await transition('ERROR', { errorMessage: 'Worker never responded — please reload the page' });
      return;
    }
    console.log('[algo-master] Worker not ready — scheduling retry in 5s');
    chrome.alarms.create(SPAWN_RETRY_ALARM, { delayInMinutes: 5 / 60 });
    return;
  }

  console.log('[algo-master] Worker is alive — verifying session');

  // Check for in-app login screen
  const sessionAlive = await checkSessionViaTab(tabId);
  if (!sessionAlive) {
    console.log('[algo-master] Content script alive but session dead — starting login flow');
    await attemptLogin(tabId);
    return;
  }

  // Session confirmed alive — start the algo loop
  console.log('[algo-master] Session alive — starting algo');
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
}

/**
 * Check if the EA session is alive by looking for the in-app login button.
 * If a "Login" button exists on the page, the session is dead.
 */
async function checkSessionViaTab(tabId: number): Promise<boolean> {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        // Check ALL buttons for EA's in-app login screen.
        // Can't just check the first button — our overlay toggle ("OP") may be first.
        const buttons = document.querySelectorAll('button');
        for (const btn of buttons) {
          if (btn.textContent?.trim() === 'Login') {
            return false; // Session dead — login screen showing
          }
        }
        return true; // No login button — session alive
      },
    });
    return results?.[0]?.result ?? true;
  } catch {
    return true; // Can't check — assume alive, health check will catch it later
  }
}

