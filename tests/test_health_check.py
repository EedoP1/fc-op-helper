"""Tests for FUTBIN health check: HTML parsing, score computation, edge cases."""

from unittest.mock import patch, MagicMock

import httpx

from src.futbin_client import (
    FutbinClient,
    _parse_sales_html,
    _parse_price,
    _parse_futbin_date,
)
from src.health_check import compute_health_score


# ── Mock HTML fixtures ────────────────────────────────────────────────

SEARCH_HTML = """
<html><body>
<div class="player-list">
  <a href="/26/player/12345/kylian-mbappe">
    <span>Kylian Mbappe</span>
  </a>
  <a href="/26/player/67890/erling-haaland">
    <span>Erling Haaland</span>
  </a>
</div>
</body></html>
"""

SEARCH_HTML_NO_RESULTS = """
<html><body>
<div class="player-list">
  <p>No players found.</p>
</div>
</body></html>
"""

SALES_TABLE_HTML = """
<html><body>
<table>
  <thead>
    <tr><th>Date</th><th>Listed for</th><th>Sold for</th><th>EA Tax</th><th>Net Price</th><th>Type</th></tr>
  </thead>
  <tbody>
    <tr>
      <td>Mar 25, 2026 14:30</td>
      <td>15,000</td>
      <td>15,000</td>
      <td>750</td>
      <td>14,250</td>
      <td>Sold</td>
    </tr>
    <tr>
      <td>Mar 25, 2026 13:00</td>
      <td>16,000</td>
      <td>0</td>
      <td>0</td>
      <td>0</td>
      <td>Expired</td>
    </tr>
    <tr>
      <td>Mar 24, 2026</td>
      <td>14,500</td>
      <td>14,500</td>
      <td>725</td>
      <td>13,775</td>
      <td>Sold</td>
    </tr>
    <tr>
      <td>Mar 24, 2026</td>
      <td>17,000</td>
      <td>0</td>
      <td>0</td>
      <td>0</td>
      <td>Expired</td>
    </tr>
    <tr>
      <td>Mar 23, 2026</td>
      <td>15,500</td>
      <td>15,500</td>
      <td>775</td>
      <td>14,725</td>
      <td>Sold</td>
    </tr>
  </tbody>
</table>
</body></html>
"""

SALES_EMPTY_HTML = """
<html><body>
<div>No sales data available.</div>
</body></html>
"""


# ── Test FutbinClient.search_player ───────────────────────────────────

class TestSearchPlayer:
    """Tests for search_player HTML parsing."""

    def test_search_player_finds_first_result(self):
        """search_player returns the first player's futbin_id."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.text = SEARCH_HTML
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        client = FutbinClient()
        with patch.object(client, "_get", return_value=mock_response):
            result = client.search_player("Mbappe")

        assert result == 12345

    def test_search_player_not_found(self):
        """search_player returns None when no player links found."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.text = SEARCH_HTML_NO_RESULTS
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        client = FutbinClient()
        with patch.object(client, "_get", return_value=mock_response):
            result = client.search_player("NonExistentPlayer")

        assert result is None

    def test_search_player_http_error(self):
        """search_player returns None on HTTP error."""
        client = FutbinClient()
        with patch.object(client, "_get", return_value=None):
            result = client.search_player("SomePlayer")

        assert result is None


# ── Test FutbinClient.fetch_sales_page ────────────────────────────────

class TestFetchSalesPage:
    """Tests for fetch_sales_page HTML parsing."""

    def test_fetch_sales_parses_table(self):
        """fetch_sales_page correctly parses HTML table rows."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.text = SALES_TABLE_HTML
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        client = FutbinClient()
        with patch.object(client, "_get", return_value=mock_response):
            sales = client.fetch_sales_page(12345, "Kylian Mbappe")

        assert len(sales) == 5
        # First row: sold for 15000
        assert sales[0]["sold_for"] == 15000
        assert sales[0]["listed_for"] == 15000
        assert sales[0]["type"] == "Sold"

        # Second row: expired (sold_for = 0)
        assert sales[1]["sold_for"] == 0
        assert sales[1]["listed_for"] == 16000
        assert sales[1]["type"] == "Expired"

    def test_fetch_sales_empty_page(self):
        """fetch_sales_page returns empty list when no table found."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.text = SALES_EMPTY_HTML
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        # Also mock the JSON fallback to return None
        client = FutbinClient()
        call_count = [0]
        original_get = client._get

        def side_effect(url):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_response  # HTML page
            return None  # JSON fallback fails

        with patch.object(client, "_get", side_effect=side_effect):
            sales = client.fetch_sales_page(12345, "SomePlayer")

        assert sales == []

    def test_fetch_sales_http_error(self):
        """fetch_sales_page returns empty list on HTTP error."""
        client = FutbinClient()
        with patch.object(client, "_get", return_value=None):
            sales = client.fetch_sales_page(12345, "SomePlayer")

        assert sales == []


# ── Test HTML parsing helpers ─────────────────────────────────────────

