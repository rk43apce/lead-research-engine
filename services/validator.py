from __future__ import annotations

import re

from services.models import EmailDraft, PublicSignal
from services.logger import log_info, log_warning


MAX_EMAIL_WORDS = 120


class LeadValidator:
    def validate_signal(self, signal: PublicSignal, company: str = "", request_id: str = "") -> list[str]:
        warnings: list[str] = []
        if signal.source_url and not signal.source_url.startswith(("http://", "https://")):
            warnings.append("Signal URL is not an HTTP(S) URL.")
            log_warning("Signal validation failed", company=company, step="signal_validation", request_id=request_id, reason="invalid_url", source_url=signal.source_url)
        if signal.source_url and not signal.summary:
            warnings.append("Signal has a source URL but no summary.")
            log_warning("Signal validation failed", company=company, step="signal_validation", request_id=request_id, reason="missing_summary")
        return warnings

    def validate_email(self, draft: EmailDraft, signal: PublicSignal, company: str = "", request_id: str = "") -> EmailDraft:
        warnings = list(draft.warnings)
        email = self._normalize_whitespace(draft.email)
        word_count = len(email.split())
        if word_count > MAX_EMAIL_WORDS:
            email = self._trim_to_words(email, MAX_EMAIL_WORDS)
            warnings.append(f"Email trimmed to {MAX_EMAIL_WORDS} words.")
            log_warning("Word count validation failed", company=company, step="email_validation", request_id=request_id, original_word_count=word_count, trimmed_to=MAX_EMAIL_WORDS)
        hallucination_risk = not signal.source_url and self._claims_recent_signal(email)
        if hallucination_risk:
            warnings.append("Email appeared to claim a recent signal where none was found.")
            log_warning("Email rewritten", company=company, step="email_validation", request_id=request_id, reason="unsupported_recent_signal_claim")
            email = (
                "I could not find a recent verifiable public signal, so I will keep this general. "
                "Financial institutions still face pressure to improve fraud review coverage without adding "
                "PII exposure or disrupting existing workflows. The PreCogs helps augment fraud and risk teams "
                "with AI-driven prevention context while keeping analysts in control. "
                "Would a brief note on fit be useful?"
            )
        has_no_pii_angle = "PII" in email or "personal data" in email.lower()
        if not has_no_pii_angle:
            warnings.append("Email does not mention the no-PII angle.")
            log_warning("Email validation warning", company=company, step="email_validation", request_id=request_id, reason="missing_no_pii_angle")
        log_info("Email validation complete", company=company, step="email_validation", request_id=request_id, word_count=len(email.split()), warnings=len(warnings))
        return EmailDraft(email=email, warnings=warnings)

    def _normalize_whitespace(self, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    def _trim_to_words(self, value: str, max_words: int) -> str:
        words = value.split()
        return " ".join(words[:max_words]).rstrip(".,;:") + "."

    def _claims_recent_signal(self, value: str) -> bool:
        lowered = value.lower()
        return any(phrase in lowered for phrase in ("i saw", "noticed", "recently announced", "your recent"))
