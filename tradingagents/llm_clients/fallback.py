"""Provider-level fallback chain for LLM invocations.

When the primary provider returns a transient error (quota exceeded, rate
limit, 5xx), the fallback chain tries the next provider in the configured
list before giving up.

Each individual LLM still has its own ``@with_llm_retry`` (3 retries per
provider per the ``retry_utils`` module). Only when all retries on one
provider are exhausted does the chain advance to the next.

Usage
-----
At graph initialisation, create the full LLM chain and patch the primary::

    primary_llm = create_llm_client(...).get_llm()
    fallback_llms = [create_llm_client(...).get_llm(), ...]
    patched = patch_invoke_with_fallback(primary_llm, fallback_llms)
    # ``patched`` is the primary instance with a patched ``invoke`` —
    # ``bind_tools``, ``with_structured_output``, and ``|`` piping all
    # transparently resolve to the patched method.
"""

from __future__ import annotations

import functools
import logging
from typing import Any

from .retry_utils import is_transient_llm_error

logger = logging.getLogger(__name__)


def patch_invoke_with_fallback(primary_llm: Any, fallback_llms: list[Any]) -> Any:
    """Patch *primary_llm*'s ``invoke`` to fall back through *fallback_llms*.

    The patched method tries each LLM in sequence (primary first, then
    fallbacks). Each individual LLM still has its own ``@with_llm_retry``
    (3 retries per provider). Only when all retries on one provider fail
    does it move to the next.

    This approach preserves full LangChain compatibility: ``bind_tools``,
    ``with_structured_output``, and ``|`` piping all continue to work
    because they call ``self.invoke()`` on the patched instance — Python's
    instance-attribute lookup finds the patched method before the class
    method.

    Args:
        primary_llm: The primary LLM instance (tried first).
        fallback_llms: Ordered list of fallback LLM instances.

    Returns:
        The *primary_llm* instance with its ``invoke`` patched in-place.
    """
    # Capture the original (retry-decorated) ``invoke`` from the class so we
    # can call it on *different* instances (primary + fallbacks) — each has
    # its own ``_retry_config`` that the decorator reads from ``self``.
    original_invoke = type(primary_llm).invoke
    chain = [primary_llm] + fallback_llms

    @functools.wraps(original_invoke)
    def patched_invoke(
        self: Any,  # noqa: ARG001 — bound to primary_llm, but we ignore it
        input: Any,
        config: Any | None = None,
        **kwargs: Any,
    ) -> Any:
        errors: list[Exception] = []
        for i, llm in enumerate(chain):
            try:
                return original_invoke(llm, input, config, **kwargs)
            except Exception as exc:
                if not is_transient_llm_error(exc) or i == len(chain) - 1:
                    raise
                errors.append(exc)
                provider_label = getattr(llm, "model_name", type(llm).__name__)
                logger.warning(
                    "LLM provider %d/%d (%s) failed: %s — falling back",
                    i + 1,
                    len(chain),
                    provider_label,
                    exc,
                )
        # Unreachable: the loop always returns or raises.
        raise errors[-1] if errors else RuntimeError(  # pragma: no cover
            "patch_invoke_with_fallback unexpectedly exhausted chain"
        )

    # Bind the patched method to the instance so ``self`` resolves correctly
    # when called via the instance (``primary_llm.invoke(...)``).
    primary_llm.invoke = patched_invoke.__get__(primary_llm, type(primary_llm))
    return primary_llm
