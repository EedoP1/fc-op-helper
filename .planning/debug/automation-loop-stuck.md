---
status: awaiting_human_verify
trigger: "automation-loop-stuck: sold/expired players not cleared, expired not relisted, bought same player (Gwinn) 3x then reported failure"
created: 2026-04-01T00:00:00Z
updated: 2026-04-01T00:02:00Z
---

## Current Focus
<!-- OVERWRITE on each update - reflects NOW -->

hypothesis: Bug 3 root cause is DIFFERENT from previous fix. The buy succeeds but success detection fails because the code does a one-shot querySelector('button.accordian') check after only jitter(1000,2000) — insufficient wait for EA's post-buy DOM transition. Since success is not detected, code treats it as failure, navigates back (abandoning the bought card), re-searches, and buys again.
test: code trace — buy-cycle.ts lines 461-479 show one-shot check with no polling, no waitForElement
expecting: fix = replace one-shot check with waitForElement polling up to 8s; also handle possible second post-buy dialog
next_action: apply fix to buy-cycle.ts — replace jitter+querySelector with waitForElement for accordion

## Symptoms
<!-- Written during gathering, then IMMUTABLE -->

expected: After checking transfer list, sold players should be cleared, expired players should be relisted, and the buy cycle should not buy the same player multiple times.
actual: 6 sold players stayed on transfer list uncollected. Expired players were not relisted. The loop bought Gwinn 3 times, reported failure to buy, and moved to next player.
errors: No error messages shown — silent loop, no errors in panel or console.
reproduction: Run the automation loop with a portfolio that has sold and expired items on the transfer list.
started: Partially worked before — buying worked but clearing/relisting never worked right.

## Eliminated

(none yet)

## Evidence

- timestamp: 2026-04-01T00:00:00Z
  checked: trade-observer.ts readTransferList() — sold detection logic
  found: |
    Line 104: `else if (hasWonClass && rawStatus === 'expired')`
    This is the ONLY path that returns status='sold'. It requires BOTH:
      1. The item element has class "won"
      2. The status text is exactly "expired" (lowercase, after .trim().toLowerCase())
    BUT: There is also a direct STATUS_MAP entry: `'sold': 'sold'` (line 47).
    However, the explicit `hasWonClass` check at line 104 would only trigger for "won+expired".
    If EA shows "Sold" as the status text directly (without the "won" class trick),
    the STATUS_MAP['sold'] path at line 109 handles it.
    The concern: if items show a different status text or class combination in practice,
    neither path may match — items fall through `if (!status) continue`.
  implication: Sold detection may silently fail if EA's DOM uses a different pattern than expected.

- timestamp: 2026-04-01T00:00:00Z
  checked: transfer-list-cycle.ts findSectionHeaderButton() — button search logic
  found: |
    Lines 63-75: Searches `.section-header-btn` buttons inside `.ut-transfer-list-view`.
    BUT selectors.ts shows:
      TL_CLEAR_SOLD = '.ut-transfer-list-view .section-header-btn'  (line 229)
      TL_RELIST_ALL_CLASS = 'btn-standard section-header-btn mini primary' (line 236)
    The function does NOT use these constants — it queries `.section-header-btn` directly
    without the "mini" qualifier, which is probably fine. But the real issue is:
    It scans for buttons that "contain" text "re-list"/"relist" or "clear sold".
    If the button text changes between EA updates (e.g. "Re-List All" vs "Re-list all"),
    the match could fail. But toLowerCase() handles casing, so text case is not the issue.
    The real problem: findSectionHeaderButton searches the ENTIRE transfer list container,
    not just a specific section. After scanning ALL pages (which may end on the last page),
    the Re-list All / Clear Sold buttons may only be visible on page 1, or EA may only
    show the buttons when the relevant section has items VISIBLE in the current view.
  implication: If scanning ends on a non-first page, the section header buttons may not be visible.
    goToFirstPage() is called before relist (line 185) which should help, but note:
    goToFirstPage() only navigates pagination — it does NOT re-navigate to the transfer list page.
    If the pagination UI differs from what PAGINATION_PREV expects, goToFirstPage() silently does nothing.

