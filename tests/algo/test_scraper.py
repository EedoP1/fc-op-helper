from src.algo.scraper import parse_futbin_price_data, extract_ea_id


def test_parse_futbin_price_data():
    """Test parsing FUTBIN's data-ps-data attribute."""
    raw = '[[1727654400000,15000],[1727740800000,15200],[1727827200000,14800]]'
    result = parse_futbin_price_data(raw)
    assert len(result) == 3
    assert result[0] == (1727654400000, 15000)
    assert result[1] == (1727740800000, 15200)
    assert result[2] == (1727827200000, 14800)


def test_parse_futbin_price_data_bad_input():
    assert parse_futbin_price_data("not json") == []
    assert parse_futbin_price_data("") == []


def test_extract_ea_id():
    html = '<img src="https://cdn3.futbin.com/content/fifa26/img/players/239085.png?w=44" alt="Haaland">'
    assert extract_ea_id(html) == 239085


def test_extract_ea_id_with_p_prefix():
    html = '<img src="https://cdn3.futbin.com/content/fifa26/img/players/p50570733.png">'
    assert extract_ea_id(html) == 50570733


def test_extract_ea_id_not_found():
    html = '<img src="https://cdn3.futbin.com/content/fifa26/img/cards/gold.png">'
    assert extract_ea_id(html) is None
