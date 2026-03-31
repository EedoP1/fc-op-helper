/**
 * Main automation loop orchestrator.
 *
 * Drives the continuous buy/list/relist cycle per D-02:
 *   Phase A: Get actions-needed from backend (D-19, D-33)
 *   Phase B: Buy cycle for each BUY action (D-02)
 *   Phase C: Scan transfer list + relist + clear sold (D-02)
 *   Phase D: Handle sold players — rebuy (D-14, D-15)
 *   Loop: repeat until stopped or error
 *
 * Key decisions:
 *   D-17: Graceful stop — checks engine.isStopping between actions
 *   D-18: Resume scans DOM to detect current state before acting
 *   D-19: Cold start fetches actions-needed from backend then scans DOM
 *   D-22: AutomationError triggers alert via engine.setError
 *   D-24: Daily cap checked before every buy phase
 *   D-35: Out of coins degrades to relist-only mode
 *   D-38: Session expiry detected and automation stopped with alert
 */
import { AutomationEngine, AutomationError, jitter } from './automation';
import { executeBuyCycle, type BuyCycleResult } from './buy-cycle';
import {
  executeTransferListCycle,
  scanTransferList,
  type TransferListCycleResult,
} from './transfer-list-cycle';
import type { ActionNeeded, ExtensionMessage } from './messages';

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Parse EA time-remaining string (e.g. "55 Minutes", "1 Hour", "30 Seconds")
 * into milliseconds. Returns Infinity if unparseable.
 */
function parseTimeRemainingMs(timeStr: string): number {
  const lower = timeStr.toLowerCase();
  const match = lower.match(/(\d+)\s*(second|minute|hour)/);
  if (!match) return Infinity;
  const value = parseInt(match[1], 10);
  const unit = match[2];
  if (unit.startsWith('second')) return value * 1_000;
  if (unit.startsWith('minute')) return value * 60_000;
  if (unit.startsWith('hour')) return value * 3_600_000;
  return Infinity;
}

/**
 * Check if the current page has the EA session login view visible.
 * Used to detect D-38: session expiry after any navigation or automation step.
 */
function isSessionExpired(): boolean {
  return document.querySelector('.ut-login-view') !== null;
}

/**
 * Check if a BuyCycleResult error reason indicates insufficient coins.
 * EA Web App shows coin-related errors in DOM notification layer text.
 * We also check the reason string as a fallback.
 */
function isInsufficientCoinsError(reason: string): boolean {
  const lower = reason.toLowerCase();
  if (lower.includes('coin') || lower.includes('insufficient') || lower.includes('funds')) {
    return true;
  }
  // Check notification layer DOM (EA shows coin errors as toast notifications)
  const notifLayer = document.querySelector('#NotificationLayer');
  if (notifLayer) {
    const text = notifLayer.textContent?.toLowerCase() ?? '';
    if (text.includes('coin') || text.includes('insufficient') || text.includes('funds')) {
      return true;
    }
  }
  return false;
}

// ── Main loop export ──────────────────────────────────────────────────────────

/**
 * Run the continuous automation cycle until stopped or an error occurs.
 *
 * Called by the content script when the user clicks "Start Automation".
 * Alternates between buying all portfolio players and scanning/relisting
 * the transfer list. Sold cards are detected and requeued for rebuy.
 *
 * @param engine       AutomationEngine state machine (tracks state, logs, persists status)
 * @param sendMessage  Callback to relay messages to the service worker / backend
 */
