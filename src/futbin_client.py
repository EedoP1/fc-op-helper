"""FUTBIN HTTP client for fetching player search results and sales data."""

import logging
import re
import time
from datetime import datetime
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

REQUEST_DELAY = 1.5  # seconds between FUTBIN requests

FUTBIN_BASE = "https://www.futbin.com"


class FutbinClient:
    """Synchronous HTTP client for fetching data from FUTBIN."""

    def __init__(self) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=30.0,
            follow_redirects=True,
        )
        self._last_request_at: float = 0.0

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def _get(self, url: str) -> httpx.Response | None:
        """Send a GET request with rate limiting and error handling.

        Args:
            url: The URL to fetch.

        Returns:
            Response object or None on error.
        """
        # Rate limiting
        elapsed = time.time() - self._last_request_at
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)

        try:
            resp = self._client.get(url)
            self._last_request_at = time.time()
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as e:
            logger.error("FUTBIN HTTP error %s for %s", e.response.status_code, url)
            return None
        except Exception as e:
            logger.error("FUTBIN request failed for %s: %s", url, e)
            return None

    # ── Public API ────────────────────────────────────────────────────

    def search_player(self, name: str) -> int | None:
        """Search FUTBIN for a player by name and return their futbin_id.

        Args:
            name: Player name to search for.

        Returns:
            The FUTBIN player ID (integer) or None if not found.
        """
        encoded_name = quote(name)
        url = f"{FUTBIN_BASE}/26/players?search={encoded_name}"
        resp = self._get(url)
        if resp is None:
            return None

        try:
            soup = BeautifulSoup(resp.text, "html.parser")
            # Look for player links in format /26/player/{id}/{slug}
            link = soup.find("a", href=re.compile(r"/26/player/\d+/"))
            if link:
                match = re.search(r"/26/player/(\d+)/", link["href"])
                if match:
                    futbin_id = int(match.group(1))
                    logger.info("Found FUTBIN ID %d for '%s'", futbin_id, name)
                    return futbin_id
        except Exception as e:
            logger.error("Error parsing FUTBIN search for '%s': %s", name, e)

        logger.warning("Player '%s' not found on FUTBIN", name)
        return None

    def fetch_sales_page(self, futbin_id: int, name: str) -> list[dict]:
        """Fetch and parse the FUTBIN sales page for a player.

        Args:
            futbin_id: The FUTBIN player ID.
            name: Player name (used in URL slug).

        Returns:
            List of sale dicts with keys: date, listed_for, sold_for, type.
            Empty list if page cannot be parsed.
        """
        slug = name.lower().replace(" ", "-")
        slug = re.sub(r"[^a-z0-9-]", "", slug)
        url = f"{FUTBIN_BASE}/26/sales/{futbin_id}/{slug}?platform=ps"
        resp = self._get(url)
        if resp is None:
            return []

        sales = _parse_sales_html(resp.text)
        if sales:
            logger.info(
                "Parsed %d sales from FUTBIN for %s (ID %d)",
                len(sales), name, futbin_id,
            )
            return sales

        # Fallback: try JSON endpoint
        logger.debug("HTML table empty, trying JSON fallback for %s", name)
        json_url = (
            f"{FUTBIN_BASE}/26/playerPrices"
            f"?player={futbin_id}&platform=ps"
        )
        json_resp = self._get(json_url)
        if json_resp is not None:
            sales = _parse_sales_json(json_resp.text, futbin_id)
            if sales:
                logger.info(
                    "Parsed %d sales from FUTBIN JSON for %s (ID %d)",
                    len(sales), name, futbin_id,
                )
                return sales

        logger.warning(
            "Could not parse FUTBIN sales for %s (ID %d) "
            "— table may be JS-rendered",
            name, futbin_id,
        )
        return []


# ── HTML / JSON parsing helpers ───────────────────────────────────────

def _parse_sales_html(html: str) -> list[dict]:
    """Parse the FUTBIN sales HTML table into sale records.

    The table has columns: Date, Listed for, Sold for, EA Tax, Net Price, Type.

    Args:
        html: Raw HTML string from the sales page.

    Returns:
        List of sale dicts.
    """
    sales: list[dict] = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if not table:
            return []

        tbody = table.find("tbody")
        if not tbody:
            return []

        rows = tbody.find_all("tr")
        for row in rows:
            try:
                cells = row.find_all("td")
                if len(cells) < 6:
                    continue

                date_str = cells[0].get_text(strip=True)
                listed_for = _parse_price(cells[1].get_text(strip=True))
                sold_for = _parse_price(cells[2].get_text(strip=True))
                sale_type = cells[5].get_text(strip=True)

                # Parse date — FUTBIN uses various formats
                date = _parse_futbin_date(date_str)

                sales.append({
                    "date": date,
                    "listed_for": listed_for,
                    "sold_for": sold_for,
                    "type": sale_type,
                })
            except Exception:
                continue
    except Exception as e:
        logger.error("Error parsing FUTBIN sales HTML: %s", e)

    return sales


def _parse_sales_json(text: str, futbin_id: int) -> list[dict]:
    """Parse the FUTBIN JSON prices endpoint as a fallback.

    Args:
        text: Raw response text (may be JSON).
        futbin_id: The player's FUTBIN ID.

    Returns:
        List of sale dicts, or empty list on failure.
    """
    import json
    try:
        data = json.loads(text)
        # The JSON structure varies; try common patterns
        player_data = data.get(str(futbin_id), data)
        if isinstance(player_data, dict):
            prices = player_data.get("ps", [])
            if isinstance(prices, list):
                return [
                    {
                        "date": None,
                        "listed_for": int(p.get("listed_for", 0) or 0),
                        "sold_for": int(p.get("sold_for", 0) or 0),
                        "type": p.get("type", "unknown"),
                    }
                    for p in prices
                    if isinstance(p, dict)
                ]
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.debug("JSON fallback parse failed: %s", e)
    return []


def _parse_price(text: str) -> int:
    """Parse a price string like '12,500' or '0' into an integer.

    Args:
        text: Price string, possibly with commas or whitespace.

    Returns:
        Integer price value, 0 if unparseable.
    """
    cleaned = re.sub(r"[^\d]", "", text)
    return int(cleaned) if cleaned else 0


def _parse_futbin_date(text: str) -> datetime | None:
    """Parse a FUTBIN date string into a datetime.

    FUTBIN uses formats like 'Mar 25, 2026 14:30' or '2026-03-25'.

    Args:
        text: Date string from the table.

    Returns:
        Parsed datetime or None if unparseable.
    """
    formats = [
        "%b %d, %Y %I:%M %p",
        "%b %d, %Y %H:%M",
        "%b %d, %Y",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ]
    cleaned = text.strip()
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue

    # FUTBIN often omits the year: "Mar 26, 9:38 PM" — inject current year
    current_year = datetime.now().year
    for fmt in ["%b %d, %I:%M %p", "%b %d, %H:%M"]:
        try:
            parsed = datetime.strptime(cleaned, fmt)
            return parsed.replace(year=current_year)
        except ValueError:
            continue
    logger.debug("Could not parse FUTBIN date: '%s'", text)
    return None
