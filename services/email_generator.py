import time

from services.llm import OpenAIClient
from services.logger import log_error, log_info, log_timing, log_warning
from services.models import EmailDraft, LLMResearchOutput, ResearchContext
from services.prompts import email_prompt, research_prompt


class LeadContentGenerator:
    """Turns researched company context into LLM-generated output.

    This class has one job: ask OpenAI for a classification and an email draft.
    If OpenAI fails, it returns a safe fallback so the CSV pipeline can continue.
    """

    def __init__(self, llm: OpenAIClient) -> None:
        self.llm = llm

    async def classify_context(self, context: ResearchContext) -> LLMResearchOutput:
        company = context.lead.company
        started_at = time.perf_counter()

        try:
            # OpenAI only receives grounded context prepared by the research layer.
            prompt = research_prompt(context)
            log_info(
                "OpenAI classification input prepared",
                company=company,
                step="llm_input",
                homepage_url=context.homepage_url or "-",
                scraped_text_chars=len(context.about_text),
                prompt_chars=len(prompt),
                has_signal=bool(context.public_signal.source_url),
            )
            data = await self.llm.generate_json(
                prompt,
                operation="openai_classification",
                company=company,
            )

            institution_type = str(data.get("institution_type") or "Unknown financial institution")
            customer_segment = str(data.get("customer_segment") or "Unknown")
            services = data.get("services") or []
            if not isinstance(services, list):
                services = []

            normalized_services = [str(service) for service in services[:8]]
            fraud_angle = self._normalize_fraud_angle(
                data.get("fraud_angle"),
                context=context,
                institution_type=institution_type,
                services=normalized_services,
            )
            fraud_angle_source = self._fraud_angle_source(data.get("fraud_angle"))

            result = LLMResearchOutput(
                institution_type=institution_type,
                customer_segment=customer_segment,
                services=normalized_services,
                fraud_angle=fraud_angle,
            )

            log_info(
                "Classification completed",
                company=company,
                step="llm_classification",
                institution_type=institution_type,
                services_count=len(normalized_services),
                fraud_angle_source=fraud_angle_source,
                fraud_angle=fraud_angle,
                duration_ms=log_timing(started_at),
            )
            print("Classification: %s -> %s, fraud_angle=%s" % (company, institution_type, fraud_angle_source))

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
                "OpenAI email input prepared",
                company=company,
                step="llm_input",
                institution_type=classification.institution_type,
                fraud_angle=classification.fraud_angle,
                prompt_chars=len(prompt),
                has_signal=bool(context.public_signal.source_url),
            )
            data = await self.llm.generate_json(
                prompt,
                operation="openai_email_generation",
                company=company,
            )
            email = str(data.get("email") or "")

            log_info(
                "Email generation completed",
                company=company,
                step="llm_email_generation",
                email_chars=len(email),
                duration_ms=log_timing(started_at),
            )
            print("Email: generated for %s" % company)

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

        if signal.source_url and signal.signal_type.value != "website_context":
            opener = "I saw a public signal for %s related to %s." % (company, signal.signal_type.value)
        elif signal.source_url:
            opener = "I reviewed the public website context for %s, but could not find a recent verifiable public signal." % company
        else:
            opener = "I could not find a recent verifiable public signal for %s, so I will keep this general." % company

        return (
            "%s Fraud and risk teams often need more review coverage without adding PII exposure "
            "or replacing existing workflows. The PreCogs helps augment analysts with AI-driven "
            "fraud prevention context while keeping teams in control. Would a brief note on fit be useful?"
        ) % opener

    def _normalize_fraud_angle(
        self,
        value,
        context: ResearchContext,
        institution_type: str,
        services: list[str],
    ) -> str:
        fraud_angle = str(value or "").strip()
        if fraud_angle and fraud_angle.lower() not in {"unknown", "n/a", "none", "null", "-"}:
            return fraud_angle

        return self._derive_fraud_angle(context, institution_type, services)

    def _fraud_angle_source(self, value) -> str:
        fraud_angle = str(value or "").strip()
        if fraud_angle and fraud_angle.lower() not in {"unknown", "n/a", "none", "null", "-"}:
            return "llm"

        return "derived_fallback"

    def _derive_fraud_angle(
        self,
        context: ResearchContext,
        institution_type: str,
        services: list[str],
    ) -> str:
        signal_type = context.public_signal.signal_type.value
        service_text = " ".join(services).lower()
        institution_text = institution_type.lower()
        context_text = " ".join(
            [
                institution_text,
                service_text,
                context.public_signal.summary.lower(),
                signal_type,
            ]
        )

        if "payment" in context_text or "ach" in context_text or "wire" in context_text or "card" in context_text:
            return "Payment activity can create pressure to monitor ACH, wire, card, and account takeover risk without adding PII exposure."

        if "partnership" in context_text or "partner" in context_text or "integrat" in context_text:
            return "Partnerships and integrations can expand fraud review complexity across onboarding, transaction monitoring, and third-party workflows."

        if "expansion" in context_text or "launch" in context_text or "growth" in context_text:
            return "Growth or new product activity can increase fraud-review volume and make consistent risk triage harder for existing teams."

        if "hiring" in context_text or "job" in context_text or "career" in context_text:
            return "Hiring or team growth can indicate rising operational demand for fraud review coverage and analyst efficiency."

        if "credit union" in institution_text:
            return "Member-facing digital banking can expose credit unions to account takeover, identity fraud, and payment-risk review needs."

        if "bank" in institution_text:
            return "Consumer and business banking workflows often need stronger fraud triage across account access, payments, and onboarding."

        if "fintech" in institution_text:
            return "Digital financial products can create fraud pressure around onboarding, account misuse, and transaction monitoring."

        return LLMResearchOutput.fallback().fraud_angle
