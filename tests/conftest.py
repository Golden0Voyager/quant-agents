"""Shared pytest fixtures that prevent CI hangs when API keys are absent."""

import os
from unittest.mock import MagicMock, patch

import pytest


def pytest_configure(config):
    for marker in ("unit", "integration", "smoke"):
        config.addinivalue_line("markers", f"{marker}: {marker}-level tests")


_API_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_CN_API_KEY",
    "ZHIPU_API_KEY",
    "ZHIPU_CN_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "OPENROUTER_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
    "AGNES_API_KEY",
    "MODELSCOPE_API_KEY",
    "NVIDIA_API_KEY",
)


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch):
    for env_var in _API_KEY_ENV_VARS:
        # `or` not a .get default: an env var present but empty (e.g. a key left
        # blank in a .env copied from .env.example) must still get the placeholder.
        monkeypatch.setenv(env_var, os.environ.get(env_var) or "placeholder")


@pytest.fixture(autouse=True)
def _isolate_config():
    """Reset the global dataflows config before and after each test.

    ``set_config`` merges (it never clears keys absent from the override), so a
    test that sets e.g. ``tool_vendors`` would otherwise leak into later tests
    and make routing behavior order-dependent. Replace the global outright so
    every test starts from a clean DEFAULT_CONFIG.
    """
    import copy

    import tradingagents.dataflows.config as config_module
    import tradingagents.default_config as default_config

    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)
    yield
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)


@pytest.fixture(autouse=True)
def _reset_pricing_yaml_cache():
    """Reset the module-level pricing YAML cache before and after each test.

    ``_load_pricing_yaml`` memoizes in ``_PRICING_YAML``; a cached value
    from one test (possibly pointing at a temp dir) would leak into later
    tests and make price lookups order-dependent.
    """
    from tradingagents.llm_clients import pricing as pricing_module

    pricing_module._PRICING_YAML = None
    yield
    pricing_module._PRICING_YAML = None


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost"}


class _NetworkBlockedError(RuntimeError):
    """Raised when a unit/smoke test tries to open an external connection."""


@pytest.fixture(autouse=True)
def _block_external_network(request, monkeypatch):
    """Fail fast when unit/smoke tests touch the real network.

    Several unit tests historically opened live connections (e.g. yfinance
    hitting Yahoo), which hangs indefinitely under flaky network conditions
    and stalls the whole suite — on CI runners without network this wedges
    the job until timeout. Unit tests must mock their vendors; this guard
    makes violations visible in milliseconds instead of hanging. Loopback
    is allowed for tests that stand up local servers.
    """
    marker = request.node.get_closest_marker("unit") or request.node.get_closest_marker("smoke")
    if not marker:
        yield
        return

    import socket

    original_connect = socket.socket.connect

    def guarded_connect(sock, address):
        host = address[0] if isinstance(address, tuple) else None
        if host in _LOOPBACK_HOSTS:
            return original_connect(sock, address)
        raise _NetworkBlockedError(
            f"unit test attempted external network connection to {address!r}; "
            f"mock the vendor instead of calling the network"
        )

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    # Safety net: even loopback or pre-existing connections must not stall.
    monkeypatch.setattr(socket, "getdefaulttimeout", lambda: 10.0)
    socket.setdefaulttimeout(10.0)

    # curl_cffi (yfinance's HTTP backend) drives libcurl in C and bypasses
    # the socket.socket.connect patch above — guard it at the Session layer.
    try:
        import curl_cffi.requests as cffi_requests
    except ImportError:
        cffi_requests = None
    if cffi_requests is not None:
        from urllib.parse import urlparse

        original_request = cffi_requests.Session.request

        def guarded_request(session, method, url, *args, **kwargs):
            host = urlparse(str(url)).hostname or ""
            if host in _LOOPBACK_HOSTS:
                return original_request(session, method, url, *args, **kwargs)
            raise _NetworkBlockedError(
                f"unit test attempted external HTTP request to {url!r}; "
                f"mock the vendor instead of calling the network"
            )

        monkeypatch.setattr(cffi_requests.Session, "request", guarded_request)

    yield
    socket.setdefaulttimeout(None)


@pytest.fixture()
def mock_llm_client():
    client = MagicMock()
    client.get_llm.return_value = MagicMock()
    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        return_value=client,
    ):
        yield client
