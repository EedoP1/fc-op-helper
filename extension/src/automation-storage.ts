/**
 * Storage adapter implementations for AutomationEngine.
 *
 * Two implementations:
 *   - IsolatedWorldStorageAdapter: Uses WXT storage items directly (chrome.storage.local)
 *   - MainWorldStorageAdapter: Bridges storage calls via window.postMessage to the isolated world
 */
import type { AutomationStorageAdapter } from './automation';
import type { AutomationStatus, ActivityLogEntry } from './storage';
import { automationStatusItem, activityLogItem } from './storage';
import { bridgedStorageSet, bridgedStorageGet } from './ea-bridge';

// ── Isolated World Adapter ──────────────────────────────────────────────────

/**
 * Storage adapter that uses WXT storage items directly.
 * Only works in the isolated world where chrome.storage.local is available.
 */
export class IsolatedWorldStorageAdapter implements AutomationStorageAdapter {
  async setStatus(status: AutomationStatus): Promise<void> {
    await automationStatusItem.setValue(status);
  }

  async getActivityLog(): Promise<ActivityLogEntry[]> {
    return activityLogItem.getValue();
  }

  async setActivityLog(entries: ActivityLogEntry[]): Promise<void> {
    await activityLogItem.setValue(entries);
  }
}

// ── Main World Adapter ──────────────────────────────────────────────────────

/**
 * Storage keys matching the WXT storage item definitions.
 * Must match the keys used in storage.ts (prefixed with 'local:').
 * chrome.storage.local strips the 'local:' prefix.
 */
const STATUS_KEY = 'automationStatus';
const ACTIVITY_LOG_KEY = 'activityLog';

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
