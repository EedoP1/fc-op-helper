/**
 * Bridge between isolated world and main world content scripts.
 *
 * The main world has access to EA's internal globals (services.Item, etc.)
 * but NOT to chrome.runtime or chrome.storage APIs.
 *
 * The isolated world has access to chrome.runtime and chrome.storage
 * but NOT to EA's page-level JavaScript globals.
 *
 * This bridge provides two-way communication via window.postMessage:
 *
 *   Main World --[chrome-api-request]--> Isolated World --[chrome-api-response]--> Main World
 *   Isolated World --[automation-command]--> Main World --[automation-result]--> Isolated World
 *
 * Uses a unique source prefix ('op-seller') and correlation IDs to avoid
 * conflicts with FUT Enhancer and other extensions.
 */

// ── Message Protocol ────────────────────────────────────────────────────────

const MSG_SOURCE = 'op-seller';

/** Request from main world to isolated world for chrome API access. */
export interface ChromeApiRequest {
  source: typeof MSG_SOURCE;
  direction: 'to-isolated';
  type: 'chrome-api-request';
  id: string;
  method: ChromeApiMethod;
  args: any[];
}

/** Response from isolated world back to main world. */
export interface ChromeApiResponse {
  source: typeof MSG_SOURCE;
  direction: 'to-main';
  type: 'chrome-api-response';
  id: string;
  result?: any;
  error?: string;
}

/** Command from isolated world to main world (automation control). */
export interface AutomationCommand {
  source: typeof MSG_SOURCE;
  direction: 'to-main';
  type: 'automation-command';
  id: string;
  command: 'start' | 'stop' | 'getStatus';
}

/** Result from main world back to isolated world. */
export interface AutomationResult {
  source: typeof MSG_SOURCE;
  direction: 'to-isolated';
  type: 'automation-result';
  id: string;
  result?: any;
  error?: string;
}

/** All chrome API methods that the main world can request. */
export type ChromeApiMethod =
  | 'sendMessage'
  | 'storageSet'
  | 'storageGet';

/** Union of all bridge message types. */
export type BridgeMessage =
  | ChromeApiRequest
  | ChromeApiResponse
  | AutomationCommand
  | AutomationResult;

// ── Helpers ─────────────────────────────────────────────────────────────────

let idCounter = 0;
function generateId(): string {
  return `${MSG_SOURCE}-${Date.now()}-${++idCounter}`;
}

function isBridgeMessage(data: any): data is BridgeMessage {
  return data && data.source === MSG_SOURCE;
}

// ── RPC timeout ─────────────────────────────────────────────────────────────

const RPC_TIMEOUT_MS = 30_000;

// ── Main World Client (calls chrome APIs via isolated world) ────────────────

const pendingChromeApiCalls = new Map<string, {
  resolve: (value: any) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}>();

/**
 * Initialize the chrome API bridge client in the main world.
 * Listens for ChromeApiResponse messages from the isolated world.
 */
export function initMainWorldBridge(): void {
  window.addEventListener('message', (event: MessageEvent) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!isBridgeMessage(data)) return;
    if (data.type !== 'chrome-api-response') return;

    const response = data as ChromeApiResponse;
    const pending = pendingChromeApiCalls.get(response.id);
    if (!pending) return;

    clearTimeout(pending.timer);
    pendingChromeApiCalls.delete(response.id);

    if (response.error) {
      pending.reject(new Error(response.error));
    } else {
      pending.resolve(response.result);
    }
  });
}

/**
 * Call a chrome API method from the main world via the isolated world bridge.
 */
function callChromeApi<T>(method: ChromeApiMethod, args: any[] = []): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const id = generateId();

    const timer = setTimeout(() => {
      pendingChromeApiCalls.delete(id);
      reject(new Error(`Bridge timeout: ${method} (${RPC_TIMEOUT_MS}ms)`));
    }, RPC_TIMEOUT_MS);

    pendingChromeApiCalls.set(id, { resolve, reject, timer });

    const request: ChromeApiRequest = {
      source: MSG_SOURCE,
      direction: 'to-isolated',
      type: 'chrome-api-request',
      id,
      method,
      args,
    };

    window.postMessage(request, '*');
  });
}

