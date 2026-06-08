import re

from web.core.logger import log_warning
from web.core.models import EmailDraft, PublicSignal, SignalType


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

        # Step 2: normalize whitespace while preserving Subject + body structure.
        email = self._normalize_email_format(draft.email, company=company)

        # Step 3: count body words because the assignment requires the email body under 120 words.
        subject, body = self._split_subject_body(email)
        word_count = len(body.split())

        # Step 4: trim the email if the LLM returned something too long.
        if word_count > MAX_EMAIL_WORDS:
            body = self._trim_to_words(body, MAX_EMAIL_WORDS)
            email = self._join_subject_body(subject, body)
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
            body = (
                "Hello,\n\n"
                "I could not find a recent verifiable public signal, so I will keep this general.\n\n"
                "Financial institutions still face pressure to improve fraud review coverage without adding "
                "PII exposure or disrupting existing workflows.\n\n"
                "The PreCogs helps augment fraud and risk teams with AI-driven prevention context while keeping "
                "analysts in control.\n\n"
                "Would a brief note on fit be useful?\n\nBest,"
            )
            email = self._join_subject_body(subject or self._fallback_subject(company), body)

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

    def _normalize_email_format(self, value: str, company: str = "") -> str:
        subject, body = self._split_subject_body(value)
        subject = subject or self._fallback_subject(company)
        body = self._normalize_body(body)
        return self._join_subject_body(subject, body)

    def _split_subject_body(self, value: str):
        cleaned = str(value or "").strip()
        if not cleaned:
            return "", ""

        lines = cleaned.splitlines()
        if lines and lines[0].lower().startswith("subject:"):
            first_line = lines[0].split(":", 1)[1].strip()
            body = "\n".join(lines[1:]).strip()
            if not body:
                subject, inline_body = self._split_inline_subject(first_line)
                return subject, inline_body
            subject = first_line
            return subject, body

        if cleaned.lower().startswith("subject:"):
            without_label = cleaned.split(":", 1)[1].strip()
            return self._split_inline_subject(without_label)

        return "", cleaned

    def _split_inline_subject(self, value: str):
        for marker in [" Hi,", " Hi ", " Hello,", " Hello "]:
            if marker in value:
                subject, body = value.split(marker, 1)
                greeting = marker.strip()
                return subject.strip(), ("%s%s" % (greeting, body)).strip()
        return value, ""

    def _join_subject_body(self, subject: str, body: str) -> str:
        return "Subject: %s\n\n%s" % (subject.strip(), body.strip())

    def _normalize_body(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""

        paragraphs = [self._collapse_spaces(part) for part in re.split(r"\n\s*\n", text) if part.strip()]
        if len(paragraphs) > 1:
            return "\n\n".join(paragraphs)

        return "\n\n".join(self._paragraphize_dense_body(self._collapse_spaces(text)))

    def _paragraphize_dense_body(self, text: str) -> list[str]:
        sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
        if len(sentences) <= 2:
            return [text]

        signoff = ""
        if sentences and sentences[-1].lower().rstrip(".") in {"best", "best regards", "regards", "thanks", "thank you"}:
            signoff = sentences.pop()

        cta = ""
        if sentences and self._looks_like_cta(sentences[-1]):
            cta = sentences.pop()

        paragraphs = []
        if sentences:
            paragraphs.append(sentences[0])

        if len(sentences) >= 2:
            paragraphs.append(sentences[1])

        middle = sentences[2:]
        if middle:
            paragraphs.append(" ".join(middle))

        if cta:
            paragraphs.append(cta)

        if signoff:
            paragraphs.append(signoff)

        return paragraphs

    def _looks_like_cta(self, sentence: str) -> bool:
        lowered = sentence.lower()
        return lowered.startswith(("would you", "are you open", "could we", "can we", "open to"))

    def _collapse_spaces(self, value: str) -> str:
        return " ".join(str(value or "").split())

    def _fallback_subject(self, company: str) -> str:
        subject = "Fraud review support"
        if company:
            subject = "Fraud review support for %s" % company
        words = subject.split()
        if len(words) > 8:
            subject = " ".join(words[:8])
        return subject.rstrip(".,;:")

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
