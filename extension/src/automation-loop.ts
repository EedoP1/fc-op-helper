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
      // ── Phase A: Get actions-needed (D-19: cold start / D-18: resume) ────

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

      // D-18 / D-19: Scan DOM to detect what is actually on the transfer list
      // Reconcile: skip players that backend says BUY but are already listed in DOM
      let alreadyListedNames: Set<string> = new Set();
      try {
        const scanResult = await scanTransferList();
        for (const item of scanResult.listed) {
          alreadyListedNames.add(item.playerName.toLowerCase());
        }
        await engine.setLastEvent(
          `Transfer list scan: ${scanResult.listed.length} listed, ${scanResult.expired.length} expired, ${scanResult.sold.length} sold`,
        );
      } catch (err) {
        if (err instanceof AutomationError) {
          await engine.setError(err.message);
          return;
        }
        await engine.log('Transfer list scan failed — proceeding without reconciliation');
      }

      if (engine.isStopping) return;

      // ── Phase B: Buy all portfolio players (D-02) ─────────────────────────

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

      if (!isCapped && buyPlayers.length > 0) {
        await engine.setState('BUYING', 'Starting buy cycle');

        for (const player of buyPlayers) {
          if (engine.isStopping) return; // D-17: graceful stop between actions

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

          // D-19 / D-18 reconciliation: skip if DOM already shows this player listed
          if (alreadyListedNames.has(player.name.toLowerCase())) {
            await engine.log(`Skipping ${player.name} — already listed on transfer list`);
            continue;
          }

          await engine.setState('BUYING', `Buying: ${player.name}`);

          const result: BuyCycleResult = await executeBuyCycle(freshPlayer, sendMessage);

          if (result.outcome === 'bought') {
            await engine.setLastEvent(`Bought ${player.name} for ${result.buyPrice.toLocaleString()}`);

            // Report buy to backend (D-30)
            sendMessage({
              type: 'TRADE_REPORT',
              ea_id: player.ea_id,
              price: result.buyPrice,
              outcome: 'bought',
            } satisfies ExtensionMessage).catch(() => {});

            // Report listing to backend (D-30: immediately listed at OP price)
            sendMessage({
              type: 'TRADE_REPORT',
              ea_id: player.ea_id,
              price: freshPlayer.sell_price,
              outcome: 'listed',
            } satisfies ExtensionMessage).catch(() => {});

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
            await engine.setLastEvent(`Skipped ${player.name}: ${result.reason}`);
          } else if (result.outcome === 'error') {
            // Check for AutomationError-level failures (DOM mismatch, CAPTCHA, etc.) — D-22
            // The buy-cycle wraps AutomationError into { outcome: 'error' }; detect by message pattern
            const isAutomationFailure =
              result.reason.includes('DOM mismatch') ||
              result.reason.includes('CAPTCHA') ||
              result.reason.includes('Timeout waiting');

            if (isAutomationFailure) {
              // D-38: Check for session expiry before raising as hard error
              if (isSessionExpired()) {
                await engine.setError('EA session expired — please log in and restart automation');
                return;
              }
              await engine.setError(result.reason);
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
      } else if (isCapped) {
        await engine.log('Daily cap reached — skipping buy phase');
      }

      if (engine.isStopping) return;

      // ── Phase C: Scan transfer list + relist + clear sold (D-02, D-03) ───

      await engine.setState('SCANNING', 'Scanning transfer list');

      let cycleResult: TransferListCycleResult | null = null;
      try {
        cycleResult = await executeTransferListCycle(sendMessage);

        if (cycleResult.relistedCount > 0) {
          await engine.setLastEvent(`Relisted ${cycleResult.relistedCount} cards`);
        }

        if (cycleResult.soldCleared > 0) {
          await engine.log(`Cleared ${cycleResult.soldCleared} sold cards`);
        }

        await engine.setState('SCANNING', 'Transfer list cycle complete');

      } catch (err) {
        if (err instanceof AutomationError) {
          // D-38: Check session expiry
          if (isSessionExpired()) {
            await engine.setError('EA session expired — please log in and restart automation');
            return;
          }
          await engine.setError(err.message);
          return;
        }
        await engine.log(`Transfer list cycle error: ${err instanceof Error ? err.message : String(err)}`);
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
              sendMessage({
                type: 'TRADE_REPORT',
                ea_id: 0,
                price: soldItem.price,
                outcome: 'sold',
              } satisfies ExtensionMessage).catch(() => {});
              continue;
            }

            // Report the sale (D-30)
            sendMessage({
              type: 'TRADE_REPORT',
              ea_id: matched.ea_id,
              price: soldItem.price,
              outcome: 'sold',
            } satisfies ExtensionMessage).catch(() => {});

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

            await engine.setState('BUYING', `Rebuying: ${matched.name}`);
            const rebuyResult = await executeBuyCycle(rebuyPlayer, sendMessage);

            if (rebuyResult.outcome === 'bought') {
              await engine.setLastEvent(
                `Rebought ${matched.name} for ${rebuyResult.buyPrice.toLocaleString()}`,
              );
              sendMessage({
                type: 'TRADE_REPORT',
                ea_id: matched.ea_id,
                price: rebuyResult.buyPrice,
                outcome: 'bought',
              } satisfies ExtensionMessage).catch(() => {});
              sendMessage({
                type: 'TRADE_REPORT',
                ea_id: matched.ea_id,
                price: rebuyPlayer.sell_price,
                outcome: 'listed',
              } satisfies ExtensionMessage).catch(() => {});
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

      // ── Inter-cycle pause (D-28, AUTO-05): avoid hammering EA servers ─────
      await engine.setState('IDLE', 'Cycle complete — waiting before next cycle');
      await engine.log('Cycle complete — pausing before next cycle');
      await jitter(5000, 10000);
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
