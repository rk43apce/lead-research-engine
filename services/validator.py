from services.logger import log_warning
from services.models import EmailDraft, PublicSignal, SignalType


MAX_EMAIL_WORDS = 120


class LeadValidator:
    def validate_signal(self, signal: PublicSignal, company: str = ""):
        warnings = []

        # A signal is only useful if the source URL can be opened and verified.
        if signal.source_url:
            is_valid_url = signal.source_url.startswith(("http://", "https://"))
            if not is_valid_url:
                warnings.append("Signal URL is not an HTTP(S) URL.")
                log_warning(
                    "Signal validation failed",
                    company=company,
                    step="signal_validation",
                    reason="invalid_url",
                )

        # If we have a source URL, we also need a short summary for the CSV/email context.
        if signal.source_url:
            if not signal.summary:
                warnings.append("Signal has a source URL but no summary.")
                log_warning(
                    "Signal validation failed",
                    company=company,
                    step="signal_validation",
                    reason="missing_summary",
                )

        return warnings

    def validate_email(self, draft: EmailDraft, signal: PublicSignal, company: str = "") -> EmailDraft:
        # Step 1: keep any warnings already created by the email generator.
        warnings = list(draft.warnings)

        # Step 2: normalize whitespace so word counting and CSV output are clean.
        email = self._normalize_whitespace(draft.email)

        # Step 3: count words because the assignment requires emails under 120 words.
        word_count = len(email.split())

        # Step 4: trim the email if the LLM returned something too long.
        if word_count > MAX_EMAIL_WORDS:
            email = self._trim_to_words(email, MAX_EMAIL_WORDS)
            warnings.append("Email trimmed to %s words." % MAX_EMAIL_WORDS)
            log_warning(
                "Email trimmed",
                company=company,
                step="email_validation",
                original_word_count=word_count,
            )

        # Step 5: check for hallucination risk.
        # If no source URL exists, the email should not sound like we found recent news.
        has_source_url = bool(signal.source_url and signal.signal_type != SignalType.WEBSITE_CONTEXT)
        claims_recent_signal = self._claims_recent_signal(email)
        hallucination_risk = not has_source_url and claims_recent_signal

        # Step 6: rewrite safely instead of allowing an unsupported claim into output.
        if hallucination_risk:
            warnings.append("Email appeared to claim a recent signal where none was found.")
            log_warning(
                "Unsupported recent signal claim",
                company=company,
                step="email_validation",
            )
            email = (
                "I could not find a recent verifiable public signal, so I will keep this general. "
                "Financial institutions still face pressure to improve fraud review coverage without adding "
                "PII exposure or disrupting existing workflows. The PreCogs helps augment fraud and risk teams "
                "with AI-driven prevention context while keeping analysts in control. "
                "Would a brief note on fit be useful?"
            )

        # Step 7: warn if the email misses The PreCogs no-PII positioning.
        mentions_pii = "PII" in email
        mentions_personal_data = "personal data" in email.lower()
        has_no_pii_angle = mentions_pii or mentions_personal_data

        if not has_no_pii_angle:
            warnings.append("Email does not mention the no-PII angle.")
            log_warning(
                "Missing no-PII angle",
                company=company,
                step="email_validation",
            )

        return EmailDraft(email=email, warnings=warnings)

    def _normalize_whitespace(self, value: str) -> str:
        return " ".join(value.split())

    def _trim_to_words(self, value: str, max_words: int) -> str:
        words = value.split()
        trimmed_email = " ".join(words[:max_words])
        return trimmed_email.rstrip(".,;:") + "."

    def _claims_recent_signal(self, value: str) -> bool:
        lowered_email = value.lower()
        recent_signal_phrases = [
            "i saw",
            "noticed",
            "recently announced",
            "your recent",
        ]

        for phrase in recent_signal_phrases:
            if phrase in lowered_email:
                return True

        return False
