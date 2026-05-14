import argparse
import asyncio
import time
import uuid
from pathlib import Path
from typing import List, Optional

import pandas as pd

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # Allows --help and explicit environment variables before dependencies are installed.
    def load_dotenv() -> bool:
        return False

from services.config import Settings
from services.email_generator import LeadContentGenerator
from services.llm import GeminiClient
from services.logging_config import configure_logging
from services.logger import log_error, log_info, log_timing, log_warning
from services.models import EnrichedLead, Lead
from services.researcher import DuckDuckGoResearcher
from services.scraper import AsyncScraper
from services.validator import LeadValidator

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research financial institution leads and generate cold emails.")
    parser.add_argument("--input", type=Path, help="Input CSV path. Defaults to INPUT_CSV or input/leads.csv.")
    parser.add_argument("--output", type=Path, help="Output CSV path. Defaults to OUTPUT_CSV or output/enriched_leads.csv.")
    parser.add_argument("--limit", type=int, help="Optional maximum number of leads to process.")
    return parser.parse_args()


def load_leads(path: Path, limit: Optional[int] = None) -> List[Lead]:
    """Read the input CSV and convert each valid row into a Lead object."""
    started_at = time.perf_counter()
    if not path.exists():
        log_error("Input CSV missing", step="csv_processing", path=path)
        raise FileNotFoundError("Input CSV not found: %s" % path)

    frame = pd.read_csv(path).fillna("")
    if "company" not in frame.columns:
        log_error("CSV validation failed", step="csv_processing", missing_column="company")
        raise ValueError("Input CSV must contain a 'company' column. Optional column: 'website'.")

    if limit:
        frame = frame.head(limit)

    leads: List[Lead] = []
    invalid_rows = 0

    for index, record in enumerate(frame.to_dict(orient="records"), start=1):
        company = str(record.get("company", "")).strip()
        website = str(record.get("website", "")).strip() or None

        if not company:
            invalid_rows += 1
            log_warning("Invalid CSV row skipped", step="csv_processing", row_number=index, reason="missing_company")
            continue

        request_id = uuid.uuid4().hex[:12]
        leads.append(Lead(company=company, website=website, request_id=request_id))

    log_info(
        "Finished loading leads",
        step="csv_processing",
        valid_count=len(leads),
        invalid_count=invalid_rows,
        duration_ms=log_timing(started_at),
    )
    return leads


async def process_lead(
    lead: Lead,
    researcher: DuckDuckGoResearcher,
    generator: LeadContentGenerator,
    validator: LeadValidator,
    semaphore: asyncio.Semaphore,
) -> EnrichedLead:
    """Process one company from research to validated email output."""
    async with semaphore:
        company = lead.company
        request_id = lead.request_id
        started_at = time.perf_counter()

        log_info("Starting company processing", company=company, step="company_processing", request_id=request_id, website=lead.website)

        try:
            # Step 1: collect grounded public context and source-backed signal.
            context = await researcher.research(lead)

            # Step 2: ask Gemini to classify the company using only grounded context.
            classification = await generator.classify_context(context)

            # Step 3: generate a personalized email draft.
            draft = await generator.generate_email(context, classification)

            # Step 4: validate the email and signal before writing CSV output.
            draft = validator.validate_email(draft, context.public_signal, company=company, request_id=request_id)
            signal_warnings = validator.validate_signal(context.public_signal, company=company, request_id=request_id)

            warnings = context.errors + signal_warnings + draft.warnings

            log_info(
                "Finished company processing",
                company=company,
                step="company_processing",
                request_id=request_id,
                duration_ms=log_timing(started_at),
                warnings=len(warnings),
                has_signal=bool(context.public_signal.source_url),
            )

            return EnrichedLead(
                company=lead.company,
                institution_type=classification.institution_type,
                fraud_angle=classification.fraud_angle,
                signal=context.public_signal.summary,
                source_url=context.public_signal.source_url,
                email=draft.email,
                warnings=warnings,
            )
        except Exception:
            log_error(
                "Company processing failed",
                company=company,
                step="company_processing",
                request_id=request_id,
                duration_ms=log_timing(started_at),
                exc_info=True,
            )
            raise


async def run_pipeline(settings: Settings, input_path: Path, limit: Optional[int] = None) -> List[EnrichedLead]:
    """Create the services and run all leads concurrently."""
    leads = load_leads(input_path, limit=limit)
    # Services are created once and shared across all lead tasks.
    scraper = AsyncScraper(timeout_seconds=settings.request_timeout_seconds)
    researcher = DuckDuckGoResearcher(
        scraper=scraper,
        timeout_seconds=settings.request_timeout_seconds,
        results_per_query=settings.search_results_per_query,
    )
    llm = GeminiClient(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
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


def write_output(path: Path, rows: List[EnrichedLead]) -> None:
    """Write the final enriched CSV."""
    started_at = time.perf_counter()
    log_info("Writing output CSV", step="output_write", path=path, row_count=len(rows))

    path.parent.mkdir(parents=True, exist_ok=True)

    output_rows = []
    for row in rows:
        output_rows.append(row.to_csv_row())

    pd.DataFrame(output_rows).to_csv(path, index=False)

    companies_with_warnings = []
    for row in rows:
        if row.warnings:
            companies_with_warnings.append(row.company)

    if companies_with_warnings:
        log_warning(
            "Completed with warnings",
            step="output_write",
            companies=companies_with_warnings,
            warning_count=len(companies_with_warnings),
        )

    log_info("Output CSV written", step="output_write", path=path, processed_count=len(rows), duration_ms=log_timing(started_at))


def main() -> None:
    # 1. Load environment variables and CLI options.
    load_dotenv()
    args = parse_args()
    settings = Settings.from_env()

    input_path = args.input or settings.input_csv
    output_path = args.output or settings.output_csv

    # 2. Configure logging before the pipeline starts.
    configure_logging(settings.log_level, log_file=settings.log_file)

    print("AI lead research pipeline is running...")
    print("Detailed logs: %s" % settings.log_file)

    if not settings.gemini_api_key:
        log_warning("GEMINI_API_KEY is not set. LLM fields will use conservative fallbacks.", step="startup")
        print("Warning: GEMINI_API_KEY is not set. Fallback content may be used.")

    # 3. Run the async pipeline and write the final CSV.
    log_info("Starting lead research pipeline", step="startup", input=input_path, output=output_path)
    rows = asyncio.run(run_pipeline(settings, input_path=input_path, limit=args.limit))
    write_output(output_path, rows)
    log_info("Lead research pipeline completed", step="shutdown", total_rows=len(rows))
    print("Done. Processed %s leads." % len(rows))
    print("Output CSV: %s" % output_path)


if __name__ == "__main__":
    main()
