/**
 * Global rolling rate limit on relist operations to stay well under
 * EA's per-day listing cap. Each successful relist records a timestamp;
 * callers check available budget before listing the next expired item.
 *
 * Runs in the EA web app's MAIN world, so storage access goes through the
 * postMessage bridge rather than `wxt/storage` (which needs chrome APIs).
 */
import { bridgedStorageGet, bridgedStorageSet } from './ea-bridge';

export const MAX_RELISTS_PER_HOUR = 2;
const WINDOW_MS = 60 * 60 * 1000;
const STORAGE_KEY = 'relistHistory';

async function prunedHistory(): Promise<number[]> {
  const cutoff = Date.now() - WINDOW_MS;
  const history = (await bridgedStorageGet<number[]>(STORAGE_KEY)) ?? [];
  const recent = history.filter(t => t >= cutoff);
  if (recent.length !== history.length) {
    await bridgedStorageSet(STORAGE_KEY, recent);
  }
  return recent;
}

/** Number of relists still allowed in the current rolling 60-minute window. */
export async function getRelistBudget(): Promise<number> {
  const recent = await prunedHistory();
  return Math.max(0, MAX_RELISTS_PER_HOUR - recent.length);
}

/** Record one successful relist at the current time. */
export async function recordRelist(): Promise<void> {
  const recent = await prunedHistory();
  recent.push(Date.now());
  await bridgedStorageSet(STORAGE_KEY, recent);
}
