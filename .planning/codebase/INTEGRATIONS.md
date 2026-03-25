# External Integrations

**Analysis Date:** 2026-03-25

## APIs & External Services

**fut.gg API:**
- Market data discovery and pricing
  - SDK/Client: `httpx.AsyncClient` (in `src/futgg_client.py`)
  - Base URL: https://www.fut.gg
  - No API key required (public API)

**Endpoints:**
- `/api/fut/players/v2/26/` - Paginated player discovery with price filtering (query params: `page`, `price__gte`, `price__lte`)
- `/api/fut/player-prices/26/{eaId}/` - Current prices, completed auctions (100 sales), live auctions, hourly price history
- `/api/fut/player-prices/26/?ids={id1},{id2},...` - Batch price fetch for multiple players (comma-separated EA IDs)
- `/api/fut/player-item-definitions/26/{eaId}/` - Card definition (stats, club, league, nation, rarity)

## Data Storage

**Databases:**
- Not used - Data is fetched from fut.gg API in real-time, not stored

**File Storage:**
- Local filesystem only
- CSV export of results to timestamped file (format: `op_sell_list_YYYYMMDD_HHMMSS.csv`)
- Written to project root directory

**Caching:**
- None - All requests made fresh to fut.gg

## Authentication & Identity

**Auth Provider:**
- None required
- fut.gg API is publicly accessible
- User-Agent header spoofed as Chrome browser to avoid blocking

**Headers used:**
- `User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36` (in `src/futgg_client.py:47-50`)
- `Accept: application/json`
- `Referer: https://www.fut.gg/players/`

## Monitoring & Observability

**Error Tracking:**
- Not integrated - Errors logged via Python logging module

**Logs:**
- Standard Python logging (console output)
- Log levels: DEBUG (verbose mode) or INFO (default)
- httpx/httpcore logs suppressed to WARNING level in non-verbose mode

## CI/CD & Deployment

**Hosting:**
- Not deployed - CLI tool run locally

**CI Pipeline:**
- Not configured - No automated pipeline detected

## Environment Configuration

**Required env vars:**
- None - Application requires only `--budget` CLI argument

**Secrets location:**
- No secrets in use (public API, no authentication)

## Webhooks & Callbacks

**Incoming:**
- None

**Outgoing:**
- None

## Request Patterns

**Rate Limiting:**
- Minimal delay between requests: 150ms (0.15s) hardcoded sleep after each GET call (in `src/futgg_client.py:73`)
- Discovery uses paginated requests with ~150ms between pages
- Batch player prices: concurrent requests with semaphore limit of 10 by default, configurable per call

**Concurrency:**
- Async HTTP client with configurable concurrency limit (default: 10 simultaneous requests)
- Semaphore-based throttling in `get_batch_market_data()` method (in `src/futgg_client.py:131`)

**Timeout:**
- 30-second timeout per request (in `src/futgg_client.py:55`)
- Requests are retried if they fail (up to httpx's default retry policy)

---

*Integration audit: 2026-03-25*
