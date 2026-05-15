import asyncio

from services.config import Settings
from services.logger import configure_logging, log_info
from services.output import write_output
from services.pipeline import run_pipeline


def main() -> None:
    # 1. Load environment variables and app settings.
    settings = Settings.from_env()

    # breakpoint()  # debugger stops here

    input_path = settings.input_csv
    output_path = settings.output_csv

    # 2. Configure logging before the pipeline starts.
    configure_logging(settings.log_level, log_file=settings.log_file)

    print("AI lead research pipeline is running...")

    if not settings.gemini_api_key:
        log_info("GEMINI_API_KEY is not set. LLM fields will use conservative fallbacks.", step="startup")
        print("Warning: GEMINI_API_KEY is not set. Fallback content may be used.")

    # 3. Run the async pipeline and write the final CSV.
    log_info("Starting lead research pipeline", step="startup", input=input_path, output=output_path)
    rows = asyncio.run(run_pipeline(settings, input_path=input_path))
    write_output(output_path, rows)
    log_info("Lead research pipeline completed", step="shutdown", total_rows=len(rows))
    print("Done. Processed %s leads." % len(rows))
    print("Output CSV: %s" % output_path)


if __name__ == "__main__":
    main()
