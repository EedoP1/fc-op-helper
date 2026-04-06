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
  type AutomationCommand,
  type AutomationResult,
} from '../src/ea-bridge';
import { AutomationEngine } from '../src/automation';
import { MainWorldStorageAdapter } from '../src/automation-storage';
import { runAutomationLoop } from '../src/automation-loop';

const MSG_SOURCE = 'op-seller';

export default defineContentScript({
  matches: ['https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*'],
  runAt: 'document_idle',
  world: 'MAIN',
  main() {
    // Initialize the bridge client (for calling chrome APIs via isolated world)
    initMainWorldBridge();

    // Create automation engine with bridged sendMessage and storage
    const storageAdapter = new MainWorldStorageAdapter();
    const automationEngine = new AutomationEngine(bridgedSendMessage, storageAdapter);

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
        }
      } catch (err) {
        response.error = err instanceof Error ? err.message : String(err);
      }

      window.postMessage(response, '*');
    });

    console.log('[OP Seller Main] Main world script loaded — EA globals accessible');
  },
});