- timestamp: 2026-04-01T00:00:00Z
  checked: buy-cycle.ts executeBuyCycle() — retry loop structure (the Gwinn 3x bug)
  found: |
    BUG CONFIRMED: The buy attempt loop has a critical structural flaw.

    The outer loop at line 294: `for (let step = 0; step <= MAX_BIN_STEPS; step++)`
    The inner while loop at line 428: `while (retries < MAX_RETRIES)`

    `retries` is declared at line 290 OUTSIDE the outer for-loop.
    `retries` is NEVER RESET between outer loop iterations (price-discovery steps).

    Scenario for Gwinn 3x buy:
    Step 0 (maxBin=buy_price): Finds Gwinn, buys successfully (bought=true), breaks inner loop.
    → Returns { outcome: 'bought', buyPrice: actualBinPaid } at line 599. ✓ First buy.

    BUT WAIT — looking more carefully at the buy success path:
    Line 474: `const hasListAccordion = document.querySelector(SELECTORS.LIST_ON_MARKET_ACCORDION) !== null`
    Line 476-478: if hasListAccordion → bought=true, break
    Line 479: `attemptFailed = true` (if no accordion = buy failed silently)

    Then Step 5 lists the card. After listing, line 591:
    `const panelStillVisible = document.querySelector(SELECTORS.QUICK_LIST_PANEL) !== null`
    If panel is STILL visible (listing failed), returns error with "unassigned pile" message.

    The actual 3x buy mechanism: The outer for-loop (step) continues AFTER a successful buy+list
    only if somehow we reach `continue` or fall through the inner while without `bought=true`.
    Looking at lines 539-542:
    ```
    if (!bought) {
      return { outcome: 'skipped', reason: 'Sniped 3 times' };
    }
    ```
    This is INSIDE the outer for-loop body. After this check, execution reaches line 544 (Step 5: List).
    After listing, `return { outcome: 'bought', buyPrice: actualBinPaid }` at line 599.

    So the function SHOULD return after the first successful buy. The 3x buy cannot happen within
    a single executeBuyCycle() call... unless the listing step fails AND the outer loop continues.

    Wait — re-reading carefully. If listing fails with "unassigned pile", returns error at line 596.
    So executeBuyCycle() WOULD return. The outer loop in automation-loop.ts calls executeBuyCycle()
    once per player. It would not re-call it for the same player.

    UNLESS: The automation loop's reconciliation check is wrong. After buying Gwinn once,
    `alreadyListedNames` is built from the PREVIOUS cycle's transfer list scan (before buying).
    On the SAME iteration of the automation loop, when iterating through buyPlayers,
    `alreadyListedNames` is NOT updated after each buy. So if Gwinn appears multiple times
    in `actionsNeeded` (duplicate BUY actions from backend), the reconciliation won't prevent
    the second buy because `alreadyListedNames` was built before the cycle's buy phase.

    After buying Gwinn the first time, alreadyListedNames still does NOT contain "gwinn"
    (it was only built from the SCAN results, not from successful buys in the current cycle).
    So on the next iteration for the same player, isAlreadyListed = false → buys again.
  implication: |
    Root cause of 3x buy: alreadyListedNames is NOT updated after each successful buy
    within the same cycle. If the backend returns duplicate BUY actions for the same player
    (or if the same player appears once and gets bought, but the listing fails and returns
    to the buy loop), the reconciliation set doesn't prevent re-buying.

    Additionally: after a successful buy, the automation-loop sends a TRADE_REPORT 'listed'
    to the backend, but then continues the for-loop for the next player. If Gwinn is listed
    twice in actionsNeeded array, both iterations will buy. After the 3rd attempt, the
    inner retry counter (retries=3 already used up) returns 'skipped: Sniped 3 times'.

    Wait — retries is scoped to executeBuyCycle(), reset per call. So each call gets fresh retries.
    The 3x buy must be: Gwinn appears in actionsNeeded multiple times OR Gwinn is at index 0
    and gets bought, but the listing step fails (returns 'error'), causing automation-loop to
    increment consecutiveFailures and continue to the NEXT player — but that next player is
    also Gwinn somehow. OR: the buy succeeds but is reported as a different outcome.

    Most likely scenario: After buying Gwinn successfully once, the listing step fails (TL full,
    or quick list panel not found). The outcome returned is 'error' with "unassigned pile".
    The automation-loop hits `result.reason.includes('unassigned pile')` check, sets TL to full,
    and breaks. BUT if the listing error is something else (e.g. "List for Transfer button not found"),
    it returns `{ outcome: 'error', reason: '...' }` without the "unassigned pile" text,
    and the loop logs it and continues to next player. If Gwinn was bought but not listed (stuck
    in unassigned pile), and the loop continues and Gwinn is the next player too, it buys again.

