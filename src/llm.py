"""LLM provider abstraction — supports Claude and OpenAI-compatible APIs."""

import logging
from abc import ABC, abstractmethod

from config import config

logger = logging.getLogger("hntldr.llm")

_provider_instance = None


class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, prompt: str, max_tokens: int, temperature: float = 0.4) -> str:
        ...


class ClaudeProvider(LLMProvider):
    def __init__(self):
        import anthropic
        self._client = anthropic.AsyncAnthropic(api_key=config.llm_api_key)

    async def complete(self, prompt: str, max_tokens: int, temperature: float = 0.4) -> str:
        import anthropic
        try:
            response = await self._client.messages.create(
                model=config.llm_model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()
        except anthropic.RateLimitError:
            logger.warning("Claude rate limit hit")
            raise
        except anthropic.APIError as e:
            logger.error(f"Claude API error: {e}")
            raise


class OpenAIProvider(LLMProvider):
    def __init__(self):
        import os
        import openai
        kwargs = {"api_key": config.llm_api_key}
        base_url = os.environ.get("OPENAI_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        self._client = openai.AsyncOpenAI(**kwargs)

    async def complete(self, prompt: str, max_tokens: int, temperature: float = 0.4) -> str:
        response = await self._client.chat.completions.create(
            model=config.llm_model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return (response.choices[0].message.content or "").strip()


def get_provider() -> LLMProvider:
    """Singleton factory — returns the configured LLM provider."""
    global _provider_instance
    if _provider_instance is None:
        if config.llm_provider == "openai":
            _provider_instance = OpenAIProvider()
        else:
            _provider_instance = ClaudeProvider()
        logger.info(f"LLM provider: {config.llm_provider} ({config.llm_model})")
    return _provider_instance
