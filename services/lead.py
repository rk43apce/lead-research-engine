import time
import uuid
from pathlib import Path
from typing import List, Optional

import pandas as pd

from services.logger import log_error, log_info, log_timing, log_warning
from services.models import Lead


def load_leads(path: Path, limit: Optional[int] = None):
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

        request_id = str(uuid.uuid4())
        leads.append(Lead(company=company, website=website, request_id=request_id))

    log_info(
        "Finished loading leads",
        step="csv_processing",
        valid_count=len(leads),
        invalid_count=invalid_rows,
        duration_ms=log_timing(started_at),
    )
    return leads
