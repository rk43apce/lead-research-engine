from __future__ import annotations

import argparse
import asyncio
import logging
import time
import uuid
from dataclasses import replace
from pathlib import Path

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
from services.logger import elapsed_ms, log_context, log_step
from services.models import EnrichedLead, Lead
from services.researcher import DuckDuckGoResearcher
from services.scraper import AsyncScraper
from services.validator import LeadValidator

LOGGER = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research financial institution leads and generate cold emails.")
    parser.add_argument("--input", type=Path, help="Input CSV path. Defaults to INPUT_CSV or input/leads.csv.")
    parser.add_argument("--output", type=Path, help="Output CSV path. Defaults to OUTPUT_CSV or output/enriched_leads.csv.")
    parser.add_argument("--limit", type=int, help="Optional maximum number of leads to process.")
    return parser.parse_args()


def load_leads(path: Path, limit: int | None = None) -> list[Lead]:
    with log_step(LOGGER, "csv_processing", "load input csv", path=path, limit=limit):
        if not path.exists():
            LOGGER.error("Input CSV missing path=%s", path)
            raise FileNotFoundError(f"Input CSV not found: {path}")
        frame = pd.read_csv(path).fillna("")
        LOGGER.info("CSV file loaded rows=%s columns=%s", len(frame), list(frame.columns))
        if "company" not in frame.columns:
            LOGGER.error("CSV validation failed missing_column=company")
            raise ValueError("Input CSV must contain a 'company' column. Optional column: 'website'.")
        if limit:
            frame = frame.head(limit)
            LOGGER.info("CSV limit applied limit=%s rows_after_limit=%s", limit, len(frame))
        leads: list[Lead] = []
        invalid_rows = 0
        for index, record in enumerate(frame.to_dict(orient="records"), start=1):
            company = str(record.get("company", "")).strip()
            if not company:
                invalid_rows += 1
                LOGGER.warning("Invalid CSV row skipped row_number=%s reason=missing_company", index)
                continue
            website = str(record.get("website", "")).strip() or None
            leads.append(Lead(company=company, website=website))
        LOGGER.info("Leads parsed valid_count=%s invalid_count=%s", len(leads), invalid_rows)
        return leads


async def process_lead(
    lead: Lead,
    researcher: DuckDuckGoResearcher,
    generator: LeadContentGenerator,
    validator: LeadValidator,
    semaphore: asyncio.Semaphore,
) -> EnrichedLead:
    async with semaphore:
        trace_id = uuid.uuid4().hex[:12]
        with log_context(trace_id=trace_id, company=lead.company):
            started_at = time.perf_counter()
            with log_step(LOGGER, "company_processing", "process company", website=lead.website):
                context = await researcher.research(lead)
                classification = await generator.classify_context(context)
                draft = await generator.generate_email(context, classification)
                draft = validator.validate_email(draft, context.public_signal)
                signal_warnings = validator.validate_signal(context.public_signal)
                warnings = [
                    *context.errors,
                    *signal_warnings,
                    *draft.warnings,
                ]
                LOGGER.info(
                    "Company processing complete duration_ms=%s warnings=%s has_signal=%s",
                    elapsed_ms(started_at),
                    len(warnings),
                    bool(context.public_signal.source_url),
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


async def run_pipeline(settings: Settings, limit: int | None = None) -> list[EnrichedLead]:
    leads = load_leads(settings.input_csv, limit=limit)
    LOGGER.info("Loaded leads count=%s path=%s", len(leads), settings.input_csv)
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
    semaphore = asyncio.Semaphore(settings.max_concurrency)

    tasks = [process_lead(lead, researcher, generator, validator, semaphore) for lead in leads]
    return await asyncio.gather(*tasks)


def write_output(path: Path, rows: list[EnrichedLead]) -> None:
    with log_step(LOGGER, "output_write", "write enriched csv", path=path):
        path.parent.mkdir(parents=True, exist_ok=True)
        output_rows = [row.to_csv_row() for row in rows]
        pd.DataFrame(output_rows).to_csv(path, index=False)
        for row in rows:
            LOGGER.debug(
                "CSV row prepared company=%s has_signal=%s email_word_count=%s",
                row.company,
                bool(row.source_url),
                len(row.email.split()),
            )
        warnings = {row.company: row.warnings for row in rows if row.warnings}
        if warnings:
            LOGGER.warning("Completed with warnings companies=%s warning_count=%s", list(warnings.keys()), len(warnings))
        LOGGER.info("Output CSV written path=%s processed_count=%s", path, len(rows))


def main() -> None:
    load_dotenv()
    args = parse_args()
    settings = Settings.from_env()
    if args.input:
        settings = replace(settings, input_csv=args.input)
    if args.output:
        settings = replace(settings, output_csv=args.output)
    configure_logging(settings.log_level, log_file=settings.log_file)
    if not settings.gemini_api_key:
        LOGGER.warning("GEMINI_API_KEY is not set. LLM fields will use conservative fallbacks.")
    rows = asyncio.run(run_pipeline(settings, limit=args.limit))
    write_output(settings.output_csv, rows)


if __name__ == "__main__":
    main()
