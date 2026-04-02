---
phase: quick
plan: 260402-enx
subsystem: scanner
tags: [playwright, cloudflare, scanner, http-client]
dependency_graph:
  requires: []
  provides: [playwright-prices-fetcher]
  affects: [src/server/scanner.py, src/futgg_client.py]
tech_stack:
  added: [playwright==1.58.0 (already in requirements.txt)]
  patterns: [sync-to-async bridge via run_coroutine_threadsafe, browser-context cookie jar for API requests]
key_files:
  created:
    - src/server/playwright_client.py
  modified:
    - src/futgg_client.py
    - src/server/scanner.py
    - src/server/scanner_main.py
decisions:
  - PlaywrightPricesClient bridges sync ThreadPoolExecutor threads to async Playwright via run_coroutine_threadsafe
  - prices_fetcher wrapped data is normalised to {"data": prices} so existing defn/prices unpacking is unchanged
  - Rate limiting applied before prices_fetcher call using same _sync_rate_lock as curl_cffi calls
  - Playwright browser stopped before curl_cffi session in stop() for clean shutdown order
metrics:
  duration: 140s
  completed: 2026-04-02
  tasks_completed: 2
  files_changed: 4
---

# Quick 260402-enx: Replace curl_cffi with Playwright for player-prices endpoint

**One-liner:** Playwright Chromium browser replaces curl_cffi for fut.gg player-prices calls, auto-solving Cloudflare JS challenge that curl_cffi cannot bypass.

## What Was Built

### src/server/playwright_client.py (new)

`PlaywrightPricesClient` encapsulates the Playwright Chromium lifecycle:

- `start()` launches headless Chromium, creates a `BrowserContext`, then calls `_resolve_challenge()` to navigate to `https://www.fut.gg/players/` and wait 3 seconds for the Cloudflare managed JS challenge to complete. The cookie set is stored in the browser context.
- `stop()` closes browser context, browser, and Playwright instance.
- `set_loop(loop)` stores the main asyncio event loop reference for the sync bridge.
- `get_prices_sync(ea_id)` bridges ThreadPoolExecutor threads to the async `_fetch_prices` coroutine via `asyncio.run_coroutine_threadsafe(..., loop).result(timeout=30)`.
- `_fetch_prices(ea_id)` uses `BrowserContext.request` (APIRequestContext) to GET the prices endpoint. On 403, calls `_resolve_challenge()` and retries once.
- `_resolve_challenge()` opens a temporary page, navigates to `fut.gg/players/`, waits 3 seconds, then closes the page. This refreshes the browser cookie jar.

### src/futgg_client.py (modified)

`get_player_market_data_sync` signature extended:

```python
def get_player_market_data_sync(
    self, ea_id: int, sync_client: Session, prices_fetcher=None,
) -> Optional[PlayerMarketData]:
```

When `prices_fetcher` is provided:
- Rate limiting is applied via `_sync_rate_lock` (same lock as curl_cffi calls)
- `prices_fetcher(ea_id)` is called — returns the parsed `data` dict or None
- Result is normalised to `{"data": prices}` so existing unpacking (`prices_data["data"]`) is unchanged
- When None, falls back to `_get_sync()` via curl_cffi (backward compat)

### src/server/scanner.py (modified)

- Imports `PlaywrightPricesClient`
- `__init__` adds `self._pw_client: Optional[PlaywrightPricesClient] = None`
- `start()` creates and starts `PlaywrightPricesClient`, then calls `set_loop(asyncio.get_running_loop())`
- `stop()` stops Playwright browser before curl_cffi session
- `_fetch_sync()` inside `scan_player` passes `prices_fetcher=self._pw_client.get_prices_sync`

### src/server/scanner_main.py (modified)

Added Playwright Chromium installation at `main()` startup:

```python
subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
```

Idempotent — no-ops on subsequent runs when binary is already present.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Self-Check: PASSED

- `src/server/playwright_client.py` — exists
- `src/futgg_client.py` — modified with prices_fetcher param
- `src/server/scanner.py` — wired PlaywrightPricesClient
- `src/server/scanner_main.py` — installs chromium on startup
- Commit 8cd7e72 — Task 1
- Commit 9276cae — Task 2
