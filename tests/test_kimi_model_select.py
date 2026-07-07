"""Kimi model selection: dynamically fetches from API when key is set,
falls back to the hardcoded catalog when the fetch fails, and exits cleanly
on cancel."""

import os
from unittest import mock

import pytest

from cli import utils


def _asks(value):
    return mock.Mock(ask=mock.Mock(return_value=value))


@pytest.mark.unit
class TestKimiPromptLabel:
    @pytest.mark.parametrize("mode,label", [("quick", "Quick-Thinking"), ("deep", "Deep-Thinking")])
    def test_prompt_states_the_mode(self, mode, label):
        captured = {}

        def fake_select(message, **kwargs):
            captured["message"] = message
            return _asks("kimi-k2.6")

        with mock.patch.object(utils, "_fetch_kimi_models", return_value=[("kimi-k2.6", "kimi-k2.6")]), \
             mock.patch.object(utils.questionary, "select", side_effect=fake_select):
            out = utils.select_kimi_model(mode)

        assert label in captured["message"]
        assert out == "kimi-k2.6"


@pytest.mark.unit
class TestKimiDynamicList:
    def test_uses_fetched_models_when_api_key_set(self):
        fetched = [
            ("kimi-k2.6", "kimi-k2.6"),
            ("kimi-k2.5", "kimi-k2.5"),
            ("kimi-for-coding", "kimi-for-coding"),
        ]
        captured = {}

        def fake_select(message, **kwargs):
            captured["values"] = [c.value for c in kwargs["choices"]]
            return _asks("kimi-k2.6")

        with mock.patch.object(utils, "_fetch_kimi_models", return_value=fetched), \
             mock.patch.object(utils.questionary, "select", side_effect=fake_select):
            utils.select_kimi_model("deep")

        assert "kimi-k2.6" in captured["values"]
        assert "kimi-k2.5" in captured["values"]
        assert "custom" in captured["values"]

    def test_falls_back_to_catalog_when_fetch_fails(self):
        captured = {}

        def fake_select(message, **kwargs):
            captured["values"] = [c.value for c in kwargs["choices"]]
            return _asks("kimi-k2.6")

        with mock.patch.object(utils, "_fetch_kimi_models", return_value=[]), \
             mock.patch.object(utils.questionary, "select", side_effect=fake_select):
            utils.select_kimi_model("quick")

        assert "kimi-k2.6" in captured["values"]
        assert "kimi-k2.5" in captured["values"]
        assert "custom" in captured["values"]


@pytest.mark.unit
class TestKimiFetchModels:
    def test_fetch_returns_empty_without_api_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            assert utils._fetch_kimi_models() == []

    def test_fetch_parses_api_response(self):
        payload = {
            "data": [
                {"id": "kimi-k2.6", "name": "Kimi K2.6", "created": 3000},
                {"id": "kimi-k2.5", "name": "Kimi K2.5", "created": 2000},
            ]
        }
        resp = mock.Mock()
        resp.json.return_value = payload
        resp.raise_for_status = mock.Mock()

        with mock.patch.dict(os.environ, {"KIMI_CODING_API_KEY": "sk-123"}), \
             mock.patch("requests.get", return_value=resp) as mock_get:
            out = utils._fetch_kimi_models()

        assert out == [("Kimi K2.6", "kimi-k2.6"), ("Kimi K2.5", "kimi-k2.5")]
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer sk-123"

    def test_fetch_handles_api_errors(self):
        with mock.patch.dict(os.environ, {"KIMI_CODING_API_KEY": "sk-123"}), \
             mock.patch("requests.get", side_effect=Exception("timeout")):
            assert utils._fetch_kimi_models() == []


@pytest.mark.unit
class TestKimiCancelExits:
    def test_dropdown_cancel_exits(self):
        with mock.patch.object(utils, "_fetch_kimi_models", return_value=[]), \
             mock.patch.object(utils.questionary, "select", return_value=_asks(None)), \
             pytest.raises(SystemExit):
            utils.select_kimi_model("quick")

    def test_custom_id_cancel_exits(self):
        with mock.patch.object(utils, "_fetch_kimi_models", return_value=[]), \
             mock.patch.object(utils.questionary, "select", return_value=_asks("custom")), \
             mock.patch.object(utils.questionary, "text", return_value=_asks(None)), \
             pytest.raises(SystemExit):
            utils.select_kimi_model("deep")
