import asyncio
import json
import re
import time
from typing import Any, Dict, Optional

import requests

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
        max_retries: int = 0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    async def generate_json(self, prompt, operation="gemini_generate", company=None):
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")

        # requests is blocking, so we run it in a worker thread.
        # This keeps the async pipeline responsive while Gemini is called.
        return await asyncio.to_thread(self._generate_json_sync, prompt, operation, company)

    def _generate_json_sync(self, prompt: str, operation: str, company) -> Dict[str, Any]:
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

        for attempt in range(1, max_attempts + 1):
            started_at = time.perf_counter()

            try:
                # Do not log the full prompt or response. Prompts can contain
                # scraped text, and responses can contain generated email copy.
                response = requests.post(url, headers=headers, json=body, timeout=self.timeout_seconds)

                duration_ms = log_timing(started_at)
                log_info(
                    "Gemini response received",
                    company=company,
                    step=operation,
                    status=response.status_code,
                    duration_ms=duration_ms,
                )

                if response.status_code == 200:
                    print("Gemini OK: %s for %s" % (operation, company or "company"))
                elif response.status_code == 429:
                    print("Gemini rate limit: 429 for %s" % (company or "company"))
                else:
                    print("Gemini API status %s for %s" % (response.status_code, company or "company"))

                response.raise_for_status()
                response_body = response.json()
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

        raise RuntimeError("Gemini request failed: %s" % last_error)

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
