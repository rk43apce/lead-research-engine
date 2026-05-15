from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str | None
    gemini_model: str
    request_timeout_seconds: float
    max_concurrency: int
    input_csv: Path
    output_csv: Path
    log_level: str
    log_file: Path

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY"),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
            request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "12")),
            max_concurrency=int(os.getenv("MAX_CONCURRENCY", "5")),
            input_csv=Path(os.getenv("INPUT_CSV", "input/leads.csv")),
            output_csv=Path(os.getenv("OUTPUT_CSV", "output/enriched_leads.csv")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_file=Path(os.getenv("LOG_FILE", "logs/app.log")),
        )
