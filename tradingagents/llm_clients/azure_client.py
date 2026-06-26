import os
from typing import Any

from langchain_openai import AzureChatOpenAI

from .base_client import BaseLLMClient, normalize_content
from .retry_utils import RetryConfig, with_llm_retry

_PASSTHROUGH_KWARGS = (
    "timeout", "max_retries", "api_key", "reasoning_effort", "temperature",
    "callbacks", "http_client", "http_async_client",
)


class NormalizedAzureChatOpenAI(AzureChatOpenAI):
    """AzureChatOpenAI with normalized content output."""

    @with_llm_retry
    def invoke(self, input, config=None, **kwargs):
        return normalize_content(super().invoke(input, config, **kwargs))


class AzureOpenAIClient(BaseLLMClient):
    """Client for Azure OpenAI deployments.

    Requires environment variables:
        AZURE_OPENAI_API_KEY: API key
        AZURE_OPENAI_ENDPOINT: Endpoint URL (e.g. https://<resource>.openai.azure.com/)
        AZURE_OPENAI_DEPLOYMENT_NAME: Deployment name
        OPENAI_API_VERSION: API version (e.g. 2025-03-01-preview)
    """

    def __init__(self, model: str, base_url: str | None = None, **kwargs):
        super().__init__(model, base_url, **kwargs)

    def get_llm(self) -> Any:
        """Return configured AzureChatOpenAI instance."""
        self._require_model()
        self.warn_if_unknown_model()

        llm_kwargs = {
            "model": self.model,
            "azure_deployment": os.environ.get("AZURE_OPENAI_DEPLOYMENT_NAME", self.model),
        }

        for key in _PASSTHROUGH_KWARGS:
            if key in self.kwargs:
                llm_kwargs[key] = self.kwargs[key]

        retry_config = self.kwargs.get("retry_config")
        if retry_config is not None and isinstance(retry_config, dict):
            retry_config = RetryConfig(**retry_config)

        llm = NormalizedAzureChatOpenAI(**llm_kwargs)
        if retry_config is not None:
            setattr(llm, "_retry_config", retry_config)
        return llm

    def validate_model(self) -> bool:
        """Azure accepts any deployed model name."""
        return True