export async function runAutomationLoop(
  engine: AutomationEngine,
  sendMessage: (msg: any) => Promise<any>,
): Promise<void> {
  try {
    while (!engine.isStopping) {
      // ── Phase A: Scan transfer list + relist + clear sold (D-02, D-03) ───
      // Runs FIRST so expired cards get relisted immediately, sold cards are
      // cleared, and trade reports are sent before fetching actions_needed.
      // Also builds the reconciliation set to skip players already listed.

      await engine.setState('SCANNING', 'Scanning transfer list');

      let alreadyListedNames: Set<string> = new Set();
      let cycleResult: TransferListCycleResult | null = null;
      try {
        cycleResult = await executeTransferListCycle(sendMessage);

        // Build reconciliation set from scan results
        for (const item of cycleResult.scanned.listed) {
          alreadyListedNames.add(item.playerName.toLowerCase());
        }

        await engine.setLastEvent(
          `Transfer list scan: ${cycleResult.scanned.listed.length} listed, ${cycleResult.scanned.expired.length} expired, ${cycleResult.scanned.sold.length} sold`,
        );

        if (cycleResult.relistedCount > 0) {
          await engine.setLastEvent(`Relisted ${cycleResult.relistedCount} cards`);
        }

        if (cycleResult.soldCleared > 0) {
          await engine.log(`Cleared ${cycleResult.soldCleared} sold cards`);
        }

        await engine.setState('SCANNING', 'Transfer list cycle complete');

      } catch (err) {
        if (err instanceof AutomationError) {
          if (isSessionExpired()) {
            await engine.setError('EA session expired — please log in and restart automation');
            return;
          }
          await engine.setError(err.message);
          return;
        }
        await engine.log(`Transfer list cycle error: ${err instanceof Error ? err.message : String(err)} — proceeding without reconciliation`);
      }

      if (engine.isStopping) return;

      // ── Phase B: Get actions-needed (D-19: cold start / D-18: resume) ────
      // Fetched AFTER scan+relist so trade reports from Phase A are processed
      // and backend state is current.

      await engine.setState('SCANNING', 'Fetching portfolio actions');

      let actionsNeeded: ActionNeeded[] = [];
      try {
        const res = await sendMessage({ type: 'ACTIONS_NEEDED_REQUEST' } satisfies ExtensionMessage);
        if (res && res.type === 'ACTIONS_NEEDED_RESULT' && res.data) {
          actionsNeeded = res.data.actions;
        }
      } catch {
        await engine.log('ACTIONS_NEEDED_REQUEST failed — continuing with last known state');
      }

      if (engine.isStopping) return;

      // ── Phase C: Buy all portfolio players (D-02) ─────────────────────────

      // Check daily cap before starting buy phase (D-24, AUTO-04)
      let isCapped = false;
      try {
        const capRes = await sendMessage({ type: 'DAILY_CAP_REQUEST' } satisfies ExtensionMessage);
        if (capRes && capRes.type === 'DAILY_CAP_RESULT') {
          isCapped = capRes.capped === true;
        }
      } catch {
        await engine.log('DAILY_CAP_REQUEST failed — assuming not capped');
      }

      const buyPlayers = actionsNeeded.filter(a => a.action === 'BUY');
      let outOfCoins = false;

      // D-36: Track transfer list occupancy — EA caps at 100 active listings.
      // Start from scan results; increment after each successful buy+list.
      const EA_TRANSFER_LIST_MAX = 100;
      let transferListCount = cycleResult
        ? cycleResult.scanned.listed.length + cycleResult.scanned.expired.length
        : 0;
      const transferListFull = transferListCount >= EA_TRANSFER_LIST_MAX;

      if (!isCapped && !transferListFull && buyPlayers.length > 0) {
        await engine.setState('BUYING', 'Starting buy cycle');

        let consecutiveFailures = 0;
        const CAPTCHA_THRESHOLD = 3;  // D-22: 3 consecutive failures = possible CAPTCHA

        for (const player of buyPlayers) {
          if (engine.isStopping) return; // D-17: graceful stop between actions

          // D-22: Too many consecutive failures — likely CAPTCHA or blocked UI
          if (consecutiveFailures >= CAPTCHA_THRESHOLD) {
            if (isSessionExpired()) {
              await engine.setError('EA session expired — please log in and restart automation');
              return;
            }
            await engine.setError(`${consecutiveFailures} consecutive buy failures — possible CAPTCHA or UI block. Please check the EA Web App.`);
            return;
          }

          // D-36: Stop buying if transfer list is full
          if (transferListCount >= EA_TRANSFER_LIST_MAX) {
            await engine.log('Transfer list full (100) — stopping buy phase');
            break;
          }

          // D-35: if already out of coins, skip buy phase entirely
          if (outOfCoins) {
            await engine.log(`Out of coins — skipping buy for ${player.name}`);
            continue;
          }

          // D-13 / D-31: Fetch fresh price before buying
          let freshPlayer = { ...player };
          try {
            const priceRes = await sendMessage({
              type: 'FRESH_PRICE_REQUEST',
              ea_id: player.ea_id,
            } satisfies ExtensionMessage);
            if (priceRes && priceRes.type === 'FRESH_PRICE_RESULT' && !priceRes.error) {
              freshPlayer = {
                ...player,
                buy_price: priceRes.buy_price,
                sell_price: priceRes.sell_price,
              };
            }
          } catch {
            await engine.log(`Fresh price unavailable for ${player.name} — using cached price`);
          }

          // D-19 / D-18 reconciliation: skip if DOM already shows this player listed.
          // EA shows surname-only on transfer list cards (e.g. "Tonali" not "Sandro Tonali"),
          // so exact match fails. Use substring: if any listed name is contained in the
          // backend name (or vice versa), it's a match.
          const pName = player.name.toLowerCase();
          const isAlreadyListed = alreadyListedNames.has(pName)
            || Array.from(alreadyListedNames).some(domName =>
              pName.includes(domName) || domName.includes(pName)
            );
          if (isAlreadyListed) {
            await engine.log(`Skipping ${player.name} — already listed on transfer list`);
            continue;
          }

          await engine.setState('BUYING', `Buying: ${player.name}`);

          const result: BuyCycleResult = await executeBuyCycle(freshPlayer, sendMessage);

          if (result.outcome === 'bought') {
            consecutiveFailures = 0;  // D-22: reset on success
            transferListCount++;  // D-36: track new listing
            await engine.setLastEvent(`Bought ${player.name} for ${result.buyPrice.toLocaleString()}`);

            // Report buy + list to backend (D-30) — await to ensure backend
            // state is up to date before the next cycle fetches actions_needed.
            try {
              await sendMessage({
                type: 'TRADE_REPORT',
                ea_id: player.ea_id,
                price: result.buyPrice,
                outcome: 'bought',
              } satisfies ExtensionMessage);
              await sendMessage({
                type: 'TRADE_REPORT',
                ea_id: player.ea_id,
                price: freshPlayer.sell_price,
                outcome: 'listed',
              } satisfies ExtensionMessage);
            } catch {
              await engine.log(`Trade report failed for ${player.name} — backend state may be stale`);
            }

            // Increment daily cap counter (D-24)
            sendMessage({ type: 'DAILY_CAP_INCREMENT' } satisfies ExtensionMessage).catch(() => {});

            // Check if now capped after this buy
            try {
              const capCheckRes = await sendMessage({ type: 'DAILY_CAP_REQUEST' } satisfies ExtensionMessage);
              if (capCheckRes && capCheckRes.type === 'DAILY_CAP_RESULT' && capCheckRes.capped) {
                await engine.log('Daily cap reached — stopping buy phase');
                break;
              }
            } catch { /* ignore */ }

          } else if (result.outcome === 'skipped') {
            // Sniped is normal market competition — reset failure counter.
            // Only DOM/timeout failures count toward CAPTCHA detection.
            const isDomFailure = result.reason.includes('search button not found')
              || result.reason.includes('DOM mismatch')
              || result.reason.includes('Timeout waiting');
            if (isDomFailure) {
              consecutiveFailures++;
            } else {
              consecutiveFailures = 0;
            }
            await engine.setLastEvent(`Skipped ${player.name}: ${result.reason}`);
          } else if (result.outcome === 'error') {
            consecutiveFailures++;

            // D-38: Check for session expiry
            if (isSessionExpired()) {
              await engine.setError('EA session expired — please log in and restart automation');
              return;
            }

            // D-35: Detect out-of-coins condition
            if (isInsufficientCoinsError(result.reason)) {
              outOfCoins = true;
              await engine.log('Out of coins — switching to relist-only mode for this cycle');
              continue;
            }

            // Non-critical error — log and continue to next player
            await engine.setLastEvent(`Error buying ${player.name}: ${result.reason}`);
          }

          // Brief pause between players (D-28 / AUTO-05)
          if (!engine.isStopping) {
            await jitter();
          }
        }
      } else if (transferListFull) {
        await engine.log(`Transfer list full (${transferListCount}/${EA_TRANSFER_LIST_MAX}) — skipping buy phase`);
      } else if (isCapped) {
        await engine.log('Daily cap reached — skipping buy phase');
      }

      if (engine.isStopping) return;

      // ── Phase D: Handle sold players — rebuy (D-14, D-15) ─────────────────

      if (cycleResult && cycleResult.scanned.sold.length > 0 && !outOfCoins) {
        // Re-check daily cap before rebuy phase
        let cappedForRebuy = false;
        try {
          const capRebuyRes = await sendMessage({ type: 'DAILY_CAP_REQUEST' } satisfies ExtensionMessage);
          if (capRebuyRes && capRebuyRes.type === 'DAILY_CAP_RESULT') {
            cappedForRebuy = capRebuyRes.capped === true;
          }
        } catch { /* ignore */ }

        if (!cappedForRebuy) {
          for (const soldItem of cycleResult.scanned.sold) {
            if (engine.isStopping) return;

            // Match sold DOM item to a portfolio player by name (endsWith) + rating
            const domName = soldItem.playerName.toLowerCase();
            const matched = actionsNeeded.find(a =>
              a.name.toLowerCase().endsWith(domName) && a.rating === soldItem.rating,
            );

            if (!matched) {
              await engine.log(`Sold item not in portfolio: ${soldItem.playerName} — skipping rebuy`);
              // Report the sale even without a matched ea_id if we have a listing ea_id
              // ea_id=0 here since we can't match; backend will ignore or log
              try {
                await sendMessage({
                  type: 'TRADE_REPORT',
                  ea_id: 0,
                  price: soldItem.price,
                  outcome: 'sold',
                } satisfies ExtensionMessage);
              } catch { /* unmatched sale — best effort */ }
              continue;
            }

            // Report the sale (D-30) — await so backend state is current
            try {
              await sendMessage({
                type: 'TRADE_REPORT',
                ea_id: matched.ea_id,
                price: soldItem.price,
                outcome: 'sold',
              } satisfies ExtensionMessage);
            } catch {
              await engine.log(`Sale report failed for ${matched.name}`);
            }

            // Track profit (approximate — EA tax applied backend-side per D-14)
            const approxProfit = soldItem.price - matched.buy_price;
            engine.addProfit(approxProfit);

            await engine.log(
              `Sold: ${matched.name} for ${soldItem.price.toLocaleString()} (approx profit: ${approxProfit.toLocaleString()})`,
            );

            // D-14: Fetch fresh price and rebuy
            let rebuyPlayer = { ...matched };
            try {
              const priceRes = await sendMessage({
                type: 'FRESH_PRICE_REQUEST',
                ea_id: matched.ea_id,
              } satisfies ExtensionMessage);
              if (priceRes && priceRes.type === 'FRESH_PRICE_RESULT' && !priceRes.error) {
                rebuyPlayer = {
                  ...matched,
                  buy_price: priceRes.buy_price,
                  sell_price: priceRes.sell_price,
                };
              }
            } catch {
              await engine.log(`Fresh price unavailable for rebuy of ${matched.name}`);
            }

            // D-36: Check transfer list space before rebuy
            if (transferListCount >= EA_TRANSFER_LIST_MAX) {
              await engine.log(`Transfer list full — skipping rebuy of ${matched.name}`);
              continue;
            }

            await engine.setState('BUYING', `Rebuying: ${matched.name}`);
            const rebuyResult = await executeBuyCycle(rebuyPlayer, sendMessage);

            if (rebuyResult.outcome === 'bought') {
              transferListCount++;  // D-36: track new listing
              await engine.setLastEvent(
                `Rebought ${matched.name} for ${rebuyResult.buyPrice.toLocaleString()}`,
              );
              try {
                await sendMessage({
                  type: 'TRADE_REPORT',
                  ea_id: matched.ea_id,
                  price: rebuyResult.buyPrice,
                  outcome: 'bought',
                } satisfies ExtensionMessage);
                await sendMessage({
                  type: 'TRADE_REPORT',
                  ea_id: matched.ea_id,
                  price: rebuyPlayer.sell_price,
                  outcome: 'listed',
                } satisfies ExtensionMessage);
              } catch {
                await engine.log(`Trade report failed for rebuy of ${matched.name}`);
              }
            } else if (rebuyResult.outcome === 'error') {
              // Check for critical failures
              const isAutomationFailure =
                rebuyResult.reason.includes('DOM mismatch') ||
                rebuyResult.reason.includes('CAPTCHA') ||
                rebuyResult.reason.includes('Timeout waiting');
              if (isAutomationFailure) {
                if (isSessionExpired()) {
                  await engine.setError('EA session expired — please log in and restart automation');
                  return;
                }
                await engine.setError(rebuyResult.reason);
                return;
              }
              if (isInsufficientCoinsError(rebuyResult.reason)) {
                outOfCoins = true;
                await engine.log('Out of coins during rebuy — stopping buy phase');
                break;
              }
              await engine.log(`Rebuy error for ${matched.name}: ${rebuyResult.reason}`);
            } else {
              await engine.log(`Rebuy skipped for ${matched.name}: ${rebuyResult.reason}`);
            }

            if (!engine.isStopping) {
              await jitter();
            }
          }
        }
      }

      if (engine.isStopping) return;

      // ── Inter-cycle pause ─────────────────────────────────────────────────
      // If transfer list is full, sleep until the earliest card expires instead
      // of polling every 10 seconds. Otherwise use standard jitter (D-28, AUTO-05).
      if (transferListCount >= EA_TRANSFER_LIST_MAX && cycleResult) {
        let earliestMs = Infinity;
        for (const item of cycleResult.scanned.listed) {
          if (item.timeRemaining) {
            const ms = parseTimeRemainingMs(item.timeRemaining);
            if (ms < earliestMs) earliestMs = ms;
          }
        }
        if (earliestMs < Infinity && earliestMs > 10_000) {
          // Add a small buffer so the card is definitely expired when we rescan
          const waitMs = earliestMs + 5_000;
          const waitMin = Math.max(1, Math.round(waitMs / 60_000));
          await engine.setState('IDLE', `Transfer list full — next expiry in ~${waitMin}m`);
          await engine.log(`Transfer list full — sleeping ${waitMin}m until next card expires`);
          // Sleep in 30s chunks so we can respond to stop requests
          let remaining = waitMs;
          while (remaining > 0 && !engine.isStopping) {
            const chunk = Math.min(remaining, 30_000);
            await new Promise(r => setTimeout(r, chunk));
            remaining -= chunk;
          }
        } else {
          await engine.setState('IDLE', 'Transfer list full — checking again shortly');
          await jitter(15_000, 30_000);
        }
      } else {
        await engine.setState('IDLE', 'Cycle complete — waiting before next cycle');
        await jitter(5000, 10000);
      }
    }
  } catch (err) {
    if (err instanceof AutomationError) {
      await engine.setError(err.message);
      return;
    }
    const msg = err instanceof Error ? err.message : String(err);
    await engine.setError(`Unexpected error: ${msg}`);
  }
}
