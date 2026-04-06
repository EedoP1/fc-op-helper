/**
 * Main automation loop orchestrator.
 *
 * Drives the continuous buy/list/relist cycle:
 *   Phase 0: Sweep unassigned pile
 *   Phase A: Transfer list cycle (relist + clear + report)
 *   Phase B: Fetch actions-needed from backend
 *   Phase C: Buy cycle for each BUY action
 *   Inter-cycle: sleep until earliest card expires
 *
 * No DOM interaction — all operations use EA service layer and message passing.
 */
import { AutomationEngine, jitter } from './automation';
import { executeBuyCycle, type BuyCycleResult } from './buy-cycle';
import {
  executeTransferListCycle,
  scanTransferList,
  type TransferListCycleResult,
} from './transfer-list-cycle';
import { getUnassigned, moveItem, isPileFull, getCoins } from './ea-services';
import type { ActionNeeded, ExtensionMessage } from './messages';

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Check if an error indicates an expired EA session (401/auth failure). */
function isSessionError(error: unknown): boolean {
  const msg = (error instanceof Error ? error.message : String(error)).toLowerCase();
  return msg.includes('401') || msg.includes('expired') || msg.includes('session') || msg.includes('unauthorized');
}

/** Check if a BuyCycleResult reason indicates insufficient coins. */
function isInsufficientCoinsError(reason: string): boolean {
  const lower = reason.toLowerCase();
  return lower.includes('coin') || lower.includes('insufficient') || lower.includes('funds');
}

// ── Main loop export ─────────────────────────────────────────────────────────

/**
 * Run the continuous automation cycle until stopped or an error occurs.
 *
 * @param engine       AutomationEngine state machine
 * @param sendMessage  Callback to relay messages to the service worker / backend
 */
