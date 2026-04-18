---
status: awaiting_human_verify
trigger: "list-sellstart-equals-sellbin"
created: 2026-04-18T00:00:00Z
updated: 2026-04-18T00:10:00Z
---

## Current Focus

hypothesis: CONFIRMED — sellStart passed raw sell_price to getBeforeStepValue instead of the already-snapped sellBin
test: traced arithmetic with sell_price=201150 through both functions
expecting: fix applied, tests pass
next_action: human verification in live extension

## Symptoms

expected: sellStart = one EA price tier below sellBin; sellBin = snapped sell_price; listing succeeds
actual: sellBin == sellStart (both 201000), sell_price=201150 slightly above; EA returns 400 for all 4 cards
errors: "Failed to list defId=xxxx (error 400, sellBin=201000, sellStart=201000, sell_price=201150)"
reproduction: Run quick list automation on any card whose computed sell_price is NOT already on a tier boundary
started: 2026-04-18, new regression (but likely a latent bug introduced when these call sites were written)

## Eliminated

- hypothesis: getBeforeStepValue function itself is broken
  evidence: all 29 ea-services tests pass; function correctly floors to nearest step below input when input is on a tier boundary
  timestamp: 2026-04-18

- hypothesis: PRICE_TIERS table is wrong
  evidence: tiers match EA's fixed steps exactly; the 100k tier has inc=1000 which is correct for 201000
  timestamp: 2026-04-18

## Evidence

- timestamp: 2026-04-18
  checked: buy-cycle.ts lines 164-165 and automation-loop.ts lines 114-115
  found: both call `roundToNearestStep(getBeforeStepValue(raw_sell_price))` for sellStart, using the raw (unsnapped) sell_price
  implication: when sell_price=201150 (not on a 1000-step boundary), getBeforeStepValue floors to 201000 — same as sellBin — so sellStart == sellBin

- timestamp: 2026-04-18
  checked: arithmetic trace with sell_price=201150
  found: sellBin = round(201150/1000)*1000 = 201000; getBeforeStepValue(201150) = floor(201150/1000)*1000 = 201000 (rounded != price so not the equal-branch); sellStart = round(201000/1000)*1000 = 201000
  implication: sellStart and sellBin are identical → EA rejects with 400

- timestamp: 2026-04-18
  checked: algo-sell-cycle.ts lines 193-194 and algo-transfer-list-sweep.ts lines 219,224
  found: these paths correctly call getBeforeStepValue(listBin) for listStart — using the already-snapped value as input
  implication: the algo paths are correct; only buy-cycle.ts and automation-loop.ts were broken

## Resolution

root_cause: In buy-cycle.ts and automation-loop.ts, sellStart was computed as roundToNearestStep(getBeforeStepValue(raw_sell_price)). When sell_price is not on a tier boundary (e.g. 201150), getBeforeStepValue floors it to the same tier step as sellBin (201000), producing sellStart == sellBin. EA requires sellStart < sellBin and rejects with 400.

fix: Changed both call sites to pass the already-snapped sellBin into getBeforeStepValue: `sellStart = roundToNearestStep(getBeforeStepValue(sellBin))`. For sell_price=201150: sellBin=201000, getBeforeStepValue(201000)=199000... wait — no. sellBin is on a boundary. getBeforeStepValue(201000): rounded=floor(201000/1000)*1000=201000, rounded===price so prev=200999, roundToNearestStep(200999,true)=floor(200999/1000)*1000=200000. sellStart=200000. Now sellStart(200000) < sellBin(201000) ✓

verification: all 29 ea-services unit tests pass; the one pre-existing content.test.ts failure (PORTFOLIO_CARD_TYPES_REQUEST) is unrelated to this change
files_changed: [extension/src/buy-cycle.ts, extension/src/automation-loop.ts]
