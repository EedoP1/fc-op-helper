# Automation Migration: DOM to `services.Item.*`

## Summary

Replace the Chrome extension's DOM-based automation (CSS selectors, synthetic clicks, polling loops, page navigation) with direct calls to EA's internal JavaScript framework (`services.Item.*`). This is the same approach used by FUT Enhancer (verified on FC26 live web app).

## Motivation

The current DOM automation has three problems:

1. **Reliability** — 281 CSS selectors break when EA updates the web app UI. Polling loops time out. Synthetic click sequences (pointerdown/mousedown/pointerup/mouseup/click) are fragile.
2. **Speed** — Human-like jitter (800-2500ms) between every DOM interaction, plus 200ms polling intervals with 5-15s timeouts, makes each buy cycle slow. Opportunities are missed.
3. **Convenience** — The web app must be visible, focused, and navigated to the correct page. Cannot run headless or in background.

## Approach

EA's FC26 Web App exposes its internal MVC framework as globals on `window`. All business logic lives in `services.Item.*` methods that handle HTTP requests, auth headers, and session management internally. Calling these methods produces HTTP requests identical to normal button clicks — same headers, same session, same everything.

Verified live on FC26 web app (2026-04-06):
- `services.Item` — 34 methods including search, bid, list, relist, transfer list, unassigned pile
- `services.User` — user data, coins (`services.User.getUser().coins.amount`)
- `repositories.Item` — pile status checks (`isPileFull`)
- `ItemPile` — enum (TRANSFER, PURCHASED, CLUB, INBOX, GIFT, STORAGE, EVOLUTION)
- `UTSearchCriteriaDTO` — search criteria constructor
- `UTCurrencyInputControl.PRICE_TIERS` — valid price steps for listing

FUT Enhancer (extension ID `boffdonfioidojlcpmfnkngipappmcoh`, version 26.1.3.8) was verified to use this exact approach — zero DOM manipulation for buy/sell/relist operations.

## Architecture

### New: EA Service Layer (`extension/src/ea-services.ts`)

Thin async wrapper around EA's globals. The only file that directly references EA's internal objects.

**Core utility:**
- `observableToPromise(observable)` — converts EA's `.observe(scope, callback)` pattern to standard Promises

**Market operations:**
- `searchMarket(criteria, page?)` — clears cache, calls `services.Item.searchTransferMarket()`, returns `{items, totalResults}`
- `buyItem(item, price)` — calls `services.Item.bid()`, returns `{success, errorCode?}`
- `listItem(item, startBid, buyNow, duration)` — calls `services.Item.list()`
- `relistAll()` — calls `services.Item.relistExpiredAuctions()`

**Pile operations:**
- `getTransferList()` — calls `services.Item.requestTransferItems()`, returns `{sold, expired, active, unlisted}` using item's `getAuctionData().isSold()`, `.isExpired()`, `.isSelling()`, `.isInactive()` methods
- `getUnassigned()` — calls `services.Item.requestUnassignedItems()`
- `clearSold()` — calls `services.Item.clearSoldItems()`
- `moveItem(item, pile)` — checks `repositories.Item.isPileFull(pile)` first, then calls `services.Item.move()`

**Price utilities:**
- `roundToNearestStep(price)` — snaps price to valid EA tier using `UTCurrencyInputControl.PRICE_TIERS`
- `getBeforeStepValue(price)` — returns the previous valid price step
- `buildCriteria(eaId, maxBuy, minBid?)` — creates `UTSearchCriteriaDTO` with fields set

**User data:**
- `getCoins()` — returns `services.User.getUser().coins.amount`
- `isPileFull(pile)` — returns `repositories.Item.isPileFull(pile)`

### EA Price Tiers

EA enforces fixed price steps when listing items. Listing at an invalid price will fail.

| Price Range | Step Size |
|-------------|-----------|
| 0 - 149 | 150 |
| 150 - 999 | 50 |
| 1,000 - 9,999 | 100 |
| 10,000 - 49,999 | 250 |
| 50,000 - 99,999 | 500 |
| 100,000+ | 1,000 |

When listing: `startBid = roundToNearestStep(getBeforeStepValue(sell_price))`, `buyNow = roundToNearestStep(sell_price)`.

These functions reference `UTCurrencyInputControl.PRICE_TIERS` (EA global), so they auto-update if EA changes tiers.

### Rewritten: Buy Cycle (`extension/src/buy-cycle.ts`)

Current: 663 lines of DOM manipulation. New: ~80-100 lines.

