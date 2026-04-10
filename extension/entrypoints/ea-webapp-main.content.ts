/**
 * Main world content script for the EA Sports FC Web App.
 *
 * Runs with `world: 'MAIN'` so it has access to EA's internal JavaScript globals
 * (services.Item, repositories.Item, ItemPile, UTSearchCriteriaDTO, etc.).
 *
 * Does NOT have access to chrome.runtime or chrome.storage APIs.
 * Those are bridged via window.postMessage to the isolated world content script.
 *
 * Responsibilities:
 *   - Hosts the AutomationEngine and runAutomationLoop (they need EA globals)
 *   - Listens for automation commands from the isolated world (start/stop/getStatus)
 *   - Uses bridgedSendMessage for chrome.runtime.sendMessage calls
 *   - Uses MainWorldStorageAdapter for chrome.storage.local persistence
 */
import {
  initMainWorldBridge,
  bridgedSendMessage,
  bridgedStorageGet,
  type AutomationCommand,
  type AutomationResult,
} from '../src/ea-bridge';
import { AutomationEngine } from '../src/automation';
import { MainWorldStorageAdapter } from '../src/automation-storage-main';
import { runAutomationLoop } from '../src/automation-loop';
import { runAlgoAutomationLoop } from '../src/algo-automation-loop';
import {
  getCoins,
  getTransferList,
  searchMarket,
  buildCriteria,
  listItem,
  relistAll,
  roundToNearestStep,
  getBeforeStepValue,
  MAX_PRICE,
} from '../src/ea-services';
import { jitter } from '../src/automation';

const RATE_LIMIT_ERROR_CODE = 460;
const EA_PAGE_SIZE = 20;

/**
 * Discover current lowest BIN for a player via transfer market search.
 * Same narrowing algorithm used by algo-sell-cycle and algo-transfer-list-sweep.
 */
async function discoverLowestBinForRelist(
  ea_id: number,
  fallbackPrice: number,
): Promise<number> {
  const MAX_NARROW_STEPS = 6;
  let currentMax = MAX_PRICE;
  let lastCheapest = fallbackPrice;

  for (let step = 0; step < MAX_NARROW_STEPS; step++) {
    const criteria = buildCriteria(ea_id, currentMax);
    if (step > 0) await jitter(1000, 2000);
    const result = await searchMarket(criteria);

    if (!result.success) {
      if (result.error === RATE_LIMIT_ERROR_CODE) {
        await jitter(4000, 8000);
        step--;
        continue;
      }
      return lastCheapest;
    }

    if (result.items.length === 0) return lastCheapest;

    let lowestBin = Infinity;
    for (const item of result.items) {
      const bin = item.getAuctionData().buyNowPrice;
      if (bin < lowestBin) lowestBin = bin;
    }
    lastCheapest = lowestBin;

    if (result.items.length < EA_PAGE_SIZE) return lowestBin;

    if (currentMax === lowestBin) {
      const below = getBeforeStepValue(lowestBin);
      if (below <= 0) return lowestBin;
      currentMax = below;
    } else {
      currentMax = lowestBin;
    }
  }

  return lastCheapest;
}

/**
 * Run the health check + maintenance routine.
 *
 * 1. Test session via getCoins()
 * 2. If alive, relist expired algo positions at current market price
 * 3. Then relistAll() for non-algo expired items
 */
async function runHealthCheck(
  sendMessage: (msg: any) => Promise<any>,
): Promise<{ healthy: boolean; relisted_algo: number; relisted_other: number }> {
  // Step 1: Session test
  try {
    getCoins();
  } catch {
    return { healthy: false, relisted_algo: 0, relisted_other: 0 };
  }

  let relistedAlgo = 0;
  let relistedOther = 0;

  // Step 2: Get transfer list and find expired items
  const { groups, success } = await getTransferList();
  if (!success || groups.expired.length === 0) {
    return { healthy: true, relisted_algo: 0, relisted_other: 0 };
  }

  // Step 3: Get algo positions from backend to identify which expired items are algo
  let algoEaIds = new Set<number>();
  try {
    const statusRes = await sendMessage({ type: 'ALGO_STATUS_REQUEST' });
    if (statusRes?.type === 'ALGO_STATUS_RESULT' && statusRes.data) {
      for (const pos of statusRes.data.positions) {
        algoEaIds.add(pos.ea_id);
      }
    }
  } catch {
    // Can't reach backend — skip algo-specific relist, just do relist-all
    const relistResult = await relistAll();
    if (relistResult.success) {
      relistedOther = groups.expired.length;
    }
    return { healthy: true, relisted_algo: 0, relisted_other: relistedOther };
  }

  // Step 4: Relist expired algo positions with price adjustment
  const algoExpired = groups.expired.filter(item => algoEaIds.has(item.definitionId));
  const nonAlgoExpired = groups.expired.filter(item => !algoEaIds.has(item.definitionId));

  // Group algo items by ea_id for efficient price discovery (one search per player)
  const algoByEaId = new Map<number, typeof algoExpired>();
  for (const item of algoExpired) {
    const list = algoByEaId.get(item.definitionId) ?? [];
    list.push(item);
    algoByEaId.set(item.definitionId, list);
  }

  for (const [ea_id, items] of algoByEaId) {
    // Discover current lowest BIN
    const fallback = items[0].getAuctionData().buyNowPrice || 10000;
    await jitter(1000, 2000);
    const lowestBin = await discoverLowestBinForRelist(ea_id, fallback);
    const listBin = roundToNearestStep(getBeforeStepValue(lowestBin));
    const listStart = roundToNearestStep(getBeforeStepValue(listBin));

    for (const expItem of items) {
      await jitter(1000, 2000);
      const listResult = await listItem(expItem, listStart, listBin);
      if (listResult.success) {
        relistedAlgo++;
      } else {
        console.warn(`[health-check] Relist failed for algo defId=${expItem.definitionId} (error ${listResult.error})`);
      }
    }

    // Report relist to backend
    if (relistedAlgo > 0) {
      try {
        await sendMessage({
          type: 'ALGO_POSITION_RELIST',
          ea_id,
          price: listBin,
          quantity: items.length,
        });
      } catch {
        console.warn(`[health-check] ALGO_POSITION_RELIST report failed for ea_id=${ea_id}`);
      }
    }
  }

  // Step 5: Relist all non-algo expired items at their previous prices
  if (nonAlgoExpired.length > 0) {
    await jitter(1000, 2000);
    const relistResult = await relistAll();
    if (relistResult.success) {
      relistedOther = nonAlgoExpired.length;
    }
  }

  return { healthy: true, relisted_algo: relistedAlgo, relisted_other: relistedOther };
}

