import json
import re
import time
from typing import Any, Dict, Optional, Protocol

import aiohttp

from web.core.logger import log_info, log_timing, log_warning


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


class GroqClient(JSONParsingMixin):
    """Minimal Groq chat-completions client for the demo UI provider option."""

    provider_name = "Groq"

    def __init__(self, api_key: Optional[str], model: str, timeout_seconds: float = 30) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def generate_json(self, prompt, operation="groq_generate", company=None):
        if not self.api_key:
            raise RuntimeError("GROQ_API_KEY is not configured.")

        headers = {
            "Authorization": "Bearer %s" % self.api_key,
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post("https://api.groq.com/openai/v1/chat/completions", headers=headers, json=body) as response:
                    response.raise_for_status()
                    response_body = await response.json()
            text = response_body["choices"][0]["message"]["content"]
            return self._parse_json(text)
        except Exception as exc:
            raise RuntimeError("Groq request failed: %s" % self._safe_error(exc, self.api_key))


class GeminiClient(JSONParsingMixin):
    """Minimal Gemini client for JSON generation from the demo UI provider option."""

    provider_name = "Gemini"

    def __init__(self, api_key: Optional[str], model: str, timeout_seconds: float = 30) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def generate_json(self, prompt, operation="gemini_generate", company=None):
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")

        url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s" % (
            self.model,
            self.api_key,
        )
        body = {
            "contents": [{"parts": [{"text": "%s\n\nReturn only a JSON object." % prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "response_mime_type": "application/json",
            },
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=body) as response:
                    response.raise_for_status()
                    response_body = await response.json()
            text = response_body["candidates"][0]["content"]["parts"][0]["text"]
            return self._parse_json(text)
        except Exception as exc:
            raise RuntimeError("Gemini request failed: %s" % self._safe_error(exc, self.api_key))


class MockClient:
    """No-network LLM used for demos and interviews."""

    async def generate_json(self, prompt, operation="mock_generate", company=None):
        payload = self._payload_from_prompt(prompt)
        company_name = company or payload.get("company") or "this institution"

        if operation == "llm_classification":
            services = payload.get("services_found") or []
            risk_clues = payload.get("risk_clues") or []
            institution_type = payload.get("institution_hint") or self._institution_type(company_name, payload)
            fraud_angle = self._fraud_angle(institution_type, services, risk_clues)
            return {
                "institution_type": institution_type,
                "customer_segment": self._customer_segment(payload),
                "services": services[:5] or self._default_services(company_name),
                "fraud_angle": fraud_angle,
            }

        services = payload.get("services_found") or []
        risk_clues = payload.get("risk_clues") or []
        fraud_angle = payload.get("fraud_angle") or self._fraud_angle(
            payload.get("institution_type") or self._institution_type(company_name, payload),
            services,
            risk_clues,
        )
        service_detail = self._first_text(services, "financial services")
        signal_summary = str(payload.get("signal_summary") or "").strip()
        has_signal = bool(payload.get("has_signal"))
        has_website_context = bool(payload.get("has_website_context_source"))

        if has_signal and signal_summary:
            opener = "Hi %s team, I saw the public update about %s." % (
                company_name,
                self._shorten(signal_summary, 18),
            )
        elif has_website_context and signal_summary:
            opener = "Hi %s team, I reviewed your public website context around %s." % (
                company_name,
                self._shorten(signal_summary, 16),
            )
        else:
            opener = "Hi %s team, I could not find a recent verifiable public signal, so I will keep this general." % company_name

        subject = self._subject(company_name, service_detail, fraud_angle)
        body = (
            "%s Your work around %s can create review pressure around %s. "
            "The PreCogs helps augment existing fraud and risk workflows with AI-assisted context "
            "while avoiding PII exposure and keeping teams in control. "
            "Would a short note on fit be useful?"
        ) % (opener, service_detail, self._shorten(fraud_angle, 16))

        return {
            "subject": subject,
            "email": body,
        }

    def _payload_from_prompt(self, prompt):
        marker = "Payload:"
        if marker not in prompt:
            marker = "Context:"
        if marker not in prompt:
            return {}

        text = prompt.split(marker, 1)[1].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return {}

        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _institution_type(self, company, payload):
        name = str(company).lower()
        if "credit union" in name:
            return "Credit union"
        if "bank" in name:
            return "Bank"
        if payload.get("is_website_blocked"):
            return "Website blocked for scraping"
        return "Financial institution"

    def _customer_segment(self, payload):
        customer_clues = payload.get("customer_clues") or []
        return self._first_text(customer_clues, "Retail and business customers")

    def _default_services(self, company):
        name = str(company).lower()
        if "credit union" in name:
            return ["member banking", "digital banking"]
        if "bank" in name:
            return ["banking", "payments"]
        return ["financial services", "digital channels"]

    def _fraud_angle(self, institution_type, services, risk_clues):
        if risk_clues:
            return self._shorten(self._first_text(risk_clues, ""), 22)

        service_text = " ".join(services).lower()
        if "loan" in service_text:
            return "Loan application fraud and identity verification pressure."
        if "payment" in service_text or "card" in service_text:
            return "Payment fraud and account takeover pressure."
        if "digital" in service_text or "online" in service_text:
            return "Account-opening and digital channel fraud pressure."
        if "credit union" in str(institution_type).lower():
            return "Member account takeover and scam review pressure."
        return "Fraud review pressure across customer-facing financial workflows."

    def _subject(self, company, service_detail, fraud_angle):
        company_word = str(company).split()[0] if company else "Fraud"
        if service_detail and service_detail != "financial services":
            subject = "%s fraud support for %s" % (service_detail, company_word)
        else:
            subject = "Fraud review support for %s" % company_word
        return " ".join(subject.split()[:8]).rstrip(".,;:")

    def _first_text(self, values, fallback):
        if isinstance(values, list):
            for value in values:
                text = str(value).strip()
                if text:
                    return text
        return fallback

    def _shorten(self, text, max_words):
        words = str(text or "").split()
        if len(words) <= max_words:
            return str(text or "").strip()
        return " ".join(words[:max_words]).rstrip(".,;:") + "..."


def create_llm_client(
    provider: str,
    openai_api_key: Optional[str],
    openai_model: str,
    anthropic_api_key: Optional[str],
    anthropic_model: str,
    gemini_api_key: Optional[str],
    gemini_model: str,
    groq_api_key: Optional[str],
    groq_model: str,
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

    if normalized_provider == "gemini":
        return GeminiClient(
            api_key=gemini_api_key,
            model=gemini_model,
            timeout_seconds=timeout_seconds,
        )

    if normalized_provider == "groq":
        return GroqClient(
            api_key=groq_api_key,
            model=groq_model,
            timeout_seconds=timeout_seconds,
        )

    if normalized_provider == "mock":
        return MockClient()

    raise ValueError("Unsupported LLM_PROVIDER: %s" % provider)
