from copy import deepcopy

import tradingagents.default_config as default_config

# Use default config but allow it to be overridden
_config: dict | None = None


def initialize_config() -> dict:
    """Initialize the configuration with default values."""
    global _config
    if _config is None:
        _config = deepcopy(default_config.DEFAULT_CONFIG)
    return _config


def set_config(config: dict):
    """Update the configuration with custom values.

    Dict-valued keys (e.g. ``data_vendors``) are merged one level deep so a
    partial update like ``{"data_vendors": {"core_stock_apis": "alpha_vantage"}}``
    keeps the other nested keys from the default; scalar keys are replaced.

    The result is always computed from a FRESH copy of the default config and
    swapped in atomically. ``set_config`` therefore means "adopt this config",
    not "patch whatever happened to be configured before" — a batch run can
    never inherit stale nested keys left behind by a previous ticker/profile
    (the old behavior merged into the live dict, so keys absent from the new
    config silently persisted).
    """
    global _config
    cfg = deepcopy(default_config.DEFAULT_CONFIG)
    incoming = deepcopy(config)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    _config = cfg


def get_config() -> dict:
    """Get the current configuration."""
    return deepcopy(initialize_config())


# Initialize with default config
initialize_config()
