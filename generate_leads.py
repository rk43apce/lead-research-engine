import argparse
import asyncio
import os
from pathlib import Path

from web.core.lead_sourcing import LeadSourceGenerator
from web.core.logger import configure_logging, log_info

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False


def main() -> None:
    load_dotenv()
    configure_logging(os.getenv("LOG_LEVEL", "INFO"), log_file=os.getenv("LOG_FILE", "logs/app.log"))

    args = parse_args(default_output=Path(os.getenv("INPUT_CSV", "input/leads.csv")))
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for lead generation.")

    generator = LeadSourceGenerator(timeout_seconds=args.timeout_seconds)

    print("Generating input leads with LLM...")
    print("Target: %s total" % args.total)
    print("Institution type: %s" % args.institution_type)
    print("Website required: yes")
    print("Output: %s" % args.output)

    leads = asyncio.run(
        generator.generate(
            output_path=args.output,
            api_key=api_key,
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            total=args.total,
            institution_type=args.institution_type,
            batch_size=args.batch_size,
            max_attempts=args.max_attempts,
        )
    )

    log_info("Lead sourcing completed", step="lead_sourcing", count=len(leads), output=args.output)
    print("Done. Generated %s leads." % len(leads))
    print("Input CSV: %s" % args.output)


def parse_args(default_output: Path):
    parser = argparse.ArgumentParser(
        description="Generate an input CSV of financial institution leads with website URLs using an LLM."
    )
    parser.add_argument("--output", type=Path, default=default_output)
    parser.add_argument("--total", type=int, default=1000)
    parser.add_argument(
        "--institution-type",
        choices=["community_bank", "credit_union", "both"],
        default="both",
        help="Which institution types to request.",
    )
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--max-attempts", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=float, default=25)
    return parser.parse_args()


if __name__ == "__main__":
    main()
