from __future__ import annotations

import logging

from services.llm import GeminiClient
from services.models import EmailDraft, LLMResearchOutput, ResearchContext
from services.prompts import email_prompt, research_prompt
from services.logger import log_step

LOGGER = logging.getLogger(__name__)


class LeadContentGenerator:
    def __init__(self, llm: GeminiClient) -> None:
        self.llm = llm

    async def classify_context(self, context: ResearchContext) -> LLMResearchOutput:
        with log_step(LOGGER, "llm_classification", "classify research context"):
            try:
                data = await self.llm.generate_json(research_prompt(context), operation="gemini_classification")
                services = data.get("services", [])
                if not isinstance(services, list):
                    LOGGER.warning("Classification validation warning field=services reason=not_list")
                    services = []
                output = LLMResearchOutput(
                    institution_type=str(data.get("institution_type") or "Unknown financial institution"),
                    customer_segment=str(data.get("customer_segment") or "Unknown"),
                    services=[str(item) for item in services[:8]],
                    fraud_angle=str(data.get("fraud_angle") or LLMResearchOutput.fallback().fraud_angle),
                )
                LOGGER.info(
                    "Classification parsed institution_type=%s customer_segment=%s service_count=%s fraud_angle_chars=%s",
                    output.institution_type,
                    output.customer_segment,
                    len(output.services),
                    len(output.fraud_angle),
                )
                return output
            except Exception as exc:
                LOGGER.exception("LLM classification failed error=%s", exc)
                return LLMResearchOutput.fallback()

    async def generate_email(
        self,
        context: ResearchContext,
        classification: LLMResearchOutput,
    ) -> EmailDraft:
        with log_step(LOGGER, "llm_email_generation", "generate email draft"):
            try:
                data = await self.llm.generate_json(
                    email_prompt(context, classification.institution_type, classification.fraud_angle),
                    operation="gemini_email_generation",
                )
                email = str(data.get("email") or "")
                LOGGER.info("Email draft generated word_count=%s chars=%s", len(email.split()), len(email))
                LOGGER.debug("Email draft preview=%s", email[:300])
                return EmailDraft(email=email)
            except Exception as exc:
                LOGGER.exception("LLM email generation failed error=%s", exc)
                fallback = self._fallback_email(context, classification)
                LOGGER.warning("Fallback email generated word_count=%s", len(fallback.split()))
                return EmailDraft(email=fallback, warnings=[str(exc)])

    def _fallback_email(self, context: ResearchContext, classification: LLMResearchOutput) -> str:
        company = context.lead.company
        if context.public_signal.source_url:
            opener = f"I saw this recent public signal for {company}: {context.public_signal.summary[:180]}"
        else:
            opener = f"I could not find a recent public signal for {company}, so I will keep this general."
        return (
            f"{opener}\n\n"
            f"For {classification.institution_type}, fraud and risk teams often need more signal without adding "
            "PII exposure or replacing existing workflows. The PreCogs helps augment analysts with AI-driven "
            "fraud prevention context while keeping the review process controlled.\n\n"
            "Would a brief note on where this could fit be useful?"
        )
