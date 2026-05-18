import time
from pathlib import Path
from typing import List

import pandas as pd

from services.logger import log_info, log_timing
from services.models import EnrichedLead


def write_output(path: Path, rows: List[EnrichedLead]):
    """Write the final enriched leads to a CSV file."""
    started_at = time.perf_counter()

    print("Writing output CSV: %s" % path)
    path.parent.mkdir(parents=True, exist_ok=True)

    output_rows = []
    for row in rows:
        output_rows.append(row.to_csv_row())

    pd.DataFrame(output_rows).to_csv(path, index=False)

    log_info(
        "Output CSV written",
        step="output_write",
        path=path,
        processed_count=len(rows),
        duration_ms=log_timing(started_at),
    )
