from src.algo.scraper import parse_price_history


def test_parse_price_history():
    """Test parsing fut.gg API response into (timestamp, price) tuples."""
    raw = {
        "history": [
            {"date": "2025-09-30T12:00:00Z", "price": 15000},
            {"date": "2025-09-30T13:00:00Z", "price": 15200},
            {"date": "2025-09-30T14:00:00Z", "price": 14800},
        ]
    }
    result = parse_price_history(12345, raw)
    assert len(result) == 3
    assert result[0] == (12345, "2025-09-30T12:00:00+00:00", 15000)
    assert result[1] == (12345, "2025-09-30T13:00:00+00:00", 15200)
    assert result[2] == (12345, "2025-09-30T14:00:00+00:00", 14800)


def test_parse_price_history_skips_bad_records():
    raw = {
        "history": [
            {"date": "2025-09-30T12:00:00Z", "price": 15000},
            {"bad_key": "missing fields"},
            {"date": "2025-09-30T14:00:00Z", "price": 14800},
        ]
    }
    result = parse_price_history(12345, raw)
    assert len(result) == 2