- timestamp: 2026-04-01T00:00:00Z
  checked: automation-loop.ts — alreadyListedNames update after buy
  found: |
    Lines 319-323: On result.outcome === 'bought', the code does:
      consecutiveFailures = 0
      transferListCount++
      engine.setLastEvent(...)
      sends TRADE_REPORT 'bought' and 'listed'

    Nowhere in the bought branch does it do: `alreadyListedNames.add(player.name.toLowerCase())`

    This is the confirmed missing update. The reconciliation set is built once at the start
    of the cycle (lines 172-175) from the pre-cycle scan results, and never updated during buying.

    So: if actionsNeeded has the same player twice, or if the loop processes the same player
    after a failed listing, nothing prevents re-purchasing.
  implication: CONFIRMED BUG 3 — alreadyListedNames not updated after successful buy.

- timestamp: 2026-04-01T00:02:00Z
  checked: buy-cycle.ts lines 461-479 — buy success detection mechanism (re-investigation after user correction)
  found: |
    BUG 3 ROOT CAUSE CORRECTED.

    User confirmed: buy actually succeeds each time. The extension misdetects success as failure.

    The success detection path:
      Line 466: await clickElement(confirmBtn)   ← fires buy confirm
      Line 467: await jitter(1000, 2000)         ← waits 1-2 seconds
      Line 471: document.querySelector('button.accordian')  ← ONE-SHOT check, no polling
      Line 474: if (hasListAccordion) { bought = true; break; }
      Line 479: attemptFailed = true   ← if accordion not found

    THE FLAW: After clicking the buy confirm button, EA must transition through its
    post-buy screen rendering before button.accordian appears. This transition can take
    more than 2 seconds (jitter max). The code checks ONCE after a short wait, gets null
    (accordion not yet rendered), treats the successful buy as a failure.

    CONSEQUENCE:
      - attemptFailed = true → retries++
      - Code navigates BACK with NAV_BACK_BUTTON
      - This navigates away from the post-buy detail view, abandoning the bought card
        to the unassigned pile
      - Code re-searches → finds Gwinn on market (different copy) → buys again
      - Repeat until retries >= MAX_RETRIES (3) → returns 'skipped: Sniped 3 times'
      - automation-loop never reaches the 'bought' branch → never reports success

    Also identified: after clicking confirm, EA may show a second post-buy dialog
    ("Purchase Successful" or similar). If this appears, button.accordian won't show
    until the second dialog is dismissed. The original code had no handling for this.
  implication: |
    Root cause is insufficient wait for post-buy DOM transition. Fix: replace
    one-shot querySelector check with waitForElement polling (8s timeout) for
    button.accordian after clicking buy confirm. Also: add handling to dismiss
    any lingering/new dialog before polling for the accordion.

## Resolution