const MSG_SOURCE = 'op-seller';

export default defineContentScript({
  matches: ['https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*'],
  runAt: 'document_idle',
  world: 'MAIN',
  main() {
    // Initialize the bridge client (for calling chrome APIs via isolated world)
    initMainWorldBridge();

    // Create automation engines with bridged sendMessage and storage
    const storageAdapter = new MainWorldStorageAdapter();
    const automationEngine = new AutomationEngine(bridgedSendMessage, storageAdapter);

    // Algo trading engine — separate from OP sell automation, same main world context
    const algoStorageAdapter = new MainWorldStorageAdapter();
    const algoEngine = new AutomationEngine(bridgedSendMessage, algoStorageAdapter);

    // Listen for automation commands from the isolated world content script
    window.addEventListener('message', async (event: MessageEvent) => {
      if (event.source !== window) return;
      const data = event.data;
      if (!data || data.source !== MSG_SOURCE || data.type !== 'automation-command') return;

      const command = data as AutomationCommand;
      const response: AutomationResult = {
        source: MSG_SOURCE,
        direction: 'to-isolated',
        type: 'automation-result',
        id: command.id,
      };

      try {
        switch (command.command) {
          case 'start': {
            const result = await automationEngine.start();
            if (result.success) {
              // Run the main loop — errors are funneled through engine.setError
              runAutomationLoop(automationEngine, bridgedSendMessage)
                .catch(err => automationEngine.setError(
                  err instanceof Error ? err.message : String(err),
                ));
            }
            response.result = result;
            break;
          }
          case 'stop': {
            const result = await automationEngine.stop();
            response.result = result;
            break;
          }
          case 'getStatus': {
            response.result = automationEngine.getStatus();
            break;
          }
          case 'algo-start': {
            const result = await algoEngine.start();
            if (result.success) {
              runAlgoAutomationLoop(algoEngine, bridgedSendMessage)
                .catch(err => algoEngine.setError(
                  err instanceof Error ? err.message : String(err),
                ));
            }
            response.result = result;
            break;
          }
          case 'algo-stop': {
            const result = await algoEngine.stop();
            response.result = result;
            break;
          }
          case 'algo-getStatus': {
            response.result = algoEngine.getStatus();
            break;
          }
          case 'algo-health-check': {
            const healthResult = await runHealthCheck(bridgedSendMessage);
            response.result = healthResult;
            break;
          }
        }
      } catch (err) {
        response.error = err instanceof Error ? err.message : String(err);
      }

      window.postMessage(response, '*');
    });

    console.log('[OP Seller Main] Main world script loaded — EA globals accessible');

    // Auto-start algo if master is in MONITORING state (handles page refresh).
    // After a refresh, the content script re-injects but the algo engine is fresh/stopped.
    // The master still thinks it's MONITORING. Check master state and restart if needed.
    bridgedStorageGet<{ status: string }>('algoMasterState').then(state => {
      if (state && (state.status === 'MONITORING' || state.status === 'SPAWNING')) {
        console.log('[OP Seller Main] Master is active, auto-starting algo engine');
        algoEngine.start().then(result => {
          if (result.success) {
            runAlgoAutomationLoop(algoEngine, bridgedSendMessage)
              .catch(err => algoEngine.setError(
                err instanceof Error ? err.message : String(err),
              ));
          }
        });
      }
    }).catch(() => {
      // Bridge not ready yet — master health check will catch it
    });
  },
});
