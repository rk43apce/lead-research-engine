import asyncio
import time
from pathlib import Path
from typing import List

import pandas as pd

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False

from services.config import Settings
from services.logger import configure_logging, log_info, log_timing
from services.models import EnrichedLead
from services.pipeline import run_pipeline


def write_output(path: Path, rows: List[EnrichedLead]):
    """Write the final enriched CSV."""
    started_at = time.perf_counter()
    log_info("Writing output CSV", step="output_write", path=path, row_count=len(rows))

    path.parent.mkdir(parents=True, exist_ok=True)

    output_rows = []
    for row in rows:
        output_rows.append(row.to_csv_row())

    pd.DataFrame(output_rows).to_csv(path, index=False)

    log_info("Output CSV written", step="output_write", path=path, processed_count=len(rows), duration_ms=log_timing(started_at))


def main() -> None:
    # 1. Load environment variables and app settings.
    load_dotenv()
    settings = Settings.from_env()

    # breakpoint()  # debugger stops here

    input_path = settings.input_csv
    output_path = settings.output_csv

    # 2. Configure logging before the pipeline starts.
    configure_logging(settings.log_level, log_file=settings.log_file)

    print("AI lead research pipeline is running...")
    print("Detailed logs: %s" % settings.log_file)

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
