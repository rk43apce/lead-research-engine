from __future__ import annotations

import re
import logging

from services.models import EmailDraft, PublicSignal
from services.logger import log_context


MAX_EMAIL_WORDS = 120
LOGGER = logging.getLogger(__name__)


class LeadValidator:
    def validate_signal(self, signal: PublicSignal) -> list[str]:
        with log_context(step="signal_validation"):
            warnings: list[str] = []
            LOGGER.info(
                "Signal validation start has_url=%s signal_type=%s confidence=%s",
                bool(signal.source_url),
                signal.signal_type.value,
                signal.confidence,
            )
            if signal.source_url and not signal.source_url.startswith(("http://", "https://")):
                warnings.append("Signal URL is not an HTTP(S) URL.")
                LOGGER.warning("Signal validation failed reason=invalid_url source_url=%s", signal.source_url)
            if signal.source_url and not signal.summary:
                warnings.append("Signal has a source URL but no summary.")
                LOGGER.warning("Signal validation failed reason=missing_summary")
            LOGGER.info("Signal validation complete warning_count=%s", len(warnings))
            return warnings

    def validate_email(self, draft: EmailDraft, signal: PublicSignal) -> EmailDraft:
        with log_context(step="email_validation"):
            warnings = list(draft.warnings)
            email = self._normalize_whitespace(draft.email)
            word_count = len(email.split())
            LOGGER.info(
                "Email validation start word_count=%s max_words=%s has_signal_url=%s upstream_warnings=%s",
                word_count,
                MAX_EMAIL_WORDS,
                bool(signal.source_url),
                len(draft.warnings),
            )
            if word_count > MAX_EMAIL_WORDS:
                email = self._trim_to_words(email, MAX_EMAIL_WORDS)
                warnings.append(f"Email trimmed to {MAX_EMAIL_WORDS} words.")
                LOGGER.warning("Word count validation failed original_word_count=%s trimmed_to=%s", word_count, MAX_EMAIL_WORDS)
            hallucination_risk = not signal.source_url and self._claims_recent_signal(email)
            LOGGER.info("Hallucination check complete risk_detected=%s", hallucination_risk)
            if hallucination_risk:
                warnings.append("Email appeared to claim a recent signal where none was found.")
                LOGGER.warning("Email rewritten reason=unsupported_recent_signal_claim")
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
                LOGGER.warning("Email validation warning reason=missing_no_pii_angle")
            LOGGER.info(
                "Email validation complete final_word_count=%s warning_count=%s has_no_pii_angle=%s",
                len(email.split()),
                len(warnings),
                has_no_pii_angle,
            )
            return EmailDraft(email=email, warnings=warnings)

    def _normalize_whitespace(self, value: str) -> str:
        return re.sub(r"\s+", " ", value).strip()

    def _trim_to_words(self, value: str, max_words: int) -> str:
        words = value.split()
        return " ".join(words[:max_words]).rstrip(".,;:") + "."

    def _claims_recent_signal(self, value: str) -> bool:
        lowered = value.lower()
        return any(phrase in lowered for phrase in ("i saw", "noticed", "recently announced", "your recent"))
