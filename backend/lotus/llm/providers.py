"""HTTP providers: OpenAI, Azure OpenAI, Anthropic. Selected with LOTUS_LLM_PROVIDER."""
from __future__ import annotations

from typing import Any

import httpx

from ..config import settings
from .base import LLM, json_instructions


class OpenAILLM(LLM):
    name = "openai"

    def __init__(self) -> None:
        super().__init__()
        self.model = settings.llm_model or "gpt-4o-mini"
        self.base = settings.llm_base_url or "https://api.openai.com/v1"
        self.client = httpx.AsyncClient(timeout=120)

    def _url_headers(self) -> tuple[str, dict[str, str]]:
        return f"{self.base}/chat/completions", {"Authorization": f"Bearer {settings.llm_api_key}"}

    async def _complete(self, system: str, user: str, schema: dict[str, Any], task: str,
                        context: dict[str, Any]) -> str:
        url, headers = self._url_headers()
        body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system + "\n\n" + json_instructions(schema)},
                         {"role": "user", "content": user}],
        }
        r = await self.client.post(url, json=body, headers=headers)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class AzureOpenAILLM(OpenAILLM):
    name = "azure_openai"

    def _url_headers(self) -> tuple[str, dict[str, str]]:
        # LOTUS_LLM_BASE_URL = https://<resource>.openai.azure.com ; LOTUS_LLM_MODEL = deployment name
        url = (f"{settings.llm_base_url}/openai/deployments/{self.model}/chat/completions"
               f"?api-version={settings.azure_api_version}")
        return url, {"api-key": settings.llm_api_key}


class AnthropicLLM(LLM):
    name = "anthropic"

    def __init__(self) -> None:
        super().__init__()
        self.model = settings.llm_model or "claude-sonnet-5-5"
        self.base = settings.llm_base_url or "https://api.anthropic.com"
        self.client = httpx.AsyncClient(timeout=120)

    async def _complete(self, system: str, user: str, schema: dict[str, Any], task: str,
                        context: dict[str, Any]) -> str:
        r = await self.client.post(
            f"{self.base}/v1/messages",
            headers={"x-api-key": settings.llm_api_key, "anthropic-version": "2023-06-01"},
            json={"model": self.model, "max_tokens": 4096, "temperature": 0,
                  "system": system + "\n\n" + json_instructions(schema),
                  "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"])


def build_llm() -> LLM:
    from .mock import MockLLM

    provider = settings.llm_provider.lower()
    if provider == "openai":
        return OpenAILLM()
    if provider == "azure_openai":
        return AzureOpenAILLM()
    if provider == "anthropic":
        return AnthropicLLM()
    return MockLLM()
