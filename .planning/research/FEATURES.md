# Feature Landscape

**Domain:** FC/FUT Ultimate Team OP sell automation platform
**Researched:** 2026-03-25

---

## Context: What OP Selling Is

OP (overpriced) selling is a coin-making strategy where you buy a large quantity of a single player card at market price and relist them above the current BIN. A fraction sell to lazy buyers or during SBC demand spikes. The cycle repeats: sold card gets repurchased and relisted. This project automates the discovery of which players are currently OP-sellable (via price-at-time scoring) and eventually the mechanical buy/relist cycle itself.

---

## Table Stakes

Features users expect. Missing = the platform feels broken or requires too much manual effort to be worth using.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Fresh player rankings | Core value prop — stale scores are useless for a time-sensitive market | Med | Hourly scan cadence already decided; this is the API + scheduler work |
| Score per player (margin, op_ratio, expected profit, efficiency) | Users need to understand *why* a player is ranked — raw rank without explanation loses trust | Low | Scoring logic already exists; just needs to be surfaced via API |
| Budget-aware portfolio output | Users need a list they can actually execute with their coin balance, not a global leaderboard | Med | Portfolio optimizer already exists; needs to pull from DB |
| Per-player detail: sales history, margin breakdown | Users validate recommendations before buying 100 cards; they need to inspect the evidence | Med | fut.gg already returns this; needs to be stored and queryable |
| Auto-relist expired listings | The buy/relist cycle is manual today; every tool in the ecosystem automates this step | Med | Chrome extension DOM automation on EA Web App transfer list |
| Buy automation from recommendation list | Extension must be able to buy players from the server's list, not require manual copying | High | Extension must receive target players + prices from backend API |
| Profit tracking per session | Users need to know if the strategy is working; P&L per card, per session is the minimum | Med | Requires recording buy price, sell price per transaction |
| Transfer list status visibility | Is my list full? How many sold? How many expired? Critical for managing the cycle | Low | Extension reads DOM state and surfaces it in popup |
| Configurable budget input | Different users run different budgets; the scorer must adapt filters accordingly | Low | Already exists in CLI; needs to move to API parameter |
| Rate-limit-safe 24/7 scanning | If the scanner gets blocked by fut.gg, all scores go stale — reliability is non-negotiable | High | Requires throttling, backoff, scheduling logic |

---

## Differentiators

Features that set this platform apart from generic sniper bots. Not expected, but high value for an OP sell specialist tool.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Price-at-time OP verification | Generic tools detect OP using current BIN — they produce false positives when price dropped after sale. This platform verifies each sale against the market price *at sale time*. | Med | Already implemented in scorer; key accuracy differentiator |
| Minimum 3 OP sales threshold | Filters out one-off lucky sells; users get high-confidence candidates only | Low | Already implemented; needs to be surfaced as visible filter in UI |
| Efficiency-sorted ranking (profit/buy_price) | Competes better than naive "biggest expected profit" ranking — favors cheap cards with good ratios, fills 60-70 slots instead of 14 | Low | Already implemented; just needs to be explained in UI |
| Historical score tracking | Did this player OP-sell well last week? Score trends reveal seasonal/SBC demand spikes before they're obvious | High | Requires storing per-player scores over time in DB |
| Market momentum alerts | Notify when a player's OP score jumps significantly — early signal of SBC demand or supply crash | High | Requires delta detection between scan cycles + notification delivery |
| Automated buy-from-list with target price enforcement | Extension buys only at or below the scorer's recommended buy price, not just any BIN | Med | Prevents overpaying when market moves between recommendation and execution |
| Session profit dashboard | Running total of coins gained this session, ROI %, cards sold vs listed — makes performance tangible | Med | Web dashboard backed by transaction log in DB |
| Score confidence indicator | Show users how many data points back the score (more sales = more confidence) | Low | Derived from existing sale count; requires display logic only |
| Player filter presets | Save a filter config ("only gold rares 15k-50k") for quick session starts | Low | UI convenience; server-side saved preferences |
| Scan coverage indicator | Show what % of the 11k-200k pool has been scored in the last N hours — transparency builds trust | Low | Derived from DB scan timestamps |

---

## Anti-Features

