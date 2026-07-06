import re
from typing import Any

from langchain_anthropic import ChatAnthropic

from .base_client import BaseLLMClient, normalize_content
from .retry_utils import RetryConfig, with_llm_retry
from .validators import validate_model

_PASSTHROUGH_KWARGS = (
    "timeout", "max_retries", "api_key", "max_tokens", "temperature",
    "callbacks", "http_client", "http_async_client", "effort",
)

# Anthropic's extended-thinking ``effort`` parameter is accepted by Opus 4.5+
# and Sonnet 4.5+ only. Haiku (any version shipped to date) 400s with
# ``"This model does not support the effort parameter"`` (#831). Future
# ``claude-{opus,sonnet}-X-Y`` releases inherit effort support via the
# forward-compat pattern below; future Haiku stays excluded by default.
_EFFORT_EXACT = {
    # "claude-mythos-preview" is a non-standard preview name that Anthropic
    # used internally for an effort-capable research checkpoint. It is NOT
    # a public model ID, but it may appear in enterprise/private-beta
    # deployments. If Anthropic retires the name, remove this entry.
    "claude-mythos-preview",
}
_EFFORT_PATTERN = re.compile(r"^claude-(opus|sonnet)-\d+-\d+$")


def _supports_effort(model: str) -> bool:
    """Whether Anthropic accepts the ``effort`` parameter for this model."""
    model_lc = model.lower()
    return model_lc in _EFFORT_EXACT or bool(_EFFORT_PATTERN.match(model_lc))


class NormalizedChatAnthropic(ChatAnthropic):
    """ChatAnthropic with normalized content output.

    Claude models with extended thinking or tool use return content as a
    list of typed blocks. This normalizes to string for consistent
    downstream handling.
    """

    @with_llm_retry
    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))


class AnthropicClient(BaseLLMClient):
    """Client for Anthropic Claude models."""

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured ChatAnthropic instance."""
        self._require_model()
        self.warn_if_unknown_model()
        llm_kwargs = {"model": self.model}

        if self.base_url:
            llm_kwargs["base_url"] = self.base_url

        for key in _PASSTHROUGH_KWARGS:
            if key not in self.kwargs:
                continue
            if key == "effort" and not _supports_effort(self.model):
                continue
            llm_kwargs[key] = self.kwargs[key]

        retry_config = self.kwargs.get("retry_config")
        if retry_config is not None and isinstance(retry_config, dict):
            retry_config = RetryConfig(**retry_config)  # pragma: no cover  -- dict→RetryConfig kwargs path

        llm = NormalizedChatAnthropic(**llm_kwargs)
        if retry_config is not None:
            llm._retry_config = retry_config
        return llm

    def validate_model(self) -> bool:
        """Validate model for Anthropic."""
        return validate_model("anthropic", self.model)
