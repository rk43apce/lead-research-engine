import json
import re
import time
from typing import Any, Dict, Optional, Protocol

import aiohttp

from services.logger import log_info, log_timing, log_warning


class LLMClient(Protocol):
    async def generate_json(self, prompt, operation="llm_generate", company=None):
        ...


class JSONParsingMixin:
    provider_name = "LLM"

    def _parse_json(self, text: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                raise
            parsed = json.loads(match.group(0))

        if not isinstance(parsed, dict):
            raise ValueError("%s returned JSON that is not an object." % self.provider_name)

        return parsed

    def _safe_error(self, exc: Optional[Exception], api_key: Optional[str]) -> str:
        if exc is None:
            return "unknown error"

        message = str(exc)

        if api_key:
            message = message.replace(api_key, "[REDACTED]")

        return re.sub(r"key=[^&\s]+", "key=[REDACTED]", message)


class OpenAIClient(JSONParsingMixin):
    """Small wrapper around the OpenAI Responses API.

    This class only calls OpenAI and returns parsed JSON. It does not decide
    business logic, choose signals, or validate outreach quality.
    """

    provider_name = "OpenAI"

    def __init__(
        self,
        api_key: Optional[str],
        model: str = "gpt-4.1-mini",
        timeout_seconds: float = 30,
        max_retries: int = 0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    async def generate_json(self, prompt, operation="openai_generate", company=None):
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        url = "https://api.openai.com/v1/responses"
        headers = {
            "Authorization": "Bearer %s" % self.api_key,
            "Content-Type": "application/json",
        }

        body = {
            "model": self.model,
            "input": prompt,
            # Low temperature keeps the output more consistent and conservative.
            "temperature": 0.2,
            # Ask OpenAI for JSON so downstream code can parse it safely.
            "text": {
                "format": {
                    "type": "json_object",
                },
            },
        }

        last_error: Optional[Exception] = None
        max_attempts = self.max_retries + 1
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)

        for attempt in range(1, max_attempts + 1):
            started_at = time.perf_counter()

            try:
                log_info(
                    "LLM request started",
                    company=company,
                    step=operation,
                    attempt=attempt,
                    provider=self.provider_name,
                    model=self.model,
                    prompt_chars=len(prompt),
                )

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, headers=headers, json=body) as response:
                        duration_ms = log_timing(started_at)
                        log_info(
                            "LLM response received",
                            company=company,
                            step=operation,
                            provider=self.provider_name,
                            status=response.status,
                            duration_ms=duration_ms,
                        )

                        if response.status == 429:
                            print("OpenAI rate limit: 429 for %s. Retrying if attempts remain." % (company or "company"))
                        elif response.status != 200:
                            print("OpenAI API status %s for %s" % (response.status, company or "company"))

                        if response.status == 429:
                            retry_after = self._retry_after_seconds(response)
                            raise RuntimeError("OpenAI rate limited with 429; retry_after=%s" % retry_after)

                        response.raise_for_status()
                        response_body = await response.json()

                text = self._extract_text(response_body)
                log_info(
                    "LLM response parsed",
                    company=company,
                    step=operation,
                    attempt=attempt,
                    provider=self.provider_name,
                    response_chars=len(text),
                )

                # We parse and validate JSON because the rest of the pipeline
                # expects a dictionary, not free-form model text.
                parsed = self._parse_json(text)
                return parsed

            except Exception as exc:
                last_error = exc
                log_warning(
                    "LLM request failed",
                    company=company,
                    step=operation,
                    attempt=attempt,
                    provider=self.provider_name,
                    error=self._safe_error(exc, self.api_key),
                )

        raise RuntimeError("OpenAI request failed: %s" % last_error)

    def _extract_text(self, response_body: Dict[str, Any]) -> str:
        output_text = response_body.get("output_text")
        if isinstance(output_text, str) and output_text:
            return output_text

        for item in response_body.get("output", []):
            if not isinstance(item, dict):
                continue

            for content in item.get("content", []):
                if not isinstance(content, dict):
                    continue

                text = content.get("text")
                if isinstance(text, str) and text:
                    return text

        raise ValueError("OpenAI response did not include output text.")

    def _retry_after_seconds(self, response) -> int:
        value = response.headers.get("Retry-After")
        if not value:
            return 0

        try:
            return int(value)
        except ValueError:
            return 0

class AnthropicClient(JSONParsingMixin):
    """Small wrapper around Anthropic Messages API with the same JSON interface."""

    provider_name = "Anthropic"

    def __init__(
        self,
        api_key: Optional[str],
        model: str = "claude-3-5-haiku-latest",
        timeout_seconds: float = 30,
        max_retries: int = 0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    async def generate_json(self, prompt, operation="anthropic_generate", company=None):
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured.")

        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "max_tokens": 2048,
            "temperature": 0.2,
            "system": "Return only valid JSON. Do not include markdown, comments, or prose.",
            "messages": [
                {
                    "role": "user",
                    "content": "%s\n\nReturn only a JSON object." % prompt,
                }
            ],
        }

        last_error: Optional[Exception] = None
        max_attempts = self.max_retries + 1
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)

        for attempt in range(1, max_attempts + 1):
            started_at = time.perf_counter()

            try:
                log_info(
                    "LLM request started",
                    company=company,
                    step=operation,
                    attempt=attempt,
                    provider=self.provider_name,
                    model=self.model,
                    prompt_chars=len(prompt),
                )

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, headers=headers, json=body) as response:
                        duration_ms = log_timing(started_at)
                        log_info(
                            "LLM response received",
                            company=company,
                            step=operation,
                            provider=self.provider_name,
                            status=response.status,
                            duration_ms=duration_ms,
                        )

                        if response.status == 429:
                            print("Anthropic rate limit: 429 for %s. Retrying if attempts remain." % (company or "company"))
                        elif response.status != 200:
                            print("Anthropic API status %s for %s" % (response.status, company or "company"))

                        response.raise_for_status()
                        response_body = await response.json()

                text = self._extract_text(response_body)
                log_info(
                    "LLM response parsed",
                    company=company,
                    step=operation,
                    attempt=attempt,
                    provider=self.provider_name,
                    response_chars=len(text),
                )
                return self._parse_json(text)

            except Exception as exc:
                last_error = exc
                log_warning(
                    "LLM request failed",
                    company=company,
                    step=operation,
                    attempt=attempt,
                    provider=self.provider_name,
                    error=self._safe_error(exc, self.api_key),
                )

        raise RuntimeError("Anthropic request failed: %s" % last_error)

    def _extract_text(self, response_body: Dict[str, Any]) -> str:
        parts = []
        for item in response_body.get("content", []):
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text:
                parts.append(text)

        if parts:
            return "\n".join(parts)

        raise ValueError("Anthropic response did not include output text.")


def create_llm_client(
    provider: str,
    openai_api_key: Optional[str],
    openai_model: str,
    anthropic_api_key: Optional[str],
    anthropic_model: str,
    timeout_seconds: float,
) -> LLMClient:
    normalized_provider = (provider or "openai").strip().lower()

    if normalized_provider == "openai":
        return OpenAIClient(
            api_key=openai_api_key,
            model=openai_model,
            timeout_seconds=timeout_seconds,
        )

    if normalized_provider == "anthropic":
        return AnthropicClient(
            api_key=anthropic_api_key,
            model=anthropic_model,
            timeout_seconds=timeout_seconds,
        )

    raise ValueError("Unsupported LLM_PROVIDER: %s" % provider)
