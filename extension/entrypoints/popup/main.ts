/**
 * Popup page script — credentials form + master status display.
 *
 * Reads/writes chrome.storage.local directly (popup runs in extension context,
 * no bridge needed). Auto-refreshes master status every 5 seconds.
 */

// ── Storage Keys ────────────────────────────────────────────────────────────
// Must match the keys in src/storage.ts (WXT storage items use these under the hood)

const CREDS_KEY = 'local:algoCredentials';
const MASTER_KEY = 'local:algoMasterState';

type AlgoCredentials = { email: string; password: string };
type AlgoMasterState = {
  status: 'IDLE' | 'SPAWNING' | 'MONITORING' | 'RECOVERING' | 'WAITING_FOR_LOGIN' | 'ERROR';
  tabId: number | null;
  recoveryAttempts: number;
  lastHealthCheck: string | null;
  errorMessage: string | null;
};

// ── DOM Elements ────────────────────────────────────────────────────────────

const emailInput = document.getElementById('email') as HTMLInputElement;
const passwordInput = document.getElementById('password') as HTMLInputElement;
const saveBtn = document.getElementById('save-btn') as HTMLButtonElement;
const credsStatus = document.getElementById('creds-status') as HTMLSpanElement;
const budgetInput = document.getElementById('budget') as HTMLInputElement;
const startBtn = document.getElementById('start-btn') as HTMLButtonElement;
const stopBtn = document.getElementById('stop-btn') as HTMLButtonElement;
const algoStatus = document.getElementById('algo-status') as HTMLDivElement;
const statusDot = document.getElementById('status-dot') as HTMLDivElement;
const statusLabel = document.getElementById('status-label') as HTMLSpanElement;
const statusDetail = document.getElementById('status-detail') as HTMLDivElement;

// ── Credentials ─────────────────────────────────────────────────────────────

async function loadCredentials(): Promise<void> {
  const stored = await chrome.storage.local.get(CREDS_KEY);
  const creds = (stored[CREDS_KEY] as AlgoCredentials | undefined) ?? null;

  if (creds) {
    emailInput.value = creds.email;
    passwordInput.placeholder = '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022';
    credsStatus.textContent = 'Credentials saved';
    credsStatus.style.color = '#2ecc71';
  } else {
    credsStatus.textContent = 'Not configured';
    credsStatus.style.color = '#e74c3c';
  }
}

saveBtn.addEventListener('click', async () => {
  const email = emailInput.value.trim();
  const password = passwordInput.value;

  if (!email) {
    credsStatus.textContent = 'Email required';
    credsStatus.style.color = '#e74c3c';
    return;
  }

  if (!password) {
    // If password empty but we have existing creds, update email only
    const stored = await chrome.storage.local.get(CREDS_KEY);
    const existing = (stored[CREDS_KEY] as AlgoCredentials | undefined) ?? null;
    if (existing) {
      await chrome.storage.local.set({ [CREDS_KEY]: { email, password: existing.password } });
      credsStatus.textContent = 'Email updated';
      credsStatus.style.color = '#2ecc71';
      return;
    }
    credsStatus.textContent = 'Password required';
    credsStatus.style.color = '#e74c3c';
    return;
  }

  await chrome.storage.local.set({ [CREDS_KEY]: { email, password } });
  passwordInput.value = '';
  passwordInput.placeholder = '\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022';
  credsStatus.textContent = 'Credentials saved';
  credsStatus.style.color = '#2ecc71';
});

// ── Master Status ───────────────────────────────────────────────────────────

const STATUS_DISPLAY: Record<string, { label: string; color: string; detail?: string }> = {
  IDLE: { label: 'Algo inactive', color: '#888' },
  SPAWNING: { label: 'Starting...', color: '#f39c12' },
  MONITORING: { label: 'Session active', color: '#2ecc71' },
  RECOVERING: { label: 'Recovering session...', color: '#f39c12' },
  WAITING_FOR_LOGIN: { label: 'Please log in manually', color: '#e67e22' },
  ERROR: { label: 'Error', color: '#e74c3c' },
};

async function refreshStatus(): Promise<void> {
  const stored = await chrome.storage.local.get(MASTER_KEY);
  const state = (stored[MASTER_KEY] as AlgoMasterState | undefined) ?? null;

  if (!state) {
    statusDot.style.background = '#888';
    statusLabel.textContent = 'Algo inactive';
    statusDetail.textContent = '';
    return;
  }

  const display = STATUS_DISPLAY[state.status] ?? STATUS_DISPLAY.IDLE;
  statusDot.style.background = display.color;
  statusLabel.textContent = display.label;

  // Detail line
  if (state.status === 'MONITORING' && state.lastHealthCheck) {
    const ago = Math.round((Date.now() - new Date(state.lastHealthCheck).getTime()) / 60_000);
    statusDetail.textContent = `Last health check: ${ago}m ago`;
  } else if (state.status === 'ERROR' && state.errorMessage) {
    statusDetail.textContent = state.errorMessage;
    statusDetail.style.color = '#e74c3c';
  } else if (state.status === 'WAITING_FOR_LOGIN' && state.errorMessage) {
    statusDetail.textContent = state.errorMessage;
    statusDetail.style.color = '#e67e22';
  } else if (state.status === 'RECOVERING') {
    statusDetail.textContent = `Attempt ${state.recoveryAttempts} of 3`;
  } else {
    statusDetail.textContent = '';
    statusDetail.style.color = '#888';
  }
}

// ── Algo Start / Stop ───────────────────────────────────────────────────────

startBtn.addEventListener('click', async () => {
  const budget = parseInt(budgetInput.value, 10);
  if (!budget || budget <= 0) {
    algoStatus.textContent = 'Enter a valid budget';
    algoStatus.style.color = '#e74c3c';
    return;
  }

  startBtn.disabled = true;
  startBtn.textContent = 'Starting...';
  algoStatus.textContent = '';

  try {
    const res = await chrome.runtime.sendMessage({ type: 'ALGO_START', budget });
    if (res?.type === 'ALGO_START_RESULT' && res.success) {
      algoStatus.textContent = `Started with ${budget.toLocaleString()} budget`;
      algoStatus.style.color = '#2ecc71';
      budgetInput.value = '';
    } else {
      algoStatus.textContent = `Failed: ${res?.error || 'Unknown error'}`;
      algoStatus.style.color = '#e74c3c';
    }
  } catch (err) {
    algoStatus.textContent = `Error: ${err instanceof Error ? err.message : String(err)}`;
    algoStatus.style.color = '#e74c3c';
  }

  startBtn.disabled = false;
  startBtn.textContent = 'Start Algo';
  refreshStatus();
});

stopBtn.addEventListener('click', async () => {
  stopBtn.disabled = true;
  stopBtn.textContent = 'Stopping...';

  try {
    const res = await chrome.runtime.sendMessage({ type: 'ALGO_STOP' });
    if (res?.type === 'ALGO_STOP_RESULT' && res.success) {
      algoStatus.textContent = 'Algo stopped';
      algoStatus.style.color = '#888';
    } else {
      algoStatus.textContent = `Failed: ${res?.error || 'Unknown error'}`;
      algoStatus.style.color = '#e74c3c';
    }
  } catch (err) {
    algoStatus.textContent = `Error: ${err instanceof Error ? err.message : String(err)}`;
    algoStatus.style.color = '#e74c3c';
  }

  stopBtn.disabled = false;
  stopBtn.textContent = 'Stop Algo';
  refreshStatus();
});

// ── Init ────────────────────────────────────────────────────────────────────

loadCredentials();
refreshStatus();
setInterval(refreshStatus, 5_000);