export async function runAutomationLoop(
  engine: AutomationEngine,
  sendMessage: (msg: any) => Promise<any>,
): Promise<void> {
  const signal = engine.getAbortSignal();
  const stopped = () => signal?.aborted ?? false;

  // Price guard cooldown: prevents retrying overpriced players every cycle.
  const PRICE_GUARD_COOLDOWN_MS = 5 * 60_000;
  const priceGuardCooldown = new Map<number, number>();

  try {
    while (!stopped()) {
      // ── Phase 0: Sweep unassigned pile ──────────────────────────────────
      try {
        const unassigned = await getUnassigned();
        if (unassigned.length > 0) {
          await engine.log(`Found ${unassigned.length} unassigned items — moving to transfer list`);
          for (const item of unassigned) {
            if (stopped()) return;
            await moveItem(item, 5); // 5 = ItemPile.TRANSFER
          }
        }
      } catch (err) {
        if (isSessionError(err)) {
          await engine.setError('EA session expired — please log in and restart automation');
          return;
        }
        await engine.log(`Unassigned sweep error: ${err instanceof Error ? err.message : String(err)} — continuing`);
      }

      if (stopped()) return;

      // ── Phase A: Transfer list cycle ───────────────────────────────────
      await engine.setState('SCANNING', 'Scanning transfer list');

      let cycleResult: TransferListCycleResult | null = null;
      try {
        cycleResult = await executeTransferListCycle(sendMessage);

        await engine.setLastEvent(
          `Transfer list: ${cycleResult.groups.active.length} active, ${cycleResult.groups.expired.length} expired, ${cycleResult.groups.sold.length} sold`,
        );

        if (cycleResult.relistedCount > 0) {
          await engine.setLastEvent(`Relisted ${cycleResult.relistedCount} cards`);
        }

        if (cycleResult.soldCleared > 0) {
          await engine.log(`Cleared ${cycleResult.soldCleared} sold cards`);
        }

        // Log profit for sold cards
        for (const soldItem of cycleResult.groups.sold) {
          const price = soldItem.getAuctionData().buyNowPrice;
          await engine.log(`Sold: defId=${soldItem.definitionId} for ${price.toLocaleString()} — rebuy queued via backend`);
          engine.addProfit(price);
        }

        await engine.setState('SCANNING', 'Transfer list cycle complete');
      } catch (err) {
        if (isSessionError(err)) {
          await engine.setError('EA session expired — please log in and restart automation');
          return;
        }
        await engine.log(`Transfer list cycle error: ${err instanceof Error ? err.message : String(err)} — continuing`);
      }

      if (stopped()) return;

      // ── Phase B: Get actions-needed ────────────────────────────────────
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

      if (stopped()) return;

      // ── Phase C: Buy cycle ─────────────────────────────────────────────

      // Check daily cap from cycle result or fresh request
      let isCapped = cycleResult?.isCapped ?? false;
      if (!isCapped) {
        try {
          const capRes = await sendMessage({ type: 'DAILY_CAP_REQUEST' } satisfies ExtensionMessage);
          if (capRes && capRes.type === 'DAILY_CAP_RESULT') {
            isCapped = capRes.capped === true;
          }
        } catch {
          await engine.log('DAILY_CAP_REQUEST failed — assuming not capped');
        }
      }

      // Purge expired cooldown entries
      const now = Date.now();
      for (const [ea_id, skippedAt] of priceGuardCooldown) {
        if (now - skippedAt >= PRICE_GUARD_COOLDOWN_MS) {
          priceGuardCooldown.delete(ea_id);
        }
      }

      const buyPlayers = actionsNeeded.filter(
        a => a.action === 'BUY' && !priceGuardCooldown.has(a.ea_id),
      );
      let outOfCoins = false;

      // Transfer list full check via EA service
      const transferListFull = isPileFull(5); // 5 = ItemPile.TRANSFER
      let transferListCount = cycleResult
        ? cycleResult.groups.all.length - cycleResult.soldCleared
        : 0;

      if (!isCapped && !transferListFull && buyPlayers.length > 0) {
        await engine.setState('BUYING', 'Starting buy cycle');

        let consecutiveFailures = 0;
        const CAPTCHA_THRESHOLD = 3;

        for (const player of buyPlayers) {
          if (stopped()) return;

          // Too many consecutive failures — possible CAPTCHA
          if (consecutiveFailures >= CAPTCHA_THRESHOLD) {
            await engine.setError(`${consecutiveFailures} consecutive buy failures — possible CAPTCHA or UI block. Please check the EA Web App.`);
            return;
          }

          // Transfer list full check
          if (isPileFull(5)) {
            await engine.log('Transfer list full — stopping buy phase');
            break;
          }

          if (outOfCoins) {
            await engine.log(`Out of coins — skipping buy for ${player.name}`);
            continue;
          }

          await engine.setState('BUYING', `Buying: ${player.name}`);

          // Fetch fresh price before buying
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

          // Increment daily cap counter per buy attempt
          sendMessage({ type: 'DAILY_CAP_INCREMENT' } satisfies ExtensionMessage).catch(() => {});

          let result: BuyCycleResult;
          try {
            result = await executeBuyCycle(freshPlayer, sendMessage);
          } catch (err) {
            if (isSessionError(err)) {
              await engine.setError('EA session expired — please log in and restart automation');
              return;
            }
            consecutiveFailures++;
            await engine.setLastEvent(`Error buying ${player.name}: ${err instanceof Error ? err.message : String(err)}`);
            if (!stopped()) await jitter();
            continue;
          }

          if (result.outcome === 'bought') {
            consecutiveFailures = 0;
            transferListCount++;
            await engine.setLastEvent(`Bought ${player.name} for ${result.buyPrice.toLocaleString()}`);

            // Report buy + list to backend
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

            // Check if now capped
            try {
              const capCheckRes = await sendMessage({ type: 'DAILY_CAP_REQUEST' } satisfies ExtensionMessage);
              if (capCheckRes && capCheckRes.type === 'DAILY_CAP_RESULT' && capCheckRes.capped) {
                await engine.log('Daily cap reached — stopping buy phase');
                break;
              }
            } catch { /* ignore */ }

          } else if (result.outcome === 'skipped') {
            consecutiveFailures = 0;

            // Price guard cooldown
            const isPriceGuard = result.reason.toLowerCase().includes('price guard')
              || result.reason.toLowerCase().includes('above guard');
            if (isPriceGuard) {
              priceGuardCooldown.set(player.ea_id, Date.now());
            }

            await engine.setLastEvent(`Skipped ${player.name}: ${result.reason}`);

          } else if (result.outcome === 'error') {
            consecutiveFailures++;

            if (isInsufficientCoinsError(result.reason)) {
              outOfCoins = true;
              await engine.log('Out of coins — switching to relist-only mode for this cycle');
              continue;
            }

            if (result.reason.includes('unassigned pile')) {
              await engine.log('Listing failed (TL full) — stopping buy phase');
              break;
            }

            await engine.setLastEvent(`Error buying ${player.name}: ${result.reason}`);
          }

          if (!stopped()) await jitter();
        }
      } else if (transferListFull) {
        await engine.log('Transfer list full — skipping buy phase');
      } else if (isCapped) {
        await engine.log('Daily cap reached — skipping buy phase');
      } else if (buyPlayers.length === 0) {
        await engine.log(`No BUY actions from backend (${actionsNeeded.length} total actions)`);
      }

      if (stopped()) return;

      // ── Inter-cycle pause ──────────────────────────────────────────────
      // Sleep until the earliest card expires instead of rescanning constantly.
      const nothingToBuy = buyPlayers.length === 0 || isCapped || transferListFull;
      if (nothingToBuy) {
        // Try to find earliest expiry from active items
        let earliestExpireMs = Infinity;
        if (cycleResult && cycleResult.groups.active.length > 0) {
          for (const item of cycleResult.groups.active) {
            const remainSec = item.getAuctionData().expires; // seconds remaining, NOT a Unix timestamp
            const remainMs = remainSec * 1000;
            if (remainMs > 0 && remainMs < earliestExpireMs) {
              earliestExpireMs = remainMs;
            }
          }
        }

        if (earliestExpireMs < Infinity && earliestExpireMs > 10_000) {
          const waitMs = earliestExpireMs + 5_000;
          const waitMin = Math.max(1, Math.round(waitMs / 60_000));
          await engine.setState('IDLE', `Waiting ~${waitMin}m for next card to expire`);
          await engine.log(`Nothing to buy — sleeping ${waitMin}m until next card expires`);
          let remaining = waitMs;
          while (remaining > 0 && !stopped()) {
            const chunk = Math.min(remaining, 30_000);
            await new Promise(r => setTimeout(r, chunk));
            remaining -= chunk;
          }
        } else if (transferListFull) {
          // TL full but no expiry data available — sleep 5 minutes
          const FALLBACK_SLEEP_MS = 5 * 60_000;
          await engine.setState('IDLE', 'Transfer list full — waiting 5m before rechecking');
          await engine.log('No expiry data — sleeping 5m before rechecking transfer list');
          let remaining = FALLBACK_SLEEP_MS;
          while (remaining > 0 && !stopped()) {
            const chunk = Math.min(remaining, 30_000);
            await new Promise(r => setTimeout(r, chunk));
            remaining -= chunk;
          }
        } else {
          await engine.setState('IDLE', 'Waiting for cards to expire');
          await jitter(15_000, 30_000);
        }
      } else {
        await engine.setState('IDLE', 'Cycle complete — waiting before next cycle');
        await jitter(5000, 10000);
      }
    }
  } catch (err) {
    if (isSessionError(err)) {
      await engine.setError('EA session expired — please log in and restart automation');
      return;
    }
    const msg = err instanceof Error ? err.message : String(err);
    await engine.setError(`Unexpected error: ${msg}`);
  }
}
