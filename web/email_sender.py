from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol


@dataclass
class SendResult:
    success: bool
    provider_message_id: str = ""
    error_message: str = ""


class EmailProvider(Protocol):
    def send_email(self, to_email: str, subject: str, body: str) -> SendResult:
        ...


class MockEmailProvider:
    """Demo provider: records a successful send without calling an external API."""

    def send_email(self, to_email: str, subject: str, body: str) -> SendResult:
        return SendResult(success=True, provider_message_id="mock:%s" % to_email)


class SendGridEmailProvider:
    """Twilio SendGrid adapter behind a vendor-neutral interface."""

    def __init__(
        self,
        api_key: str,
        from_email: str,
        from_name: str = "",
        reply_to: str = "",
        timeout_seconds: int = 20,
    ) -> None:
        self.api_key = api_key
        self.from_email = from_email
        self.from_name = from_name
        self.reply_to = reply_to
        self.timeout_seconds = timeout_seconds

    def send_email(self, to_email: str, subject: str, body: str) -> SendResult:
        if not self.api_key:
            return SendResult(success=False, error_message="SendGrid API key is not configured.")
        if not self.from_email:
            return SendResult(success=False, error_message="From email is not configured.")

        payload = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": self.from_email},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }
        if self.from_name:
            payload["from"]["name"] = self.from_name
        if self.reply_to:
            payload["reply_to"] = {"email": self.reply_to}

        request = urllib.request.Request(
            "https://api.sendgrid.com/v3/mail/send",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": "Bearer %s" % self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                message_id = response.headers.get("X-Message-Id", "")
                return SendResult(success=response.status in {200, 202}, provider_message_id=message_id)
        except urllib.error.HTTPError as exc:
            message = exc.read().decode("utf-8", errors="replace")
            return SendResult(success=False, error_message=_redact_key(message, self.api_key))
        except Exception as exc:
            return SendResult(success=False, error_message=_redact_key(str(exc), self.api_key))


def create_email_provider(config: dict[str, str]) -> EmailProvider:
    provider = (config.get("email_provider") or "Mock").strip().lower()
    if provider in {"twilio_sendgrid", "sendgrid", "twilio sendgrid"}:
        return SendGridEmailProvider(
            api_key=config.get("email_api_key", "") or os.getenv("SENDGRID_API_KEY", ""),
            from_email=config.get("email_from_email", "") or os.getenv("EMAIL_FROM_EMAIL", ""),
            from_name=config.get("email_from_name", "") or os.getenv("EMAIL_FROM_NAME", ""),
            reply_to=config.get("email_reply_to", "") or os.getenv("EMAIL_REPLY_TO", ""),
        )
    return MockEmailProvider()


def _redact_key(message: str, api_key: str) -> str:
    if api_key:
        return message.replace(api_key, "[REDACTED]")
    return message
