/**
 * MV3 Service Worker — OP Seller background entrypoint.
 *
 * Lifecycle: Chrome re-executes this module on every worker wake (alarm fire, message, etc.).
 * All state that must survive termination lives in chrome.storage.local (via storage items).
 *
 * Key behaviors:
 *   - D-01: Alarm-based polling at 1-minute intervals (alarms survive worker termination)
 *   - D-02: Immediate poll on wake (before resuming the alarm cycle)
 *   - D-03: Polling gated by enabled flag in storage (Phase 8 UI wires the toggle)
 *   - D-04: Backend URL hardcoded to localhost:8000 (v1.1 is localhost-only)
 */
import { enabledItem, lastActionItem } from '../src/storage';
import { ExtensionMessage } from '../src/messages';

const BACKEND_URL = 'http://localhost:8000';
const POLL_ALARM = 'poll';

export default defineBackground({
  type: 'module',
  main() {
    // Check-and-recreate alarm (per D-01: 1-minute interval).
    // Alarm state survives worker termination but listeners do not — must re-register on every wake.
    // Using get+create instead of always-create ensures we don't reset the schedule on each wake.
    // Use Promise-based chrome.alarms.get (Chrome MV3 supports both callback and Promise forms).
    chrome.alarms.get(POLL_ALARM).then((alarm) => {
      if (!alarm) {
        chrome.alarms.create(POLL_ALARM, { periodInMinutes: 1 });
      }
    });

    // Re-register alarm listener on every wake (listeners don't persist across termination).
    chrome.alarms.onAlarm.addListener(async (alarm) => {
      if (alarm.name === POLL_ALARM) {
        await maybePoll();
      }
    });

    // D-02: Poll immediately on wake — worker may have been terminated during a cycle.
    maybePoll();
  },
});

/**
 * Send a PING to the active EA Web App tab to confirm the content script is alive.
 * Returns the PONG response, or null if the content script is not ready (expected during navigation).
 * Wraps tabs.sendMessage in try/catch — rejects with "Could not establish connection" when
 * no content script listener is registered (Pitfall 4 from research).
 */
async function pingActiveTab(): Promise<ExtensionMessage | null> {
  try {
    const [tab] = await chrome.tabs.query({
      url: 'https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*',
      active: true,
    });
    if (!tab?.id) return null;
    return await chrome.tabs.sendMessage(tab.id, { type: 'PING' } satisfies ExtensionMessage);
  } catch {
    return null; // content script not ready — expected during navigation
  }
}

/**
 * Poll the backend for a pending action if the enabled flag is set.
 * Stores the fetched action in lastActionItem for Phase 7 DOM automation to consume.
 * After storing an action, pings the active EA tab to confirm content script is alive.
 * Handles all errors gracefully — never throws (worker errors are silent to the user).
 */
async function maybePoll(): Promise<void> {
  const enabled = await enabledItem.getValue();
  if (!enabled) return;

  try {
    const res = await fetch(`${BACKEND_URL}/api/v1/actions/pending`);
    if (!res.ok) return;
    const data = await res.json();
    if (data.action) {
      await lastActionItem.setValue(data.action);
      const pong = await pingActiveTab();
      if (pong) {
        console.log('[OP Seller] Content script alive');
      }
    }
  } catch (e) {
    console.error('[OP Seller] poll failed:', e);
  }
}
