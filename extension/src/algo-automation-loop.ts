/**
 * Algo trading automation loop.
 *
 * Polls the backend for pending signals and executes buy/sell cycles.
 * Runs continuously until stopped via the AutomationEngine abort signal.
 *
 * Loop:
 *   Phase A: If positions are listed, sweep TL for sold/expired
 *   Phase B: Poll for signal via ALGO_SIGNAL_REQUEST
 *     - If null: wait 30-60s, continue
 *   Phase C: Execute signal
 *     - BUY: executeAlgoBuyCycle, report 'bought'
 *     - SELL: executeAlgoSellCycle, report 'listed' (position stays alive)
 *   Jitter 3-5s between signals
 */
import { AutomationEngine, jitter } from './automation';
import { executeAlgoBuyCycle, type AlgoBuyCycleResult } from './algo-buy-cycle';
import { executeAlgoSellCycle, type AlgoSellCycleResult } from './algo-sell-cycle';
import { runAlgoTransferListSweep } from './algo-transfer-list-sweep';
import type { ExtensionMessage, AlgoSignal, AlgoStatusData } from './messages';

/**
 * Run the algo trading automation loop until stopped or error.
 *
 * @param engine       AutomationEngine for state tracking and abort signal
 * @param sendMessage  Callback to relay messages to the service worker / backend
 */
