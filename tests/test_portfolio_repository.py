"""Unit tests for tradingagents.portfolio.repository.PortfolioRepository.

Covers initialization, atomic save/load, error handling, and utility queries.
All tests use local temporary directories and do not perform network I/O.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from tradingagents.portfolio.models import Holding, Portfolio
from tradingagents.portfolio.repository import (
    _DEFAULT_FILENAME,
    PortfolioRepository,
)


@pytest.mark.unit
class PortfolioRepositoryInitTests(unittest.TestCase):
    """Tests for PortfolioRepository construction and path handling."""

    def test_default_path(self):
        """No args -> path ends with the default filename."""
        repo = PortfolioRepository()
        self.assertEqual(repo.path.name, _DEFAULT_FILENAME)
        self.assertTrue(str(repo.path).endswith(_DEFAULT_FILENAME))

    def test_custom_path(self):
        """Custom data_path is stored as the repository path."""
        repo = PortfolioRepository(data_path="/tmp/custom.json")
        self.assertEqual(repo.path, Path("/tmp/custom.json"))


@pytest.mark.unit
class PortfolioRepoSaveLoadTests(unittest.TestCase):
    """Tests for save/load behavior, atomic writes, and error paths."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._portfolio_path = os.path.join(self._tmp, "portfolio.json")
        self._repo = PortfolioRepository(data_path=self._portfolio_path)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_portfolio(self, ticker: str = "AAPL") -> Portfolio:
        holding = Holding(ticker=ticker, shares=100, avg_cost=150.0)
        return Portfolio(holdings={ticker: holding})

    def test_save_creates_file(self):
        """Saving a portfolio creates the target JSON file."""
        portfolio = self._make_portfolio()
        self._repo.save(portfolio)
        self.assertTrue(os.path.exists(self._portfolio_path))

    def test_save_atomicity(self):
        """A saved portfolio can be loaded back unchanged."""
        portfolio = self._make_portfolio("AAPL")
        self._repo.save(portfolio)

        loaded = self._repo.load()
        self.assertIsInstance(loaded, Portfolio)
        self.assertIn("AAPL", loaded.holdings)
        self.assertEqual(loaded.holdings["AAPL"].shares, 100)
        self.assertEqual(loaded.holdings["AAPL"].avg_cost, 150.0)

    def test_save_creates_parent_dir(self):
        """Saving to a nested path creates missing parent directories."""
        deep_path = os.path.join(self._tmp, "a", "b", "c", "portfolio.json")
        repo = PortfolioRepository(data_path=deep_path)
        repo.save(self._make_portfolio())

        self.assertTrue(os.path.exists(deep_path))
        loaded = repo.load()
        self.assertIn("AAPL", loaded.holdings)

    def test_save_cleans_up_temp_on_failure(self):
        """If serialization fails, the temporary file is removed."""
        portfolio = self._make_portfolio()
        temp_path = self._repo.path.with_suffix(".tmp")

        with patch("json.dump", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                self._repo.save(portfolio)

        self.assertFalse(temp_path.exists())
        self.assertFalse(os.path.exists(self._portfolio_path))

    def test_load_returns_portfolio(self):
        """load() returns a Portfolio object with expected holdings."""
        portfolio = self._make_portfolio("MSFT")
        self._repo.save(portfolio)

        loaded = self._repo.load()
        self.assertIsInstance(loaded, Portfolio)
        self.assertTrue(loaded.has_holding("MSFT"))
        holding = loaded.get_holding("MSFT")
        self.assertIsNotNone(holding)
        self.assertEqual(holding.ticker, "MSFT")
        self.assertEqual(holding.shares, 100)
        self.assertEqual(holding.avg_cost, 150.0)

    def test_load_raises_on_missing_file(self):
        """load() raises FileNotFoundError when the file does not exist."""
        with self.assertRaises(FileNotFoundError):
            self._repo.load()

    def test_load_raises_on_corrupted_json(self):
        """load() raises ValueError when the JSON is corrupted."""
        Path(self._portfolio_path).write_text("not valid json", encoding="utf-8")
        with self.assertRaises(ValueError):
            self._repo.load()

    def test_load_backups_corrupted_file(self):
        """Corrupted file is copied to a .json.bak backup before raising."""
        corrupted_content = "{invalid json"
        Path(self._portfolio_path).write_text(corrupted_content, encoding="utf-8")
        backup_path = self._repo.path.with_suffix(".json.bak")

        with self.assertRaises(ValueError):
            self._repo.load()

        self.assertTrue(backup_path.exists())
        self.assertEqual(backup_path.read_text(encoding="utf-8"), corrupted_content)


@pytest.mark.unit
class PortfolioRepoUtilityTests(unittest.TestCase):
    """Tests for exists(), get_mtime(), and get_holding() helpers."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._portfolio_path = os.path.join(self._tmp, "portfolio.json")
        self._repo = PortfolioRepository(data_path=self._portfolio_path)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _make_portfolio(self, ticker: str = "AAPL") -> Portfolio:
        holding = Holding(ticker=ticker, shares=100, avg_cost=150.0)
        return Portfolio(holdings={ticker: holding})

    def test_exists_true(self):
        """exists() returns True after saving a non-empty portfolio."""
        self._repo.save(self._make_portfolio())
        self.assertTrue(self._repo.exists())

    def test_exists_false(self):
        """exists() returns False when the file does not exist."""
        self.assertFalse(self._repo.exists())

    def test_exists_false_empty_file(self):
        """exists() returns False when the file is empty."""
        Path(self._portfolio_path).touch()
        self.assertTrue(os.path.exists(self._portfolio_path))
        self.assertFalse(self._repo.exists())

    def test_get_mtime_returns_datetime(self):
        """get_mtime() returns a datetime after the file is saved."""
        from datetime import datetime

        self._repo.save(self._make_portfolio())
        mtime = self._repo.get_mtime()
        self.assertIsInstance(mtime, datetime)

    def test_get_mtime_returns_none(self):
        """get_mtime() returns None when the file does not exist."""
        self.assertIsNone(self._repo.get_mtime())

    def test_get_holding_returns_dict(self):
        """get_holding() returns a dict for an existing ticker."""
        self._repo.save(self._make_portfolio("AAPL"))
        result = self._repo.get_holding("AAPL")

        self.assertIsInstance(result, dict)
        self.assertEqual(result.get("ticker"), "AAPL")
        self.assertEqual(result.get("shares"), 100)
        self.assertEqual(result.get("avg_cost"), 150.0)

    def test_get_holding_returns_none(self):
        """get_holding() returns None when the ticker is not present."""
        self._repo.save(self._make_portfolio("AAPL"))
        result = self._repo.get_holding("TSLA")
        self.assertIsNone(result)

    def test_get_holding_returns_none_when_no_file(self):
        """get_holding() returns None when the portfolio file is missing."""
        result = self._repo.get_holding("AAPL")
        self.assertIsNone(result)
