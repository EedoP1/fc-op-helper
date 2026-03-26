"""
fut.gg API client.

Uses fut.gg's internal JSON API endpoints to fetch player data, prices,
sales history, and live listings. No scraping needed — direct HTTP calls.

Endpoints:
  - /api/fut/players/v2/26/          → paginated player list (price filterable)
  - /api/fut/player-prices/26/{eaId}/ → prices, sales, listings, history
  - /api/fut/player-item-definitions/26/{eaId}/ → card definition
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from src.models import Player, PlayerMarketData, PricePoint, SaleRecord

logger = logging.getLogger(__name__)

POSITION_MAP = {
    0: "GK", 1: "RWB", 2: "RB", 3: "CB", 4: "LB", 5: "LWB",
    6: "RDM", 7: "CDM", 8: "LDM", 9: "RM", 10: "CM", 11: "LM",
    12: "RAM", 13: "CAM", 14: "LAM", 15: "RF", 16: "CF", 17: "LF",
    18: "RW", 19: "ST", 20: "LW",
}


class FutGGClient:
    """HTTP client for fut.gg's internal API."""

    BASE_URL = "https://www.fut.gg"

    def __init__(self):
        self.client: Optional[httpx.AsyncClient] = None

    async def start(self) -> None:
        """Create the HTTP client."""
        self.client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
                "Referer": f"{self.BASE_URL}/players/",
            },
            timeout=30,
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
        if not self.client:
            raise RuntimeError("Client not started. Call start() first.")
        try:
            resp = await self.client.get(path)
            resp.raise_for_status()
            await asyncio.sleep(0.05)
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP {e.response.status_code} for {path}")
            return None
        except Exception as e:
            logger.error(f"Request failed for {path}: {e}")
            return None

    # ── API endpoints ──────────────────────────────────────────────

    async def get_batch_prices(self, ea_ids: list[int]) -> list[dict]:
        """Fetch current prices for multiple players at once."""
        if not ea_ids:
            return []
        ids_str = ",".join(str(i) for i in ea_ids)
        data = await self._get(f"/api/fut/player-prices/26/?ids={ids_str}")
        return data["data"] if data and "data" in data else []

    async def get_player_prices(self, ea_id: int) -> Optional[dict]:
        """Fetch full price data for a single player card."""
        data = await self._get(f"/api/fut/player-prices/26/{ea_id}/")
        return data["data"] if data and "data" in data else None

    async def get_player_definition(self, ea_id: int) -> Optional[dict]:
        """Fetch full card definition for a player."""
        data = await self._get(f"/api/fut/player-item-definitions/26/{ea_id}/")
        return data["data"] if data and "data" in data else None

    # ── Data assembly ──────────────────────────────────────────────

    async def get_player_market_data(self, ea_id: int) -> Optional[PlayerMarketData]:
        """Fetch and assemble full market data for a single player card."""
        defn, prices = await asyncio.gather(
            self.get_player_definition(ea_id),
            self.get_player_prices(ea_id),
        )
        if not defn or not prices:
            return None

        player = self._build_player(defn)
        current_bin = self._extract_current_bin(prices)
        if not current_bin:
            return None

        raw_auctions = prices.get("liveAuctions", [])
        return PlayerMarketData(
            player=player,
            current_lowest_bin=current_bin,
            listing_count=len(raw_auctions),
            price_history=self._parse_price_history(ea_id, prices),
            sales=self._parse_sales(ea_id, prices),
            live_auction_prices=[a["buyNowPrice"] for a in raw_auctions],
            live_auctions_raw=raw_auctions,  # preserve all fields for fingerprinting (D-04)
        )

    async def get_batch_market_data(
        self, ea_ids: list[int], concurrency: int = 5,
    ) -> list[Optional[PlayerMarketData]]:
        """Fetch market data for multiple players concurrently."""
        sem = asyncio.Semaphore(concurrency)

        async def fetch_one(ea_id: int) -> Optional[PlayerMarketData]:
            async with sem:
                return await self.get_player_market_data(ea_id)

        return await asyncio.gather(*[fetch_one(eid) for eid in ea_ids])

    # ── Discovery ──────────────────────────────────────────────────

    async def discover_players(
        self, budget: int, max_pages: int = 999,
        min_price: int = 0, max_price: int = 0,
    ) -> list[dict]:
        """Discover all tradeable player cards within a price range."""
        if max_price <= 0:
            max_price = int(budget * 0.10)
        if min_price <= 0:
            min_price = 1000

        all_candidates = []
        seen_ids: set[int] = set()

        for page_num in range(1, max_pages + 1):
            logger.info(f"Fetching player list page {page_num}...")
            url = f"/api/fut/players/v2/26/?page={page_num}"
            if min_price > 0:
                url += f"&price__gte={min_price}"
            if max_price > 0:
                url += f"&price__lte={max_price}"
            result = await self._get(url)

            if not result or "data" not in result:
                break
            players = result["data"]
            if not players:
                break

            ea_ids = []
            player_map = {}
            for p in players:
                ea_id = self._extract_ea_id(p)
                if ea_id and ea_id not in seen_ids:
                    seen_ids.add(ea_id)
                    ea_ids.append(ea_id)
                    player_map[ea_id] = p

            if ea_ids:
                prices = await self.get_batch_prices(ea_ids)
                price_map = {
                    p["eaId"]: p["price"]
                    for p in prices if p.get("price") is not None
                }
                for ea_id, player_data in player_map.items():
                    price = price_map.get(ea_id, 0) or 0
                    if min_price <= price <= max_price:
                        player_data["ea_id"] = ea_id
                        player_data["price"] = price
                        all_candidates.append(player_data)

            logger.info(
                f"Page {page_num}: {len(players)} players, "
                f"{len(all_candidates)} candidates so far"
            )

            if not result.get("next"):
                break

        logger.info(f"Discovery complete: {len(all_candidates)} candidates")
        return all_candidates

    # ── Private helpers ────────────────────────────────────────────

    @staticmethod
    def _extract_ea_id(player_data: dict) -> Optional[int]:
        """Extract EA ID from a player list entry's slug."""
        slug = player_data.get("slug", "")
        ea_id_str = slug.split("-", 1)[-1] if "-" in slug else ""
        try:
            return int(ea_id_str)
        except ValueError:
            return None

    @staticmethod
    def _extract_current_bin(prices: dict) -> Optional[int]:
        """Get the current lowest BIN from price data."""
        live_auctions = prices.get("liveAuctions", [])
        if live_auctions:
            return min(a["buyNowPrice"] for a in live_auctions)

        current_price = prices.get("currentPrice", {}) or {}
        price = current_price.get("price", 0) or 0
        if price:
            return int(price)

        overview = prices.get("overview", {}) or {}
        return overview.get("averageBin", 0) or None

    @staticmethod
    def _parse_price_history(ea_id: int, prices: dict) -> list[PricePoint]:
        """Parse price history from the API response."""
        points = []
        for point in prices.get("history", []):
            try:
                points.append(PricePoint(
                    resource_id=ea_id,
                    recorded_at=datetime.fromisoformat(
                        point["date"].replace("Z", "+00:00")
                    ),
                    lowest_bin=point["price"],
                ))
            except Exception:
                continue
        return points

    @staticmethod
    def _parse_sales(ea_id: int, prices: dict) -> list[SaleRecord]:
        """Parse completed sales from the API response."""
        sales = []
        for auction in prices.get("completedAuctions", []):
            try:
                sales.append(SaleRecord(
                    resource_id=ea_id,
                    sold_at=datetime.fromisoformat(
                        auction["soldDate"].replace("Z", "+00:00")
                    ),
                    sold_price=auction["soldPrice"],
                ))
            except Exception:
                continue
        return sales

    def _build_player(self, defn: dict) -> Player:
        """Build a Player model from a card definition response."""
        position_str = POSITION_MAP.get(defn.get("position", 0), "?")
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
