import time

from services.llm import GeminiClient
from services.logger import log_error, log_info, log_timing, log_warning
from services.models import EmailDraft, LLMResearchOutput, ResearchContext
from services.prompts import email_prompt, research_prompt


class LeadContentGenerator:
    """Turns researched company context into LLM-generated output.

    This class has one job: ask Gemini for a classification and an email draft.
    If Gemini fails, it returns a safe fallback so the CSV pipeline can continue.
    """

    def __init__(self, llm: GeminiClient) -> None:
        self.llm = llm

    async def classify_context(self, context: ResearchContext) -> LLMResearchOutput:
        company = context.lead.company
        started_at = time.perf_counter()

        try:
            # Gemini only receives grounded context prepared by the research layer.
            prompt = research_prompt(context)
            log_info(
                "Gemini classification input prepared",
                company=company,
                step="llm_input",
                homepage_url=context.homepage_url or "-",
                scraped_text_chars=len(context.about_text),
                prompt_chars=len(prompt),
                has_signal=bool(context.public_signal.source_url),
            )
            data = await self.llm.generate_json(
                prompt,
                operation="gemini_classification",
                company=company,
            )

            institution_type = str(data.get("institution_type") or "Unknown financial institution")
            customer_segment = str(data.get("customer_segment") or "Unknown")
            services = data.get("services") or []
            fraud_angle = str(data.get("fraud_angle") or LLMResearchOutput.fallback().fraud_angle)

            if not isinstance(services, list):
                services = []

            result = LLMResearchOutput(
                institution_type=institution_type,
                customer_segment=customer_segment,
                services=[str(service) for service in services[:8]],
                fraud_angle=fraud_angle,
            )

            return result

        except Exception as exc:
            log_error(
                "Classification failed",
                company=company,
                step="llm_classification",
                duration_ms=log_timing(started_at),
                error=exc,
            )
            log_warning("Classification fallback used", company=company, step="llm_classification")
            return LLMResearchOutput.fallback()

    async def generate_email(
        self,
        context: ResearchContext,
        classification: LLMResearchOutput,
    ) -> EmailDraft:
        company = context.lead.company
        started_at = time.perf_counter()

        try:
            # The prompt includes whether a source-backed signal exists.
            # This helps avoid unsupported "recent news" claims.
            prompt = email_prompt(context, classification.institution_type, classification.fraud_angle)
            log_info(
                "Gemini email input prepared",
                company=company,
                step="llm_input",
                institution_type=classification.institution_type,
                fraud_angle=classification.fraud_angle,
                prompt_chars=len(prompt),
                has_signal=bool(context.public_signal.source_url),
            )
            data = await self.llm.generate_json(
                prompt,
                operation="gemini_email_generation",
                company=company,
            )
            email = str(data.get("email") or "")

            return EmailDraft(email=email)

        except Exception as exc:
            log_error(
                "Email generation failed",
                company=company,
                step="llm_email_generation",
                duration_ms=log_timing(started_at),
                error=exc,
            )

            # Fallback exists so one LLM failure does not break the full batch.
            fallback_email = self._fallback_email(context, classification)
            log_warning(
                "Email fallback used",
                company=company,
                step="llm_email_generation",
                word_count=len(fallback_email.split()),
                fallback_used=True,
            )
            return EmailDraft(email=fallback_email, warnings=[str(exc)])

    def _fallback_email(self, context: ResearchContext, classification: LLMResearchOutput) -> str:
        company = context.lead.company
        signal = context.public_signal

        if signal.source_url:
            opener = "I saw a public signal for %s related to %s." % (company, signal.signal_type.value)
        else:
            opener = "I could not find a recent verifiable public signal for %s, so I will keep this general." % company

        return (
            "%s Fraud and risk teams often need more review coverage without adding PII exposure "
            "or replacing existing workflows. The PreCogs helps augment analysts with AI-driven "
            "fraud prevention context while keeping teams in control. Would a brief note on fit be useful?"
        ) % opener
