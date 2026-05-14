from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

import requests

from services.logger import elapsed_ms, log_context

LOGGER = logging.getLogger(__name__)


class GeminiClient:
    def __init__(
        self,
        api_key: str | None,
        model: str = "gemini-flash-latest",
        timeout_seconds: float = 30,
        max_retries: int = 2,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    async def generate_json(self, prompt: str, operation: str = "gemini_generate") -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")
        return await asyncio.to_thread(self._generate_json_sync, prompt, operation)

    def _generate_json_sync(self, prompt: str, operation: str) -> dict[str, Any]:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key or "",
        }
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "response_mime_type": "application/json",
            },
        }

        last_error: Exception | None = None
        with log_context(step=operation):
            LOGGER.info(
                "Gemini request prepared model=%s prompt_chars=%s payload_summary=%s",
                self.model,
                len(prompt),
                {"contents": 1, "temperature": 0.2, "response_mime_type": "application/json"},
            )
            for attempt in range(1, self.max_retries + 2):
                started_at = time.perf_counter()
                try:
                    LOGGER.info("Gemini request start attempt=%s max_attempts=%s", attempt, self.max_retries + 1)
                    response = requests.post(url, headers=headers, json=body, timeout=self.timeout_seconds)
                    duration_ms = elapsed_ms(started_at)
                    LOGGER.info(
                        "Gemini response received status=%s duration_ms=%s response_chars=%s",
                        response.status_code,
                        duration_ms,
                        len(response.text),
                    )
                    response.raise_for_status()
                    data = response.json()
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    parsed = self._parse_json(text)
                    LOGGER.info("Gemini JSON parsed keys=%s", sorted(parsed.keys()))
                    LOGGER.debug("Gemini raw text preview=%s", text[:500])
                    return parsed
                except requests.Timeout as exc:
                    last_error = exc
                    LOGGER.warning(
                        "Gemini timeout attempt=%s duration_ms=%s error=%s",
                        attempt,
                        elapsed_ms(started_at),
                        self._safe_error(exc),
                    )
                except requests.RequestException as exc:
                    last_error = exc
                    LOGGER.warning(
                        "Gemini network/API failure attempt=%s duration_ms=%s error=%s",
                        attempt,
                        elapsed_ms(started_at),
                        self._safe_error(exc),
                    )
                except (KeyError, IndexError, json.JSONDecodeError, ValueError) as exc:
                    last_error = exc
                    LOGGER.exception(
                        "Gemini JSON parsing failure attempt=%s duration_ms=%s error=%s",
                        attempt,
                        elapsed_ms(started_at),
                        self._safe_error(exc),
                    )
        raise RuntimeError(f"Gemini request failed: {last_error}")

    def _parse_json(self, text: str) -> dict[str, Any]:
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

    def _safe_error(self, exc: Exception) -> str:
        message = str(exc)
        if self.api_key:
            message = message.replace(self.api_key, "[REDACTED]")
        return re.sub(r"key=[^&\\s]+", "key=[REDACTED]", message)
