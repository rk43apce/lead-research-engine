import json
import re
import asyncio
import time
from typing import Any, Dict, Optional

import aiohttp

from services.logger import log_info, log_timing, log_warning


class GeminiClient:
    """Small wrapper around the Gemini API.

    This class only calls Gemini and returns parsed JSON. It does not decide
    business logic, choose signals, or validate outreach quality.
    """

    def __init__(
        self,
        api_key: Optional[str],
        model: str = "gemini-flash-latest",
        timeout_seconds: float = 30,
        max_retries: int = 2,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    async def generate_json(self, prompt, operation="gemini_generate", company=None):
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")

        url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % self.model
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key or "",
        }

        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                # Low temperature keeps the output more consistent and conservative.
                "temperature": 0.2,
                # Ask Gemini for JSON so downstream code can parse it safely.
                "response_mime_type": "application/json",
            },
        }

        last_error: Optional[Exception] = None
        max_attempts = self.max_retries + 1
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)

        for attempt in range(1, max_attempts + 1):
            started_at = time.perf_counter()

            try:
                # Do not log the full prompt or response. Prompts can contain
                # scraped text, and responses can contain generated email copy.
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, headers=headers, json=body) as response:
                        duration_ms = log_timing(started_at)
                        log_info(
                            "Gemini response received",
                            company=company,
                            step=operation,
                            status=response.status,
                            duration_ms=duration_ms,
                        )

                        if response.status == 200:
                            print("Gemini OK: %s for %s" % (operation, company or "company"))
                        elif response.status == 429:
                            print("Gemini rate limit: 429 for %s. Retrying if attempts remain." % (company or "company"))
                        else:
                            print("Gemini API status %s for %s" % (response.status, company or "company"))

                        if response.status == 429:
                            retry_after = self._retry_after_seconds(response)
                            raise RuntimeError("Gemini rate limited with 429; retry_after=%s" % retry_after)

                        response.raise_for_status()
                        response_body = await response.json()

                text = response_body["candidates"][0]["content"]["parts"][0]["text"]

                # We parse and validate JSON because the rest of the pipeline
                # expects a dictionary, not free-form model text.
                parsed = self._parse_json(text)
                return parsed

            except Exception as exc:
                last_error = exc
                log_warning(
                    "Gemini request failed",
                    company=company,
                    step=operation,
                    attempt=attempt,
                    error=self._safe_error(exc),
                )

                if attempt < max_attempts:
                    wait_seconds = self._retry_delay_seconds(attempt, exc)
                    print("Waiting %s seconds before retrying Gemini for %s" % (wait_seconds, company or "company"))
                    await asyncio.sleep(wait_seconds)

        raise RuntimeError("Gemini request failed: %s" % last_error)

    def _retry_after_seconds(self, response) -> int:
        value = response.headers.get("Retry-After")
        if not value:
            return 0

        try:
            return int(value)
        except ValueError:
            return 0

    def _retry_delay_seconds(self, attempt: int, exc: Exception) -> int:
        message = str(exc)
        match = re.search(r"retry_after=(\d+)", message)
        if match:
            retry_after = int(match.group(1))
            if retry_after > 0:
                return min(retry_after, 30)

        return min(2 ** attempt, 10)

    def _parse_json(self, text: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                raise
            parsed = json.loads(match.group(0))

        if not isinstance(parsed, dict):
            raise ValueError("Gemini returned JSON that is not an object.")

        return parsed

    def _safe_error(self, exc: Optional[Exception]) -> str:
        if exc is None:
            return "unknown error"

        message = str(exc)

        if self.api_key:
            message = message.replace(self.api_key, "[REDACTED]")

        return re.sub(r"key=[^&\s]+", "key=[REDACTED]", message)
