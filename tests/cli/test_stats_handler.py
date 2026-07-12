from unittest.mock import MagicMock

import pytest

from cli.stats_handler import StatsCallbackHandler

pytestmark = pytest.mark.unit


def _make_response(model: str, tokens_in: int, tokens_out: int):
    """Build a minimal LLMResult-like response for on_llm_end."""
    msg = MagicMock()
    msg.usage_metadata = {"input_tokens": tokens_in, "output_tokens": tokens_out}
    generation = MagicMock()
    generation.message = msg
    response = MagicMock()
    response.generations = [[generation]]
    return response


def test_llm_calls_by_model_tracks_chat_model_calls():
    handler = StatsCallbackHandler()
    serialized = {"kwargs": {"model_name": "deepseek-v4-flash"}}

    handler.on_chat_model_start(serialized, [])
    handler.on_llm_end(_make_response("deepseek-v4-flash", 100, 50))

    handler.on_chat_model_start(serialized, [])
    handler.on_llm_end(_make_response("deepseek-v4-flash", 200, 80))

    serialized2 = {"kwargs": {"model_name": "Qwen/Qwen3.5-397B-A17B"}}
    handler.on_chat_model_start(serialized2, [])
    handler.on_llm_end(_make_response("Qwen/Qwen3.5-397B-A17B", 10, 5))

    stats = handler.get_stats()
    assert stats["llm_calls"] == 3
    assert stats["llm_calls_by_model"] == {
        "deepseek-v4-flash": 2,
        "Qwen/Qwen3.5-397B-A17B": 1,
    }
