/**
 * Popup page script — credentials form + master status display.
 *
 * Reads/writes chrome.storage.local directly (popup runs in extension context,
 * no bridge needed). Auto-refreshes master status every 5 seconds.
 */

// ── Storage Keys ────────────────────────────────────────────────────────────
// WXT strips the 'local:' prefix when writing to chrome.storage.local.
// storage.defineItem('local:algoCredentials') stores under key 'algoCredentials'.

const CREDS_KEY = 'algoCredentials';
const MASTER_KEY = 'algoMasterState';

type AlgoCredentials = { email: string; password: string };
type TradeMode = 'algo' | 'op-selling';

const SELECTED_MODE_KEY = 'selectedMode';

type AlgoMasterState = {
  status: 'IDLE' | 'SPAWNING' | 'MONITORING' | 'RECOVERING' | 'WAITING_FOR_LOGIN' | 'ERROR';
  tabId: number | null;
  recoveryAttempts: number;
  lastHealthCheck: string | null;
  errorMessage: string | null;
  mode?: TradeMode;
};

// ── DOM Elements ────────────────────────────────────────────────────────────

const emailInput = document.getElementById('email') as HTMLInputElement;
const passwordInput = document.getElementById('password') as HTMLInputElement;
const saveBtn = document.getElementById('save-btn') as HTMLButtonElement;
const credsStatus = document.getElementById('creds-status') as HTMLSpanElement;
const modeSelect = document.getElementById('mode-select') as HTMLSelectElement;
const controlTitle = document.getElementById('control-title') as HTMLDivElement;
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

// ── Mode Selection ──────────────────────────────────────────────────────────

async function resolveInitialMode(): Promise<TradeMode> {
  // Priority order: (1) running mode from master state, (2) stored selectedMode, (3) default 'algo'
  const stored = await chrome.storage.local.get([MASTER_KEY, SELECTED_MODE_KEY]);
  const master = (stored[MASTER_KEY] as AlgoMasterState | undefined) ?? null;
  const isRunning = master && master.status !== 'IDLE';
  if (isRunning && master.mode) return master.mode;
  const selected = stored[SELECTED_MODE_KEY] as TradeMode | undefined;
  return selected ?? 'algo';
}

function applyModeUI(mode: TradeMode, isRunning: boolean): void {
  modeSelect.value = mode;
  modeSelect.disabled = isRunning;
  // Budget input is only relevant for algo mode
  budgetInput.style.display = mode === 'algo' ? '' : 'none';
  controlTitle.textContent = mode === 'algo' ? 'Algo Trading' : 'OP Selling';
  startBtn.textContent = 'Start';
  stopBtn.textContent = 'Stop';
}

async function onModeChange(): Promise<void> {
  const mode = modeSelect.value as TradeMode;
  await chrome.storage.local.set({ [SELECTED_MODE_KEY]: mode });
  applyModeUI(mode, false);
}

modeSelect.addEventListener('change', onModeChange);

// ── Master Status ───────────────────────────────────────────────────────────

const STATUS_DISPLAY: Record<string, { label: string; color: string; detail?: string }> = {
  IDLE: { label: 'Idle', color: '#888' },
  SPAWNING: { label: 'Starting...', color: '#f39c12' },
  MONITORING: { label: 'Session active', color: '#2ecc71' },
  RECOVERING: { label: 'Recovering session...', color: '#f39c12' },
  WAITING_FOR_LOGIN: { label: 'Please log in manually', color: '#e67e22' },
  ERROR: { label: 'Error', color: '#e74c3c' },
};

async function refreshStatus(): Promise<void> {
  const stored = await chrome.storage.local.get(MASTER_KEY);
  const state = (stored[MASTER_KEY] as AlgoMasterState | undefined) ?? null;

  const isRunning = !!state && state.status !== 'IDLE';
  const currentMode: TradeMode = (state?.mode as TradeMode | undefined) ?? (modeSelect.value as TradeMode);
  applyModeUI(currentMode, isRunning);

  if (!state) {
    statusDot.style.background = '#888';
    statusLabel.textContent = 'Idle';
    statusDetail.textContent = '';
    return;
  }

  const display = STATUS_DISPLAY[state.status] ?? STATUS_DISPLAY.IDLE;
  statusDot.style.background = display.color;
  const modeLabel = currentMode === 'op-selling' ? 'OP Selling' : 'Algo';
  statusLabel.textContent = state.status === 'MONITORING' ? `Running: ${modeLabel}` : display.label;

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
  const mode = modeSelect.value as TradeMode;

  startBtn.disabled = true;
  startBtn.textContent = 'Starting...';
  algoStatus.textContent = '';

  try {
    if (mode === 'algo') {
      const budget = parseInt(budgetInput.value, 10);
      if (!budget || budget <= 0) {
        algoStatus.textContent = 'Enter a valid budget';
        algoStatus.style.color = '#e74c3c';
        startBtn.disabled = false;
        startBtn.textContent = 'Start';
        return;
      }
      const res = await chrome.runtime.sendMessage({ type: 'ALGO_START', budget });
      if (res?.type === 'ALGO_START_RESULT' && res.success) {
        algoStatus.textContent = `Algo started with ${budget.toLocaleString()} budget`;
        algoStatus.style.color = '#2ecc71';
        budgetInput.value = '';
      } else {
        algoStatus.textContent = `Failed: ${res?.error || 'Unknown error'}`;
        algoStatus.style.color = '#e74c3c';
      }
    } else {
      const res = await chrome.runtime.sendMessage({ type: 'AUTOMATION_START' });
      if (res?.type === 'AUTOMATION_START_RESULT' && res.success) {
        algoStatus.textContent = 'OP selling started';
        algoStatus.style.color = '#2ecc71';
      } else {
        algoStatus.textContent = `Failed: ${res?.error || 'Unknown error'}`;
        algoStatus.style.color = '#e74c3c';
      }
    }
  } catch (err) {
    algoStatus.textContent = `Error: ${err instanceof Error ? err.message : String(err)}`;
    algoStatus.style.color = '#e74c3c';
  }

  startBtn.disabled = false;
  startBtn.textContent = 'Start';
  refreshStatus();
});

stopBtn.addEventListener('click', async () => {
  stopBtn.disabled = true;
  stopBtn.textContent = 'Stopping...';

  try {
    // Read current running mode from master state to decide which stop message to send
    const stored = await chrome.storage.local.get(MASTER_KEY);
    const master = (stored[MASTER_KEY] as AlgoMasterState | undefined) ?? null;
    const runningMode: TradeMode = master?.mode ?? 'algo';
    const stopType = runningMode === 'op-selling' ? 'AUTOMATION_STOP' : 'ALGO_STOP';
    const res: any = await chrome.runtime.sendMessage({ type: stopType });
    const resultType = runningMode === 'op-selling' ? 'AUTOMATION_STOP_RESULT' : 'ALGO_STOP_RESULT';
    if (res?.type === resultType && res.success) {
      algoStatus.textContent = `${runningMode === 'op-selling' ? 'OP selling' : 'Algo'} stopped`;
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
  stopBtn.textContent = 'Stop';
  refreshStatus();
});

// ── Init ────────────────────────────────────────────────────────────────────

loadCredentials();
resolveInitialMode().then(mode => {
  modeSelect.value = mode;
  // applyModeUI will be called by refreshStatus with isRunning state
  refreshStatus();
});
setInterval(refreshStatus, 5_000);