root_cause: |
  THREE root causes found:

  BUG 1 — Sold items not cleared:
  In trade-observer.ts, sold detection requires hasWonClass=true AND rawStatus='expired'.
  EA Web App shows sold items with class "won" and status text "Expired" — this is correct for
  items where SOMEONE ELSE bought your listed card (winning an auction). But if EA instead shows
  "Sold" as the status text directly (which is in STATUS_MAP), the STATUS_MAP path works.
  The real issue: the comment in selectors.ts says "Sold items have class 'won' on the .listFUTItem"
  but the actual DOM behavior may put the sold price in a different selector. Also, the BIN price
  selector `.auction .auctionValue:nth-child(3) .value` may return 0 for sold items (the auction
  is over — prices may have cleared). If price reads as 0, the item is still detected but may be
  mismatched in the automation-loop's name reconciliation.

  More critically: transfer-list-cycle.ts Step 6 (Clear Sold) calls findSectionHeaderButton('clear')
  which searches for `.section-header-btn` containing "clear sold". This function returns the button
  if found — but if scanning ended on a non-first page, the transfer list DOM may not show the
  "Sold Items" section header button (EA only shows it when sold items are visible in current view).
  goToFirstPage() is NOT called before the Clear Sold step — only before relist.

  BUG 2 — Expired items not relisted:
  Same structural issue: goToFirstPage() is called before relisting (correct), BUT the
  Re-list All button may not appear until you are on the section that shows expired items.
  More critically: after scanAllPages() navigates through pagination, the DOM state is on
  the LAST page. goToFirstPage() clicks PAGINATION_PREV repeatedly — but the selector
  `button.pagination.prev` may not exist or may be disabled if there's only one page,
  or the EA pagination structure differs from what's expected. If goToFirstPage() silently
  fails (no prev button exists on the last page view), the relist button search may fail
  because expired items are shown in a different section not visible in current view.

  BUG 3 — Bought Gwinn 3x (CORRECTED root cause):
  In buy-cycle.ts, the buy success detection uses a one-shot querySelector('button.accordian')
  check after jitter(1000, 2000). After clicking the buy confirm button, EA transitions through
  a post-buy screen render — button.accordian doesn't appear until that transition completes,
  which can take more than 2 seconds. The code checks once, gets null (accordion not yet
  rendered), treats the successful buy as a snipe failure. This causes the code to:
    1. Set attemptFailed = true → retries++
    2. Navigate BACK with NAV_BACK_BUTTON (abandoning the bought card to unassigned pile)
    3. Re-search, find a different copy of Gwinn, buy again
    4. Repeat until MAX_RETRIES (3) → return 'skipped: Sniped 3 times'
    5. automation-loop never sees 'bought' → never reports success → never stops trying

  Additionally, EA may show a second post-buy dialog after the confirm click. The original
  code had no handling to dismiss it before checking for the accordion.

fix: |
  FIX 1 (Bugs 1+2) — Added goToFirstPage() before the Clear Sold button search in
  transfer-list-cycle.ts Step 6. After scanAllPages() paginates to the last page,
  the DOM is left on that page. goToFirstPage() was already called before relist (Step 4)
  but was missing before clear (Step 6). Now both steps navigate to page 1 first.

  FIX 2 (automation-loop.ts) — Added `alreadyListedNames.add(player.name.toLowerCase())`
  in automation-loop.ts at the 'bought' outcome branch. Still correct and necessary: once
  the success detection is fixed, the 'bought' branch will be reached, and this prevents
  a second buy attempt for the same player within the same cycle.

  FIX 3 (buy-cycle.ts — ROOT CAUSE of Bug 3) — Replaced one-shot querySelector + jitter(1000,2000)
  with:
    1. jitter(500,1000) initial wait
    2. Check if EA dialog is still present (possible second post-buy dialog) and dismiss it
    3. waitForElement polling for 'button.accordian' up to 8 seconds
  If accordion appears within 8s → bought = true. If not → attemptFailed = true (true failure).

verification: code trace confirms waitForElement polls every 200ms for up to 8s; previous one-shot with 1-2s was insufficient for EA post-buy DOM transition
files_changed:
  - extension/src/transfer-list-cycle.ts
  - extension/src/automation-loop.ts
  - extension/src/buy-cycle.ts
