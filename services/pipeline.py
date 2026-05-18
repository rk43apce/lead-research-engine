import asyncio
import time
from pathlib import Path

from services.config import Settings
from services.email_generator import LeadContentGenerator
from services.lead import load_leads
from services.llm import OpenAIClient
from services.logger import log_error, log_timing
from services.models import EnrichedLead, Lead
from services.researcher import CompanyResearcher
from services.scraper import AsyncScraper
from services.validator import LeadValidator


async def process_lead(
    lead: Lead,
    researcher: CompanyResearcher,
    generator: LeadContentGenerator,
    validator: LeadValidator,
    semaphore: asyncio.Semaphore,
):
    """Process one company from research to validated email output."""
    async with semaphore:
        company = lead.company
        started_at = time.perf_counter()

        try:
            # Step 1: collect grounded public context and source-backed signal.
            print("\nProcessing lead: %s" % company)
            context = await researcher.research(lead)

            # Step 2: ask OpenAI to classify the company using only grounded context.
            classification = await generator.classify_context(context)

            # Step 3: generate a personalized email draft.
            draft = await generator.generate_email(context, classification)

            # Step 4: validate the email and signal before writing CSV output.
            draft = validator.validate_email(draft, context.public_signal, company=company)
            validator.validate_signal(context.public_signal, company=company)

            print("Completed lead: %s" % company)
            return EnrichedLead(
                company=lead.company,
                institution_type=classification.institution_type,
                fraud_angle=classification.fraud_angle,
                signal=context.public_signal.summary,
                source_url=context.public_signal.source_url,
                email=draft.email,
            )

        except Exception as exc:
            log_error(
                "Company processing failed",
                company=company,
                step="company_processing",
                duration_ms=log_timing(started_at),
                error=exc,
            )
            return EnrichedLead(
                company=lead.company,
                institution_type="Processing failed",
                fraud_angle="Processing failed before a safe fraud/risk angle could be generated.",
                signal="No signal generated because processing failed.",
                source_url="",
                email="",
            )


async def run_pipeline(settings: Settings, input_path: Path):
    """Create the services and run all leads concurrently."""
    leads = load_leads(input_path)
    print("Pipeline: processing %s leads with max concurrency %s" % (len(leads), settings.max_concurrency))

    # Services are created once and shared across all lead tasks.
    scraper = AsyncScraper(timeout_seconds=settings.request_timeout_seconds)
    researcher = CompanyResearcher(scraper=scraper)
    llm = OpenAIClient(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        timeout_seconds=settings.request_timeout_seconds + 18,
    )
    generator = LeadContentGenerator(llm)
    validator = LeadValidator()

    # The semaphore keeps async processing fast without overwhelming websites or APIs.
    semaphore = asyncio.Semaphore(settings.max_concurrency)

    tasks = []
    for lead in leads:
        task = process_lead(lead, researcher, generator, validator, semaphore)
        tasks.append(task)

    return await asyncio.gather(*tasks)
