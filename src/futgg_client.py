"""
fut.gg API client.

Uses fut.gg's internal JSON API endpoints to fetch player data, prices,
sales history, and live listings. No scraping needed — direct HTTP calls.

Discovered endpoints:
  - /api/fut/player-prices/26/{eaId}/         → prices, sales, listings, history
  - /api/fut/player-prices/26/?ids=X,Y,Z      → batch current prices
  - /api/fut/player-item-definitions/26/{eaId}/ → player card definition
  - /api/fut/metarank/players/?ids=X,Y,Z       → meta rankings
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from src.config import (
    FUTGG_BASE_URL,
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
)
from src.models import Player, PlayerMarketData, PricePoint, SaleRecord

logger = logging.getLogger(__name__)

# Position ID to string mapping (from fut.gg data)
POSITION_MAP = {
    0: "GK", 1: "RWB", 2: "RB", 3: "CB", 4: "LB", 5: "LWB",
    6: "RDM", 7: "CDM", 8: "LDM", 9: "RM", 10: "CM", 11: "LM",
    12: "RAM", 13: "CAM", 14: "LAM", 15: "RF", 16: "CF", 17: "LF",
    18: "RW", 19: "ST", 20: "LW",
}


class FutGGClient:
    """HTTP client for fut.gg's internal API."""

    def __init__(self):
        self.base_url = FUTGG_BASE_URL
        self.client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        """Create the HTTP client."""
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Referer": f"{self.base_url}/players/",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        logger.info("FutGG client started")

    async def stop(self) -> None:
        """Close the HTTP client."""
        if self.client:
            await self.client.aclose()
        logger.info("FutGG client stopped")

    async def _get(self, path: str) -> Optional[dict]:
        """Make a GET request with minimal delay."""
        try:
            resp = await self.client.get(path)
            resp.raise_for_status()
            await asyncio.sleep(0.15)  # light delay to be polite
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP {e.response.status_code} for {path}")
            return None
        except Exception as e:
            logger.error(f"Request failed for {path}: {e}")
            return None

    # ── Batch endpoints ──────────────────────────────────────────────

    async def get_batch_prices(self, ea_ids: list[int]) -> list[dict]:
        """
        Fetch current prices for multiple players at once.
        Returns list of {eaId, price, isExtinct, ...}.
        """
        if not ea_ids:
            return []

        ids_str = ",".join(str(i) for i in ea_ids)
        data = await self._get(f"/api/fut/player-prices/26/?ids={ids_str}")
        if data and "data" in data:
            return data["data"]
        return []

    async def get_batch_metaranks(self, ea_ids: list[int]) -> dict[int, float]:
        """
        Fetch meta ranking scores for multiple players.
        Returns {eaId: score}.
        """
        if not ea_ids:
            return {}

        ids_str = ",".join(str(i) for i in ea_ids)
        data = await self._get(f"/api/fut/metarank/players/?ids={ids_str}")
        if data and "data" in data:
            return {item["eaId"]: item.get("score", 0) for item in data["data"]}
        return {}

    # ── Individual player endpoints ──────────────────────────────────

    async def get_player_prices(self, ea_id: int) -> Optional[dict]:
        """
        Fetch full price data for a single player card.

        Returns dict with:
          - currentPrice: {price, isExtinct, priceUpdatedAt, ...}
          - completedAuctions: [{soldDate, soldPrice}, ...]
          - liveAuctions: [{buyNowPrice, endDate, startingBid}, ...]
          - history: [{date, price}, ...]  (daily/hourly price chart)
          - momentum: {lowestBin, highestBin, currentBinMomentum, lastUpdates}
          - overview: {averageBin, cheapestSale, discardValue, yesterdayAverageBin}
          - priceRange: {minPrice, maxPrice}
        """
        data = await self._get(f"/api/fut/player-prices/26/{ea_id}/")
        if data and "data" in data:
            return data["data"]
        return None

    async def get_player_definition(self, ea_id: int) -> Optional[dict]:
        """
        Fetch full card definition for a player.

        Returns dict with: firstName, lastName, overall, position,
        club, league, nation, rarity, face stats, attributes, etc.
        """
        data = await self._get(f"/api/fut/player-item-definitions/26/{ea_id}/")
        if data and "data" in data:
            return data["data"]
        return None

    async def get_player_other_versions(self, ea_id: int) -> list[dict]:
        """Get all card versions for a player."""
        data = await self._get(
            f"/api/fut/player-item-definitions/26/{ea_id}/other-versions/"
        )
        if data and "data" in data:
            return data["data"]
        return []

    # ── High-level data assembly ─────────────────────────────────────

    async def get_player_market_data(self, ea_id: int) -> Optional[PlayerMarketData]:
        """
        Assemble full market data for a single player card.

        Fetches both card definition and price data, then combines
        into a PlayerMarketData object.
        """
        # Fetch definition and prices in parallel
        defn, prices = await asyncio.gather(
            self.get_player_definition(ea_id),
            self.get_player_prices(ea_id),
        )

        if not defn or not prices:
            logger.warning(f"Missing data for ea_id={ea_id}")
            return None

        # Build Player model
        player = self._build_player(defn)

        # Current price
        current_price = prices.get("currentPrice", {}) or {}
        current_bin = current_price.get("price", 0) or 0

        # Live listings count
        live_auctions = prices.get("liveAuctions", [])
        listing_count = len(live_auctions)

        # Actual lowest BIN from live listings + estimate OP listings
        op_listing_count = 1
        if live_auctions:
            actual_lowest_bin = min(a["buyNowPrice"] for a in live_auctions)
            current_bin = actual_lowest_bin

            # The API only shows ~16 listings, but there are many more on the market.
            # Estimate OP listing count:
            # - Count what % of visible listings are OP-priced
            # - Scale up by total listing count (from the visible sample size ratio)
            op_threshold = actual_lowest_bin * 1.03  # 3%+ above floor = OP
            visible_op = sum(1 for a in live_auctions if a["buyNowPrice"] >= op_threshold)
            visible_total = len(live_auctions)

            if visible_total > 0:
                op_ratio = visible_op / visible_total
                # The real market has many more listings than the ~16 we see.
                # Conservative estimate: at least 5x what's visible for popular cards,
                # use the ratio to scale. Minimum of visible_op + 1 (us).
                estimated_total_listings = max(visible_total * 5, visible_total)
                op_listing_count = max(int(estimated_total_listings * op_ratio), 1)
            else:
                op_listing_count = 1

        # Price history
        price_history = []
        for point in prices.get("history", []):
            try:
                price_history.append(PricePoint(
                    resource_id=ea_id,
                    recorded_at=datetime.fromisoformat(
                        point["date"].replace("Z", "+00:00")
                    ),
                    lowest_bin=point["price"],
                ))
            except Exception:
                continue

        # Completed sales
        sales = []
        overview = prices.get("overview", {})
        for auction in prices.get("completedAuctions", []):
            try:
                sales.append(SaleRecord(
                    resource_id=ea_id,
                    sold_at=datetime.fromisoformat(
                        auction["soldDate"].replace("Z", "+00:00")
                    ),
                    sold_price=auction["soldPrice"],
                    # Use current BIN as approximation of floor at time of sale
                    # The HOSS scorer will use price history to get more accurate floors
                    lowest_bin_at_time=current_bin,
                ))
            except Exception:
                continue

        # Ensure we have a valid price
        if not current_bin or current_bin <= 0:
            # Try getting from overview or history
            overview = prices.get("overview", {}) or {}
            current_bin = overview.get("averageBin", 0) or 0
            if not current_bin and price_history:
                current_bin = price_history[-1].lowest_bin

        if not current_bin or current_bin <= 0:
            return None

        # Extract base player info for FUTBIN mapping
        base_ea_id = defn.get("basePlayerEaId", 0) or 0
        base_slug = defn.get("basePlayerSlug", "") or ""

        # Store all live auction BIN prices and end times
        live_prices = [a["buyNowPrice"] for a in live_auctions]
        live_end_times = [a.get("endDate", "") for a in live_auctions]

        return PlayerMarketData(
            player=player,
            current_lowest_bin=int(current_bin),
            listing_count=listing_count,
            op_listing_count=max(op_listing_count, 1),
            price_history=price_history,
            sales=sales,
            live_auction_prices=live_prices,
            live_auction_end_times=live_end_times,
            base_player_ea_id=base_ea_id,
            base_player_slug=base_slug,
        )

    async def get_batch_market_data(
        self, ea_ids: list[int], concurrency: int = 5,
    ) -> list[Optional[PlayerMarketData]]:
        """
        Fetch market data for multiple players concurrently.

        Uses a semaphore to limit concurrent requests.
        """
        sem = asyncio.Semaphore(concurrency)

        async def fetch_one(ea_id: int) -> Optional[PlayerMarketData]:
            async with sem:
                return await self.get_player_market_data(ea_id)

        return await asyncio.gather(*[fetch_one(eid) for eid in ea_ids])

    def _build_player(self, defn: dict) -> Player:
        """Build a Player model from a card definition response."""
        position_id = defn.get("position", 0)
        position_str = POSITION_MAP.get(position_id, str(position_id))

        rarity = defn.get("rarity", {})
        card_type = rarity.get("slug", "gold") if isinstance(rarity, dict) else "gold"

        club = defn.get("club", {})
        league = defn.get("league", {})
        nation = defn.get("nation", {})

        return Player(
            resource_id=defn.get("eaId", 0),
            name=defn.get("commonName") or f"{defn.get('firstName', '')} {defn.get('lastName', '')}".strip(),
            rating=defn.get("overall", 0),
            position=position_str,
            nation=nation.get("name", "") if isinstance(nation, dict) else "",
            league=league.get("name", "") if isinstance(league, dict) else "",
            club=club.get("name", "") if isinstance(club, dict) else "",
            card_type=card_type,
            pace=defn.get("facePace", 0),
            shooting=defn.get("faceShooting", 0),
            passing=defn.get("facePassing", 0),
            dribbling=defn.get("faceDribbling", 0),
            defending=defn.get("faceDefending", 0),
            physical=defn.get("facePhysicality", 0),
        )

    # ── Player discovery ─────────────────────────────────────────────

    async def get_player_list_page(
        self, page: int = 1,
        min_price: int = 0, max_price: int = 0,
    ) -> Optional[dict]:
        """
        Fetch a page of players from the trending listing API.

        Uses the trending endpoint which supports price filtering:
          price__gte=min, price__lte=max
        """
        url = f"/api/fut/players/v2/26/?page={page}"
        params = []
        if min_price > 0:
            params.append(f"price__gte={min_price}")
        if max_price > 0:
            params.append(f"price__lte={max_price}")
        if params:
            url += "&" + "&".join(params)
        return await self._get(url)

    async def discover_players(
        self, budget: int, max_pages: int = 999,
        min_price: int = 0, max_price: int = 0,
    ) -> list[dict]:
        """
        Discover tradeable player cards within budget.

        Uses price-filtered API to only fetch relevant players.
        max_price defaults to 10% of budget (no single card > 10% of budget).
        min_price defaults to 1000 (ignore discard-value cards).
        """
        if max_price <= 0:
            max_price = int(budget * 0.10)
        if min_price <= 0:
            min_price = 1000

        all_candidates = []
        seen_ids = set()

        for page_num in range(1, max_pages + 1):
            logger.info(f"Fetching player list page {page_num}...")
            result = await self.get_player_list_page(
                page_num, min_price=min_price, max_price=max_price,
            )

            if not result or "data" not in result:
                break

            players = result["data"]
            if not players:
                break

            # Extract eaIds for batch price lookup
            ea_ids = []
            player_map = {}
            for p in players:
                slug = p.get("slug", "")
                ea_id_str = slug.split("-", 1)[-1] if "-" in slug else ""
                try:
                    ea_id = int(ea_id_str)
                except ValueError:
                    continue

                if ea_id in seen_ids:
                    continue
                seen_ids.add(ea_id)
                ea_ids.append(ea_id)
                player_map[ea_id] = p

            # Batch fetch prices
            if ea_ids:
                prices = await self.get_batch_prices(ea_ids)
                price_map = {
                    p["eaId"]: p["price"]
                    for p in prices
                    if p.get("price") is not None
                }

                for ea_id, player_data in player_map.items():
                    price = price_map.get(ea_id, 0) or 0
                    if min_price <= price <= max_price:
                        player_data["ea_id"] = ea_id
                        player_data["price"] = price
                        all_candidates.append(player_data)

            logger.info(
                f"Page {page_num}: {len(players)} players, "
                f"{len(all_candidates)} candidates within budget so far"
            )

            # Stop if no next page
            next_page = result.get("next")
            if not next_page:
                break

        logger.info(f"Discovery complete: {len(all_candidates)} candidates")
        return all_candidates