class TestParsingHelpers:
    """Tests for price and date parsing."""

    def test_parse_price_with_commas(self):
        assert _parse_price("15,000") == 15000

    def test_parse_price_plain(self):
        assert _parse_price("500") == 500

    def test_parse_price_zero(self):
        assert _parse_price("0") == 0

    def test_parse_price_empty(self):
        assert _parse_price("") == 0

    def test_parse_price_with_spaces(self):
        assert _parse_price(" 12,500 ") == 12500

    def test_parse_futbin_date_standard(self):
        dt = _parse_futbin_date("Mar 25, 2026 14:30")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 25
        assert dt.hour == 14
        assert dt.minute == 30

    def test_parse_futbin_date_no_time(self):
        dt = _parse_futbin_date("Mar 24, 2026")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 3
        assert dt.day == 24

    def test_parse_futbin_date_iso(self):
        dt = _parse_futbin_date("2026-03-25")
        assert dt is not None
        assert dt.year == 2026

    def test_parse_futbin_date_unparseable(self):
        dt = _parse_futbin_date("invalid date")
        assert dt is None

    def test_parse_sales_html_full_table(self):
        sales = _parse_sales_html(SALES_TABLE_HTML)
        assert len(sales) == 5
        sold = [s for s in sales if s["sold_for"] > 0]
        expired = [s for s in sales if s["sold_for"] == 0]
        assert len(sold) == 3
        assert len(expired) == 2

    def test_parse_sales_html_no_table(self):
        sales = _parse_sales_html(SALES_EMPTY_HTML)
        assert sales == []

    def test_parse_sales_html_empty_string(self):
        sales = _parse_sales_html("")
        assert sales == []


# ── Test health score computation ─────────────────────────────────────

class TestHealthScore:
    """Tests for compute_health_score."""

    def test_perfect_match(self):
        """Identical data should score 100."""
        our = {
            "our_sell_rate": 0.60,
            "our_median_price": 15000,
            "our_listing_count": 50,
            "our_min_price": 14000,
            "our_max_price": 17000,
            "our_sold": 30,
            "our_expired": 20,
        }
        futbin = {
            "futbin_sell_rate": 0.60,
            "futbin_median_price": 15000,
            "futbin_total": 50,
            "futbin_min_listed": 14000,
            "futbin_max_listed": 17000,
            "futbin_sold": 30,
            "futbin_expired": 20,
        }
        score = compute_health_score(our, futbin)
        assert score == 100.0

    def test_completely_off(self):
        """Wildly different data should score low."""
        our = {
            "our_sell_rate": 0.90,
            "our_median_price": 50000,
            "our_listing_count": 200,
            "our_min_price": 40000,
            "our_max_price": 60000,
            "our_sold": 90,
            "our_expired": 10,
        }
        futbin = {
            "futbin_sell_rate": 0.10,
            "futbin_median_price": 15000,
            "futbin_total": 20,
            "futbin_min_listed": 14000,
            "futbin_max_listed": 16000,
            "futbin_sold": 2,
            "futbin_expired": 18,
        }
        score = compute_health_score(our, futbin)
        assert score < 20.0

    def test_no_futbin_data(self):
        """Empty FUTBIN data with some of our data scores middling."""
        our = {
            "our_sell_rate": 0.50,
            "our_median_price": 15000,
            "our_listing_count": 30,
            "our_min_price": 14000,
            "our_max_price": 16000,
            "our_sold": 15,
            "our_expired": 15,
        }
        futbin = {
            "futbin_sell_rate": 0.0,
            "futbin_median_price": 0,
            "futbin_total": 0,
            "futbin_min_listed": 0,
            "futbin_max_listed": 0,
            "futbin_sold": 0,
            "futbin_expired": 0,
        }
        score = compute_health_score(our, futbin)
        assert 40.0 <= score <= 60.0

    def test_both_empty(self):
        """Both empty datasets should score 100 (consistent)."""
        our = {
            "our_sell_rate": 0.0,
            "our_median_price": 0,
            "our_listing_count": 0,
            "our_min_price": 0,
            "our_max_price": 0,
            "our_sold": 0,
            "our_expired": 0,
        }
        futbin = {
            "futbin_sell_rate": 0.0,
            "futbin_median_price": 0,
            "futbin_total": 0,
            "futbin_min_listed": 0,
            "futbin_max_listed": 0,
            "futbin_sold": 0,
            "futbin_expired": 0,
        }
        score = compute_health_score(our, futbin)
        assert score == 100.0

    def test_score_in_range(self):
        """Health score is always between 0 and 100."""
        our = {
            "our_sell_rate": 0.75,
            "our_median_price": 20000,
            "our_listing_count": 40,
            "our_min_price": 18000,
            "our_max_price": 22000,
            "our_sold": 30,
            "our_expired": 10,
        }
        futbin = {
            "futbin_sell_rate": 0.60,
            "futbin_median_price": 19000,
            "futbin_total": 45,
            "futbin_min_listed": 17000,
            "futbin_max_listed": 21000,
            "futbin_sold": 27,
            "futbin_expired": 18,
        }
        score = compute_health_score(our, futbin)
        assert 0.0 <= score <= 100.0

    def test_all_expired_futbin(self):
        """All expired on FUTBIN side."""
        our = {
            "our_sell_rate": 0.0,
            "our_median_price": 0,
            "our_listing_count": 10,
            "our_min_price": 14000,
            "our_max_price": 16000,
            "our_sold": 0,
            "our_expired": 10,
        }
        futbin = {
            "futbin_sell_rate": 0.0,
            "futbin_median_price": 0,
            "futbin_total": 20,
            "futbin_min_listed": 14000,
            "futbin_max_listed": 16000,
            "futbin_sold": 0,
            "futbin_expired": 20,
        }
        score = compute_health_score(our, futbin)
        # Sell-through matches (both 0%), prices match, counts off but close
        assert score >= 50.0
