---
phase: quick
plan: 260402-enx
type: execute
wave: 1
depends_on: []
files_modified:
  - src/server/scanner.py
  - src/server/scanner_main.py
  - src/futgg_client.py
autonomous: true
requirements: []
must_haves:
  truths:
    - "Scanner fetches player-prices data via Playwright browser context instead of curl_cffi"
    - "Scanner still fetches player-item-definitions via curl_cffi (unchanged)"
    - "On 403 from player-prices, scanner automatically re-navigates to fut.gg to re-solve Cloudflare challenge"
    - "Rate limiting still applies to Playwright requests (0.25s minimum interval)"
  artifacts:
    - path: "src/server/playwright_client.py"
      provides: "Playwright browser lifecycle and prices-fetch wrapper"
    - path: "src/futgg_client.py"
      provides: "Modified get_player_market_data_sync to accept a prices-fetcher callable"
    - path: "src/server/scanner.py"
      provides: "Scanner wires Playwright lifecycle and passes prices-fetcher to futgg_client"
    - path: "src/server/scanner_main.py"
      provides: "Installs Playwright chromium browser on first run"
  key_links:
    - from: "src/server/scanner.py"
      to: "src/server/playwright_client.py"
      via: "PlaywrightPricesClient created in start(), closed in stop()"
    - from: "src/server/scanner.py"
      to: "src/futgg_client.py"
      via: "passes pw_client.get_prices_sync as callable to get_player_market_data_sync"
---

<objective>
Replace curl_cffi with Playwright for the player-prices endpoint to bypass Cloudflare's JS challenge.

Purpose: Cloudflare now serves a managed JS challenge on fut.gg's /api/fut/player-prices/ endpoint that curl_cffi cannot solve (all fingerprints return 403). Playwright can solve it by running a real browser, then use the browser's cookie jar for subsequent raw HTTP requests.

Output: Scanner uses Playwright APIRequestContext for prices calls, curl_cffi for definitions calls. Auto-retries Cloudflare challenge on 403.
</objective>

<execution_context>
@C:\Users\maftu\.claude\get-shit-done\workflows\execute-plan.md
@C:\Users\maftu\.claude\get-shit-done\templates\summary.md
</execution_context>

<context>
@src/server/scanner.py
@src/server/scanner_main.py
@src/futgg_client.py
@src/config.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Create PlaywrightPricesClient module</name>
  <files>src/server/playwright_client.py</files>
  <action>
Create a new module `src/server/playwright_client.py` that encapsulates Playwright browser lifecycle and prices-endpoint fetching.

Class `PlaywrightPricesClient`:

**Lifecycle (async):**
- `async start()`: Launch Playwright, start chromium browser (headless=True), create a BrowserContext. Navigate to `https://www.fut.gg/players/` to solve the Cloudflare JS challenge (wait for `networkidle` or `domcontentloaded` + a short sleep of ~3s to let challenge complete). Store `browser_context.request` (the APIRequestContext) for later use.
- `async stop()`: Close browser context, browser, and Playwright instance.

**Prices fetch (sync-compatible wrapper):**
- `get_prices_sync(ea_id: int) -> dict | None`: This is called from ThreadPoolExecutor threads. Since Playwright's APIRequestContext.get() is async, this method must schedule the coroutine on the Playwright's event loop. Approach: store a reference to the event loop running Playwright (the main asyncio loop) and use `asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=30)` to bridge sync-to-async.
- Internal `async _fetch_prices(ea_id: int) -> dict | None`: Uses `self._api_context.get(url, headers=...)` to fetch `/api/fut/player-prices/26/{ea_id}/`. On success, parse JSON and return `data["data"]`. On 403 status, call `_resolve_challenge()` and retry once. On other errors, log and return None.
- `async _resolve_challenge()`: Navigate the page to `https://www.fut.gg/players/` again, wait for load. Log that challenge was re-solved. This refreshes the cookies in the browser context.

