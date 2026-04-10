/**
 * Main world storage adapter — bridges to chrome.storage.local via postMessage.
 * This file must NOT import from './storage' (which uses wxt/storage).
 */
import type { AutomationStorageAdapter } from './automation';
import { bridgedStorageSet, bridgedStorageGet } from './ea-bridge';

/** Must match the keys used in storage.ts. */
const STATUS_KEY = 'automationStatus';
const ACTIVITY_LOG_KEY = 'activityLog';

/** Types duplicated here to avoid importing from storage.ts (which pulls in wxt/storage). */
export interface AutomationStatus {
  isRunning: boolean;
  state: string;
  currentAction: string | null;
  lastEvent: string | null;
  sessionProfit: number;
  errorMessage: string | null;
}

export interface ActivityLogEntry {
  timestamp: string;
  message: string;
}

/**
 * Storage adapter that bridges to chrome.storage.local via postMessage.
 * Used in the main world where chrome.storage is not directly available.
 */
export class MainWorldStorageAdapter implements AutomationStorageAdapter {
  async setStatus(status: AutomationStatus): Promise<void> {
    await bridgedStorageSet(STATUS_KEY, status);
  }

  async getActivityLog(): Promise<ActivityLogEntry[]> {
    const entries = await bridgedStorageGet<ActivityLogEntry[]>(ACTIVITY_LOG_KEY);
    return entries ?? [];
  }

  async setActivityLog(entries: ActivityLogEntry[]): Promise<void> {
    await bridgedStorageSet(ACTIVITY_LOG_KEY, entries);
  }
}
