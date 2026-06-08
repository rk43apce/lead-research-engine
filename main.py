import asyncio
import argparse
from datetime import datetime

from web.core.config import Settings
from web.core.logger import configure_logging, log_info
from web.core.output import write_output
from web.core.pipeline import run_pipeline


def main() -> None:
    args = _parse_args()

    # 1. Load environment variables and app settings.
    settings = Settings.from_env()

    # breakpoint()  # debugger stops here

    input_path = settings.input_csv
    output_path = _timestamped_output_path(settings.output_csv)

    # 2. Configure logging before the pipeline starts.
    configure_logging(settings.log_level, log_file=settings.log_file)

    print("AI lead research pipeline is running...")

    missing_key_name = _missing_llm_key_name(settings)
    if missing_key_name:
        log_info("%s is not set. LLM fields will use conservative fallbacks." % missing_key_name, step="startup")
        print("Warning: %s is not set. Fallback content may be used." % missing_key_name)

    # 3. Run the async pipeline and write the final CSV.
    log_info("Starting lead research pipeline", step="startup", input=input_path, output=output_path)
    rows = asyncio.run(run_pipeline(settings, input_path=input_path, limit=args.limit))
    write_output(output_path, rows)
    log_info("Lead research pipeline completed", step="shutdown", total_rows=len(rows))
    print("Done. Processed %s leads." % len(rows))
    print("Output CSV: %s" % output_path)


def _parse_args():
    parser = argparse.ArgumentParser(description="Run the AI lead research pipeline.")
    parser.add_argument("--limit", type=int, default=None, help="Optional number of leads to process.")
    return parser.parse_args()


def _timestamped_output_path(path):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return path.with_name("%s_%s%s" % (path.stem, timestamp, path.suffix))


def _missing_llm_key_name(settings):
    provider = (settings.llm_provider or "openai").lower()
    if provider == "openai" and not settings.openai_api_key:
        return "OPENAI_API_KEY"
    if provider == "anthropic" and not settings.anthropic_api_key:
        return "ANTHROPIC_API_KEY"
    if provider == "gemini" and not settings.gemini_api_key:
        return "GEMINI_API_KEY"
    if provider == "groq" and not settings.groq_api_key:
        return "GROQ_API_KEY"
    return ""


if __name__ == "__main__":
    main()