**Rate limiting:** This module does NOT do its own rate limiting -- the caller (futgg_client.py's `_get_sync`) already handles it via `_sync_rate_lock` and `_MIN_REQUEST_INTERVAL`.

**Headers:** Set Accept: application/json and Referer: https://www.fut.gg/players/ on requests (same as FutGGClient.DEFAULT_HEADERS).

**Logging:** Use `logger = logging.getLogger(__name__)`. Log browser launch, challenge solve, 403 retries, errors.
  </action>
  <verify>
    <automated>python -c "from src.server.playwright_client import PlaywrightPricesClient; print('Import OK')"</automated>
  </verify>
  <done>PlaywrightPricesClient class exists with start/stop lifecycle and get_prices_sync bridge method</done>
</task>

<task type="auto">
  <name>Task 2: Wire Playwright into scanner and futgg_client</name>
  <files>src/futgg_client.py, src/server/scanner.py, src/server/scanner_main.py</files>
  <action>
**futgg_client.py changes:**

Modify `get_player_market_data_sync()` signature to accept an optional `prices_fetcher` callable:

```python
def get_player_market_data_sync(
    self, ea_id: int, sync_client: Session, prices_fetcher=None,
) -> Optional[PlayerMarketData]:
```

Inside the method, change the prices fetch logic:
- If `prices_fetcher` is provided, call `prices = prices_fetcher(ea_id)` to get the prices data directly (already parsed, returns the `data` dict or None).
- If `prices_fetcher` is None, use the existing `_get_sync(f"/api/fut/player-prices/26/{ea_id}/")` path (backward compat).
- The definitions fetch continues using `_get_sync()` with the sync curl_cffi client as before.

The rate limiting in `_get_sync` should still apply to the definitions call. For the prices call via `prices_fetcher`, the rate limiting is handled by the fact that `_get_sync` is called for definitions first (maintaining the interval). However, to be safe, add rate-limit enforcement before calling `prices_fetcher` too -- acquire `_sync_rate_lock`, enforce `_MIN_REQUEST_INTERVAL`, then call `prices_fetcher(ea_id)`.

**scanner.py changes:**

1. Import `PlaywrightPricesClient` from `src.server.playwright_client`.
2. In `__init__`, add `self._pw_client: Optional[PlaywrightPricesClient] = None`.
3. In `start()`:
   - After creating the ThreadPoolExecutor, create and start the PlaywrightPricesClient:
     ```python
     self._pw_client = PlaywrightPricesClient()
     await self._pw_client.start()
     ```
   - Store the running event loop reference on the pw_client so it can bridge sync-to-async:
     ```python
     self._pw_client.set_loop(asyncio.get_running_loop())
     ```
4. In `stop()`: Call `await self._pw_client.stop()` before closing curl_cffi client.
5. In `_scan_player_inner`, modify `_fetch_sync()`:
   - Pass `prices_fetcher=self._pw_client.get_prices_sync` to `get_player_market_data_sync`:
     ```python
     def _fetch_sync():
         return self._client.get_player_market_data_sync(
             ea_id, self._sync_client,
             prices_fetcher=self._pw_client.get_prices_sync,
         )
     ```

**scanner_main.py changes:**

Add Playwright browser installation at the top of `main()`, before creating engine:
```python
import subprocess
logger.info("Ensuring Playwright Chromium is installed...")
subprocess.run(
    ["python", "-m", "playwright", "install", "chromium"],
    check=True,
)
```

This ensures the browser binary is available on first run or after updates.
  </action>
  <verify>
    <automated>python -c "from src.server.scanner import ScannerService; from src.futgg_client import FutGGClient; print('Import OK')"</automated>
  </verify>
  <done>Scanner starts Playwright browser on startup, uses it for all player-prices requests, falls back to re-solving challenge on 403. curl_cffi still handles player-item-definitions. Rate limiting preserved.</done>
</task>

</tasks>

<verification>
1. `python -c "from src.server.playwright_client import PlaywrightPricesClient; print('OK')"` -- imports clean
2. `python -c "from src.server.scanner import ScannerService; print('OK')"` -- no circular imports
3. Manual verification: Start scanner process, observe logs for "Playwright browser started", "Cloudflare challenge solved", and successful player-prices fetches returning data instead of 403
</verification>

<success_criteria>
- Scanner process starts Playwright chromium, navigates to fut.gg to solve Cloudflare challenge
- All player-prices requests go through Playwright's APIRequestContext (browser cookie jar + TLS)
- All player-item-definitions requests continue via curl_cffi (unchanged)
- On 403 from prices endpoint, challenge is automatically re-solved and request retried
- Rate limiting (0.25s interval) applies to both Playwright and curl_cffi requests
- No changes to scoring, DB writes, or dispatch logic
</success_criteria>

<output>
After completion, create `.planning/quick/260402-enx-replace-curl-cffi-with-playwright-for-pl/260402-enx-SUMMARY.md`
</output>
