import json
import re
import time
from typing import Any, Dict, Optional

import aiohttp

from services.logger import log_info, log_timing, log_warning


class OpenAIClient:
    """Small wrapper around the OpenAI Responses API.

    This class only calls OpenAI and returns parsed JSON. It does not decide
    business logic, choose signals, or validate outreach quality.
    """

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
                    "OpenAI request started",
                    company=company,
                    step=operation,
                    attempt=attempt,
                    model=self.model,
                    prompt_chars=len(prompt),
                )

                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, headers=headers, json=body) as response:
                        duration_ms = log_timing(started_at)
                        log_info(
                            "OpenAI response received",
                            company=company,
                            step=operation,
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
                    "OpenAI response parsed",
                    company=company,
                    step=operation,
                    attempt=attempt,
                    response_chars=len(text),
                )

                # We parse and validate JSON because the rest of the pipeline
                # expects a dictionary, not free-form model text.
                parsed = self._parse_json(text)
                return parsed

            except Exception as exc:
                last_error = exc
                log_warning(
                    "OpenAI request failed",
                    company=company,
                    step=operation,
                    attempt=attempt,
                    error=self._safe_error(exc),
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

    def _parse_json(self, text: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                raise
            parsed = json.loads(match.group(0))

        if not isinstance(parsed, dict):
            raise ValueError("OpenAI returned JSON that is not an object.")

        return parsed

    def _safe_error(self, exc: Optional[Exception]) -> str:
        if exc is None:
            return "unknown error"

        message = str(exc)

        if self.api_key:
            message = message.replace(self.api_key, "[REDACTED]")

        return re.sub(r"key=[^&\s]+", "key=[REDACTED]", message)