/**
 * Bridged chrome.runtime.sendMessage — callable from the main world.
 * The isolated world receives the request, calls chrome.runtime.sendMessage,
 * and posts the response back.
 */
export function bridgedSendMessage(msg: any): Promise<any> {
  return callChromeApi('sendMessage', [msg]);
}

/**
 * Bridged chrome.storage.local.set — callable from the main world.
 * @param key - Storage key
 * @param value - Value to store (must be structured-cloneable)
 */
export function bridgedStorageSet(key: string, value: any): Promise<void> {
  return callChromeApi('storageSet', [key, value]);
}

/**
 * Bridged chrome.storage.local.get — callable from the main world.
 * @param key - Storage key
 * @returns The stored value, or undefined if not set.
 */
export function bridgedStorageGet<T>(key: string): Promise<T | undefined> {
  return callChromeApi('storageGet', [key]);
}

// ── Isolated World Server (handles chrome API requests from main world) ─────

/**
 * Initialize the chrome API bridge server in the isolated world.
 * Handles requests from the main world to access chrome.runtime and chrome.storage.
 */
export function initIsolatedWorldBridge(): void {
  window.addEventListener('message', async (event: MessageEvent) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!isBridgeMessage(data)) return;
    if (data.type !== 'chrome-api-request') return;

    const request = data as ChromeApiRequest;

    const response: ChromeApiResponse = {
      source: MSG_SOURCE,
      direction: 'to-main',
      type: 'chrome-api-response',
      id: request.id,
    };

    try {
      switch (request.method) {
        case 'sendMessage':
          response.result = await chrome.runtime.sendMessage(request.args[0]);
          break;
        case 'storageSet': {
          const [key, value] = request.args;
          await chrome.storage.local.set({ [key]: value });
          break;
        }
        case 'storageGet': {
          const [key] = request.args;
          const stored = await chrome.storage.local.get(key);
          response.result = stored[key];
          break;
        }
        default:
          response.error = `Unknown chrome API method: ${request.method}`;
      }
    } catch (err) {
      response.error = err instanceof Error ? err.message : String(err);
    }

    window.postMessage(response, '*');
  });
}

// ── Automation Command Bridge (Isolated -> Main) ────────────────────────────

const pendingAutomationCalls = new Map<string, {
  resolve: (value: any) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
}>();

/**
 * Initialize the automation command listener in the isolated world.
 * Listens for AutomationResult messages from the main world.
 */
export function initAutomationCommandClient(): void {
  window.addEventListener('message', (event: MessageEvent) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!isBridgeMessage(data)) return;
    if (data.type !== 'automation-result') return;

    const result = data as AutomationResult;
    const pending = pendingAutomationCalls.get(result.id);
    if (!pending) return;

    clearTimeout(pending.timer);
    pendingAutomationCalls.delete(result.id);

    if (result.error) {
      pending.reject(new Error(result.error));
    } else {
      pending.resolve(result.result);
    }
  });
}

/**
 * Send an automation command from the isolated world to the main world.
 */
export function sendAutomationCommand(
  command: 'start' | 'stop' | 'getStatus',
): Promise<any> {
  return new Promise((resolve, reject) => {
    const id = generateId();

    const timer = setTimeout(() => {
      pendingAutomationCalls.delete(id);
      reject(new Error(`Automation command timeout: ${command}`));
    }, RPC_TIMEOUT_MS);

    pendingAutomationCalls.set(id, { resolve, reject, timer });

    const msg: AutomationCommand = {
      source: MSG_SOURCE,
      direction: 'to-main',
      type: 'automation-command',
      id,
      command,
    };

    window.postMessage(msg, '*');
  });
}