Features to explicitly NOT build. Each has a principled reason.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| Sniping / buying underpriced cards | Different strategy entirely (requires sub-second reaction), different risk profile, different user skill requirement. Scope creep. | Stay laser-focused on OP sell; sniping users have 10 other tools |
| Mass bidding automation | High EA detection surface, fundamentally different strategy, not complementary to OP sell flow | If users want autobidding they use FUT Trade Buddy or UT Web Helper |
| SBC solver | Useful feature but zero relation to OP sell profitability; adds a massive scope blob | Reference existing tools (Futrich, UT Web Helper) in docs if asked |
| Multi-account management | Each EA account has its own transfer list; multi-account operation dramatically increases ban risk and complexity | Single-account focus; document this explicitly |
| In-game console automation | EA Web App is accessible via browser; console overlays require platform-specific injection that breaks ToS more aggressively | Chrome extension on EA Web App only, as already decided |
| FUTBIN integration | Already removed once; adds rate limiting fragility and a second data dependency for no accuracy gain | fut.gg provides all required data |
| Price manipulation detection / prevention | Trying to detect if you're inflating a market is speculative and legally grey; not needed for personal tool | Scoring naturally avoids illiquid cards via 7 sales/hr minimum filter |
| Social/community trading signals | Discord tip-sharing, community filters, etc. add moderation overhead and change the product from "data-driven" to "social trust" | Keep recommendations algorithmic; document methodology instead |
| Mobile app | Not where EA Web App lives; adds platform split and maintenance overhead | Web dashboard accessible from mobile browser is sufficient |
| "Guaranteed profit" framing | OP selling has a probabilistic success rate; claiming guarantees is misleading and creates support burden | Surface op_ratio and expected_profit so users understand it's probabilistic |

---

## Feature Dependencies

The dependency graph determines build order.

```
Persistent DB (scan results, player scores, transactions)
    └── REST API (serve scores, receive transaction records)
            ├── Updated CLI thin client (queries API instead of scoring live)
            ├── Chrome extension backend calls (buy targets, relist triggers)
            │       ├── Auto-buy from recommendation list
            │       ├── Auto-relist expired listings
            │       └── Transfer list status visibility
            └── Web dashboard
                    ├── Profit tracking per session
                    ├── Session profit dashboard
                    └── Historical score tracking
                            └── Market momentum alerts
```

Additional dependency notes:

- **Hourly scanner** must exist before any score freshness guarantee can be made to users.
- **Transaction logging** (buy price + sell price per card) is a prerequisite for every profit tracking feature.
- **Price-at-time verification** is already implemented — it is a dependency that's already satisfied.
- **Market momentum alerts** require at least two time-series data points per player — score history must accumulate for some period before this feature can fire.

---

## MVP Recommendation

Prioritize for the first usable milestone (personal use):

1. **Persistent backend with hourly scanner** — Without this, nothing else runs continuously. Core of the platform.
2. **REST API serving top OP players filtered by budget** — Replaces the CLI as the data access layer.
3. **Chrome extension: auto-relist expired listings** — Lowest-risk automation. No buy logic, just relist. Immediate daily time saving.
4. **Chrome extension: buy from recommendation list** — Closes the loop between scoring and execution.
5. **Transaction log + simple P&L display** — Validates the strategy is working; motivates continued use.

Defer to subsequent milestones:

- **Historical score tracking** — Valuable but requires data accumulation time; not useful day one.
- **Market momentum alerts** — Depends on historical data; defer until score history exists.
- **Web dashboard** — CLI + extension popup covers personal use initially; dashboard is a paid-product-tier concern.
- **Player filter presets / saved configs** — Nice UX but not blocking core loop.

---

## Sources

- [FUT Simple Trader — Features & Pricing](https://futsimpletrader.com/services/ea-fc-sniping-bot-and-autobuyer)
- [UT Web Helper — Auto Relist, Sniper, SBC](https://utwebhelper.com/)
- [FutStarz — Profit Tracking Dashboard](https://www.futstarz.com/en)
- [Levelled Up Gaming — Overpriced Selling Guide](https://www.levelledupgaming.com/overpriced-selling-guide/)
- [Levelled Up Gaming — OP Selling Method](https://www.levelledupgaming.com/fifa-23-op-selling-easy-coins/)
- [FUT Trade Buddy on Chrome Web Store](https://chromewebstore.google.com/detail/fut-trade-buddy-autobuyer/egcilaiennocopjhedfpacbmicepoopo)
- [EasyFUT GitHub — Transfer Market Automation](https://github.com/Kava4/EasyFUT)
- [FUTBotManager — EA Ban Wave Avoidance](https://futbotmanager.com/ea-ban-wave-avoidance-futbotmanager/)
- [FutEarn — FC26 Autobuyer](https://futearn.com)
- [FUTEarn Chrome Web Store listing](https://chromewebstore.google.com/detail/ea-fc-26-sniping-bot-auto/ekmgafjpdinnpmmpfdhhlpnkghahfpif)

All claims marked LOW confidence unless backed by official product pages or multiple independent sources. OP sell strategy mechanics (HIGH confidence — multiple consistent community sources). Ban risk claims (MEDIUM confidence — industry consensus but EA enforcement is non-deterministic).
