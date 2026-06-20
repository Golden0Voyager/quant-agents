import pytest

from cli.watchlists import (
    list_watchlists,
    load_watchlist,
    parse_watchlist_content,
    save_watchlist,
)


@pytest.fixture(autouse=True)
def _mock_watchlists_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("cli.watchlists._WATCHLISTS_DIR", tmp_path / "watchlists")


def test_save_and_load_watchlist():
    tickers = ["AAPL", "MSFT", "GOOGL"]
    path = save_watchlist("tech", tickers)
    assert path.exists()
    loaded = load_watchlist("tech")
    assert loaded == tickers


def test_parse_watchlist_content():
    raw = """# Tech stocks
AAPL
MSFT

# Energy
TSLA
"""
    result = parse_watchlist_content(raw)
    assert result == ["AAPL", "MSFT", "TSLA"]


def test_load_watchlist_not_found():
    with pytest.raises(FileNotFoundError):
        load_watchlist("nonexistent")


def test_list_watchlists():
    save_watchlist("alpha", ["A"])
    save_watchlist("beta", ["B"])
    names = list_watchlists()
    assert sorted(names) == ["alpha", "beta"]