```
For each BUY action {ea_id, buy_price, sell_price} from backend:

  1. Build criteria: defId=[ea_id], maxBuy=buy_price, minBid=random(0..1000)
  2. clearTransferMarketCache()
  3. searchMarket(criteria) -> items[]
  4. If empty -> increment minBid (cache bust), retry up to 3x
  5. Pick cheapest item (lowest buyNowPrice)
  6. Price guard: skip if buyNowPrice > buy_price * 1.05
  7. buyItem(item, buyNowPrice)
     - Sniped (error 461) -> retry from step 1, up to 3x
     - Failed -> skip, report to backend
  8. listItem(item, roundToNearestStep(getBeforeStepValue(sell_price)),
                    roundToNearestStep(sell_price), 3600)
  9. Jitter delay 1-2s
  10. Report bought+listed to backend
```

No navigation. No DOM. No page dependency. Uses ea_id directly — no name matching or card verification needed.

### Rewritten: Transfer List Cycle (`extension/src/transfer-list-cycle.ts`)

Current: 200+ lines of DOM navigation, pagination, status parsing. New: ~30 lines.

```
  1. getTransferList() -> {sold, expired, active, unlisted}
  2. If expired.length > 0 -> relistAll()
  3. If sold.length > 0 -> clearSold()
  4. Return counts to automation loop
```

### Updated: Automation Loop (`extension/src/automation-loop.ts`)

Same phases, stripped of navigation and DOM waits:

- **Phase 0 (sweep unassigned):** `getUnassigned()` + `moveItem(item, ItemPile.TRANSFER)` per card
- **Phase A (transfer list):** `getTransferList()` + `relistAll()` + `clearSold()`
- **Phase B (fetch actions):** unchanged (backend HTTP call)
- **Phase C (buy cycle):** new service-based buy cycle
- **Inter-cycle sleep:** stays (wait for next card to expire)

All guard rails preserved:
- Daily cap gating
- Transfer list occupancy (`isPileFull(ItemPile.TRANSFER)`)
- Out-of-coins detection (`getCoins()`)
- Price guard cooldown (5-min skip window)
- Graceful stop via AbortSignal

### Updated: State Machine (`extension/src/automation.ts`)

**Keep:** State enum (IDLE/BUYING/LISTING/SCANNING/RELISTING/STOPPED/ERROR), chrome.storage persistence, activity log (200 entry cap), session profit tracking, AbortSignal-based graceful stop, `jitter()` function.

**Delete:** `clickElement()`, `waitForElement()`, `waitForSearchResults()`, `typePrice()`, `requireElement()` — all DOM helpers.

### Session Expiry Detection

Current: checks DOM for login view. New: if any `services.Item.*` call returns an auth/session error, treat as session expired and stop gracefully.

## Files Changed

| File | Action | Reason |
|------|--------|--------|
| `extension/src/ea-services.ts` | **Create** | EA service wrapper layer |
| `extension/src/buy-cycle.ts` | **Rewrite** | 663 -> ~80-100 lines |
| `extension/src/transfer-list-cycle.ts` | **Rewrite** | 200+ -> ~30 lines |
| `extension/src/automation-loop.ts` | **Update** | Strip navigation/DOM, use ea-services |
| `extension/src/automation.ts` | **Update** | Remove DOM helpers, keep state machine |
| `extension/src/selectors.ts` | **Delete** | 281 lines, no longer needed |
| `extension/src/navigation.ts` | **Delete** | 111 lines, no longer needed |

## Files Unchanged

| File | Reason |
|------|--------|
| `extension/src/messages.ts` | IPC types unchanged |
| `extension/src/storage.ts` | Persistence unchanged |
| `extension/entrypoints/background.ts` | Service worker handlers unchanged |
| `extension/entrypoints/ea-webapp.content.ts` | Still injection point + overlay panel host |
| `extension/src/trade-observer.ts` | Passive DOM reading for overlay (optional future migration) |

## Risk Mitigations

- **Human-like delays:** 1-2s jitter between every action (same as current)
- **No parallel requests:** sequential buy cycles, one at a time
- **Cache-bust minBid:** random 0-1000, increment on retry
- **Same request volume:** one search + one buy + one list per player — identical HTTP footprint to DOM automation
- **FUT Enhancer precedent:** major extension, same approach, same API surface, shipping on FC26
- **If EA changes internals:** FUT Enhancer breaks too, giving us a canary signal

## Testing

- Unit tests for `ea-services.ts` price utility functions (roundToNearestStep, getBeforeStepValue)
- Integration test: run buy cycle on live web app with a cheap test player
- Verify backend reporting still works (same message format)
