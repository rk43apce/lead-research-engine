import argparse
import asyncio
import os
from pathlib import Path
from typing import Optional

from services.lead_sourcing import LeadSourceGenerator
from services.logger import configure_logging, log_info

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False


def main() -> None:
    load_dotenv()
    configure_logging(os.getenv("LOG_LEVEL", "INFO"), log_file=os.getenv("LOG_FILE", "logs/app.log"))

    args = parse_args(default_output=Path(os.getenv("INPUT_CSV", "input/leads.csv")))
    banks, credit_unions = _split_sources(args.total, args.banks, args.credit_unions)

    generator = LeadSourceGenerator(
        timeout_seconds=args.timeout_seconds,
        email_pages_per_company=args.email_pages_per_company if args.include_management_emails else 0,
    )

    print("Generating input leads...")
    print("Source: %s" % args.source)
    print("Target: %s total (%s banks, %s credit unions)" % (args.total, banks, credit_unions))
    print("Website required: yes")
    if args.include_management_emails:
        print("Optional email scraping enabled: %s pages/company" % args.email_pages_per_company)

    if args.source == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required when --source openai is used.")

        leads = asyncio.run(
            generator.generate_with_openai(
                output_path=args.output,
                api_key=api_key,
                model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
                total=args.total,
                institution_type=args.institution_type,
                batch_size=args.openai_batch_size,
                max_attempts=args.openai_max_attempts,
            )
        )
    else:
        leads = asyncio.run(
            generator.generate(
                output_path=args.output,
                total=args.total,
                banks=banks,
                credit_unions=credit_unions,
            )
        )

    log_info("Lead sourcing completed", step="lead_sourcing", count=len(leads), output=args.output)
    print("Done. Generated %s leads." % len(leads))
    print("Input CSV: %s" % args.output)


def parse_args(default_output: Path):
    parser = argparse.ArgumentParser(
        description="Generate an input CSV of financial institution leads with website URLs."
    )
    parser.add_argument("--source", choices=["fdic", "openai"], default="fdic")
    parser.add_argument("--output", type=Path, default=default_output)
    parser.add_argument("--total", type=int, default=1000)
    parser.add_argument("--banks", type=int, default=None)
    parser.add_argument("--credit-unions", type=int, default=0)
    parser.add_argument(
        "--institution-type",
        choices=["community_bank", "credit_union", "both"],
        default="both",
        help="OpenAI source only: which institution types to request.",
    )
    parser.add_argument("--openai-batch-size", type=int, default=50)
    parser.add_argument("--openai-max-attempts", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=float, default=25)
    parser.add_argument(
        "--include-management-emails",
        action="store_true",
        help="Scrape each institution website for non-generic email addresses.",
    )
    parser.add_argument(
        "--email-pages-per-company",
        type=int,
        default=3,
        help="Maximum website pages to check per company when email scraping is enabled.",
    )
    return parser.parse_args()


def _split_sources(total: int, banks: Optional[int], credit_unions: Optional[int]):
    if total < 1:
        raise ValueError("--total must be at least 1")

    if banks is None and credit_unions is None:
        banks = total
        credit_unions = 0
    elif banks is None:
        credit_unions = max(credit_unions or 0, 0)
        banks = max(total - credit_unions, 0)
    elif credit_unions is None:
        banks = max(banks, 0)
        credit_unions = max(total - banks, 0)

    return min(banks, total), min(credit_unions, total)


if __name__ == "__main__":
    main()