export async function runAlgoAutomationLoop(
  engine: AutomationEngine,
  sendMessage: (msg: any) => Promise<any>,
): Promise<void> {
  const signal = engine.getAbortSignal();
  const stopped = () => signal?.aborted ?? false;

  try {
    while (!stopped()) {
      // ── Phase A: Transfer List Sweep ────────────────────────────────────
      // Check if any positions are listed (have listed_price set).
      // If so, sweep the TL for sold/expired cards.
      try {
        const statusRes = await sendMessage({ type: 'ALGO_STATUS_REQUEST' } satisfies ExtensionMessage);
        if (statusRes?.type === 'ALGO_STATUS_RESULT' && statusRes.data) {
          const statusData: AlgoStatusData = statusRes.data;
          // Positions with listed_price set are on the transfer list
          const listedPositions = statusData.positions.filter(p => p.listed_price != null);
          if (listedPositions.length > 0 && !stopped()) {
            await engine.setState('SCANNING', 'Sweeping transfer list');
            const positions = listedPositions.map(p => ({
              ea_id: p.ea_id,
              player_name: p.player_name,
              quantity: p.quantity,
              buy_price: p.buy_price,
              listed_price: p.listed_price,
            }));
            const sweepResult = await runAlgoTransferListSweep(sendMessage, positions, stopped);
            if (sweepResult.soldCount > 0 || sweepResult.relistedCount > 0) {
              await engine.setLastEvent(
                `TL sweep: ${sweepResult.soldCount} sold, ${sweepResult.relistedCount} relisted`,
              );
            }
          }
        }
      } catch (err) {
        await engine.log(`TL sweep error: ${err instanceof Error ? err.message : String(err)}`);
      }

      if (stopped()) return;

      // ── Phase B: Poll for next signal ───────────────────────────────────
      await engine.setState('SCANNING', 'Polling for algo signal');

      let algoSignal: AlgoSignal | null = null;
      try {
        const res = await sendMessage({ type: 'ALGO_SIGNAL_REQUEST' } satisfies ExtensionMessage);
        if (res && res.type === 'ALGO_SIGNAL_RESULT') {
          if (res.error) {
            await engine.log(`Signal poll error: ${res.error}`);
          }
          algoSignal = res.signal ?? null;
        }
      } catch (err) {
        await engine.log(`Signal poll failed: ${err instanceof Error ? err.message : String(err)}`);
      }

      if (stopped()) return;

      // No signal available — wait 30-60s before next poll
      if (!algoSignal) {
        await engine.setState('IDLE', 'No pending signals — waiting');
        const waitMs = 30_000 + Math.floor(Math.random() * 30_000);
        let remaining = waitMs;
        while (remaining > 0 && !stopped()) {
          const chunk = Math.min(remaining, 5_000);
          await new Promise(r => setTimeout(r, chunk));
          remaining -= chunk;
        }
        continue;
      }

      // ── Phase C: Execute signal ─────────────────────────────────────────
      if (algoSignal.action === 'BUY') {
        await engine.setState('BUYING', `Buying: ${algoSignal.player_name} x${algoSignal.quantity}`);

        let totalBought = 0;
        let lastPrice = 0;

        for (let i = 0; i < algoSignal.quantity; i++) {
          if (stopped()) return;

          await engine.setState('BUYING', `Buying: ${algoSignal.player_name} (${i + 1}/${algoSignal.quantity})`);
          const result: AlgoBuyCycleResult = await executeAlgoBuyCycle(algoSignal, sendMessage);

          if (result.outcome === 'bought') {
            totalBought += result.quantity;
            lastPrice = result.buyPrice;
            await engine.setLastEvent(
              `Bought ${algoSignal.player_name} for ${result.buyPrice.toLocaleString()}`,
            );
          } else if (result.outcome === 'skipped') {
            await engine.setLastEvent(
              `Skipped ${algoSignal.player_name}: ${result.reason}`,
            );
            break;
          } else {
            await engine.setLastEvent(
              `Error buying ${algoSignal.player_name}: ${result.reason}`,
            );
            break;
          }

          if (i < algoSignal.quantity - 1 && !stopped()) {
            await jitter(3000, 5000);
          }
        }

        // Report completion to backend
        if (!stopped()) {
          const outcome = totalBought > 0 ? 'bought' : 'skipped';
          try {
            await sendMessage({
              type: 'ALGO_SIGNAL_COMPLETE',
              signal_id: algoSignal.id,
              outcome,
              price: lastPrice,
              quantity: totalBought,
            } satisfies ExtensionMessage);
          } catch (err) {
            await engine.log(`Signal complete report failed: ${err instanceof Error ? err.message : String(err)}`);
          }
        }

      } else if (algoSignal.action === 'SELL') {
        await engine.setState('LISTING', `Listing: ${algoSignal.player_name} x${algoSignal.quantity}`);

        let totalListed = 0;
        let lastPrice = 0;

        for (let i = 0; i < algoSignal.quantity; i++) {
          if (stopped()) return;

          await engine.setState('LISTING', `Listing: ${algoSignal.player_name} (${i + 1}/${algoSignal.quantity})`);
          const result: AlgoSellCycleResult = await executeAlgoSellCycle(algoSignal, sendMessage);

          if (result.outcome === 'listed') {
            totalListed += result.quantity;
            lastPrice = result.sellPrice;
            await engine.setLastEvent(
              `Listed ${algoSignal.player_name} for ${result.sellPrice.toLocaleString()}`,
            );
          } else if (result.outcome === 'skipped') {
            await engine.setLastEvent(
              `Skipped sell ${algoSignal.player_name}: ${result.reason}`,
            );
            break;
          } else {
            await engine.setLastEvent(
              `Error selling ${algoSignal.player_name}: ${result.reason}`,
            );
            break;
          }

          if (i < algoSignal.quantity - 1 && !stopped()) {
            await jitter(3000, 5000);
          }
        }

        // Report listing to backend — outcome is 'listed', NOT 'sold'
        // Position stays alive until the TL sweep detects actual sale
        if (!stopped()) {
          const outcome = totalListed > 0 ? 'listed' : 'skipped';
          try {
            await sendMessage({
              type: 'ALGO_SIGNAL_COMPLETE',
              signal_id: algoSignal.id,
              outcome,
              price: lastPrice,
              quantity: totalListed,
            } satisfies ExtensionMessage);
          } catch (err) {
            await engine.log(`Signal complete report failed: ${err instanceof Error ? err.message : String(err)}`);
          }
        }
      }

      if (stopped()) return;

      // Jitter between signals
      await jitter(3000, 5000);
    }
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    await engine.setError(`Unexpected error: ${msg}`);
  }
}
