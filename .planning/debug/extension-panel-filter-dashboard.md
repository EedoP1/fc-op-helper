---
status: awaiting_human_verify
trigger: "Three extension panel UI bugs: (1) filtering appends results instead of replacing the list, (2) dashboard has no filtering like portfolio, (3) dashboard players aren't clickable to fut.gg links."
created: 2026-03-28T00:00:00Z
updated: 2026-03-28T00:02:00Z
---

## Current Focus

hypothesis: The EA SPA (https://www.ea.com/...) has a global document-level click listener that intercepts <a> tag clicks to handle SPA routing, calling preventDefault() before the browser can open a new tab. The nameLink's target="_blank" never fires because the EA app swallows the click event.
test: Replace the passive <a target="_blank"> approach with an explicit click handler that calls e.preventDefault() + e.stopImmediatePropagation() to prevent the EA SPA from interfering, then opens the URL via window.open() which cannot be suppressed by page-level handlers.
expecting: After fix, clicking a player name in the dashboard opens the fut.gg search page in a new tab regardless of the EA SPA's click interception.
next_action: Await user verification — rebuild extension and test clicking a player name

## Symptoms

expected:
1. Filtering should replace the current list with filtered results in correct order
2. Dashboard should have the same filtering capabilities as the portfolio view
3. Player names/rows in the dashboard should be clickable links to their fut.gg page

actual:
1. Filtering appends filtered results to the end of the existing list instead of replacing
2. Dashboard view has no filter UI at all
3. Player entries in dashboard are plain text, not clickable

errors: No error messages reported — these are functional/UX bugs

reproduction:
1. Generate a list, then apply a filter — results append instead of replacing
2. Open dashboard view — no filter controls exist
3. Look at player names in dashboard — they're not links

started: Current state of the extension

## Eliminated

- hypothesis: Bug is in renderDraft
  evidence: renderDraft uses listEl correctly — listEl.innerHTML = '' before appending rows. Not the source of bug 1.
  timestamp: 2026-03-28T00:00:00Z

## Evidence

- timestamp: 2026-03-28T00:00:00Z
  checked: renderPortfolioContent (lines 661-732)
  found: renderPlayerList() clears listEl.innerHTML then re-renders rows BUT appends rows to `parent` (line 718) instead of `listEl`. So on re-sort/filter, new rows go to the bottom of `parent` while `listEl` is inside `parent`. This causes apparent duplication/append behavior.
  implication: Bug 1 root cause — rows should append to `listEl`, not `parent`.

- timestamp: 2026-03-28T00:00:00Z
  checked: renderDashboardContent (lines 218-308)
  found: No search/filter input exists in renderDashboardContent. The portfolio view has sort buttons via renderSortBar but the dashboard has none. Dashboard has its own player type (DashboardPlayer) with different fields (status, times_sold, realized_profit) so sorting needs a different implementation.
  implication: Bug 2 root cause — need to add filter/sort UI to renderDashboardContent.

- timestamp: 2026-03-28T00:00:00Z
  checked: renderDashboardContent player rendering (lines 278-280)
  found: nameEl is created as a plain `<span>` element with textContent set to player.name. DashboardPlayer type has ea_id field and the backend has futgg_url via PortfolioPlayer but DashboardPlayer in messages.ts does NOT include futgg_url. Need to construct a search URL from name like the portfolio does.
  implication: Bug 3 root cause — need to convert nameEl span to an anchor tag linking to fut.gg search by name.

- timestamp: 2026-03-28T01:00:00Z
  checked: renderDashboardContent — user requested sort buttons like portfolio
  found: DashboardPlayer fields available for sorting: name, buy_price, sell_price, times_sold, realized_profit, unrealized_pnl. Added local dashSortKey/dashSortDir state, renderDashSortBar() helper, and sort step before row rendering. TypeScript noEmit check passed.
  implication: Dashboard now has 6-column sort bar with active state highlighting and arrow direction indicator, matching portfolio UX pattern.

- timestamp: 2026-03-28T02:00:00Z
  checked: All three nameLink usages in panel.ts (dashboard ~line 421, portfolio ~line 694, draft ~line 852)
  found: All three have href + target="_blank" set correctly but rely on browser default navigation. No preventDefault/stopPropagation is present. EA Web App runs at https://www.ea.com/ea-sports-fc/ultimate-team/web-app/ and is an SPA that attaches document-level click listeners to handle its own routing — these intercept <a> clicks before the browser default (new tab) fires.
  implication: The fix is to add an explicit click listener on each nameLink that calls e.preventDefault() + e.stopImmediatePropagation() (to prevent the EA SPA from seeing the event) and then calls window.open(url, '_blank', 'noopener') directly. This bypasses the SPA routing interception entirely.

## Resolution

root_cause:
  bug1: In renderPortfolioContent, the inner renderPlayerList() function appends rows to `parent` (line 718) instead of `listEl`. When re-sorting, listEl.innerHTML is cleared but the old rows already appended to `parent` remain, and new rows are appended again to `parent` — appearing to "stack up" below listEl.
  bug2: renderDashboardContent has no filter/sort controls. The dashboard player list is rendered in arrival order with no interactivity.
  bug3: In renderDashboardContent, player name is rendered as a plain <span> instead of an <a> tag linking to the fut.gg search page.

fix: Patch panel.ts — five targeted changes:
  1. Change `parent.appendChild(row)` to `listEl.appendChild(row)` in renderPortfolioContent's renderPlayerList inner function
  2. Add a text search filter input + status filter dropdown above the player list in renderDashboardContent, with local filter state and a re-render helper
  3. Replace nameEl span with an anchor tag in renderDashboardContent player rows
  4. Add sort bar to renderDashboardContent (Name/Buy/Sell/Times Sold/Profit/Unreal. buttons) with local dashSortKey/dashSortDir state, active column highlighting, and direction arrow indicator; sort applied to filtered array before rendering
  5. (Bug 3 root fix) Add explicit click handlers to all three nameLink usages (dashboard, portfolio, draft) that call e.preventDefault() + e.stopImmediatePropagation() then window.open(url, '_blank', 'noopener') — bypasses EA SPA document-level click interception

verification: TypeScript noEmit check passed — zero compiler errors. Awaiting user confirmation in browser.
files_changed:
  - extension/src/overlay/panel.ts
