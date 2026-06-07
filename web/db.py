from __future__ import annotations

import csv
import sqlite3
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "web" / "app.db"
OUTPUT_DIR = BASE_DIR / "output"


DEFAULT_CONFIG = {
    "llm_provider": "OpenAI",
    "api_key": "",
    "model_name": "gpt-4.1-mini",
    "input_csv_path": "input/leads.csv",
    "output_csv_path": "output/enriched_leads.csv",
    "number_of_leads": "5",
    "max_concurrency": "2",
}


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create small UI tables. The core pipeline remains independent."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                limit_count INTEGER,
                started_at TEXT,
                ended_at TEXT,
                output_file TEXT,
                error_message TEXT,
                log_tail TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reviewed_emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                row_number INTEGER NOT NULL,
                company TEXT,
                institution_type TEXT,
                fraud_angle TEXT,
                signal TEXT,
                source_url TEXT,
                contact_email TEXT,
                original_email TEXT,
                final_email TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(filename, row_number)
            )
            """
        )

        for key, value in DEFAULT_CONFIG.items():
            conn.execute(
                "INSERT OR IGNORE INTO app_config (key, value) VALUES (?, ?)",
                (key, value),
            )


def get_config() -> dict[str, str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value FROM app_config").fetchall()
    config = dict(DEFAULT_CONFIG)
    config.update({row["key"]: row["value"] for row in rows})
    return config


def save_config(values: dict[str, Any]) -> None:
    allowed = set(DEFAULT_CONFIG.keys())
    with get_connection() as conn:
        for key, value in values.items():
            if key not in allowed:
                continue
            conn.execute(
                "INSERT INTO app_config (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, str(value or "")),
            )


def create_pipeline_run(limit_count: int) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO pipeline_runs (status, limit_count, started_at) "
            "VALUES ('pending', ?, datetime('now'))",
            (limit_count,),
        )
        return int(cursor.lastrowid)


def update_pipeline_run(run_id: int, **fields: Any) -> None:
    if not fields:
        return
    assignments = ", ".join("%s = ?" % key for key in fields)
    values = list(fields.values())
    values.append(run_id)
    with get_connection() as conn:
        conn.execute("UPDATE pipeline_runs SET %s WHERE id = ?" % assignments, values)


def finish_pipeline_run(
    run_id: int,
    status: str,
    output_file: str = "",
    error_message: str = "",
    log_tail: str = "",
) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            UPDATE pipeline_runs
            SET status = ?, ended_at = datetime('now'), output_file = ?,
                error_message = ?, log_tail = ?
            WHERE id = ?
            """,
            (status, output_file, error_message, log_tail, run_id),
        )


def latest_run() -> sqlite3.Row | None:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()


def all_runs(limit: int = 10) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()


def dashboard_counts() -> dict[str, int]:
    with get_connection() as conn:
        total_leads = conn.execute(
            "SELECT COUNT(*) AS count FROM reviewed_emails WHERE TRIM(COALESCE(final_email, '')) != ''"
        ).fetchone()["count"]
        approved = conn.execute(
            "SELECT COUNT(*) AS count FROM reviewed_emails "
            "WHERE status = 'approved' AND TRIM(COALESCE(final_email, '')) != ''"
        ).fetchone()["count"]
        rejected = conn.execute(
            "SELECT COUNT(*) AS count FROM reviewed_emails "
            "WHERE status = 'rejected' AND TRIM(COALESCE(final_email, '')) != ''"
        ).fetchone()["count"]
        processed = conn.execute(
            "SELECT COALESCE(SUM(limit_count), 0) AS count FROM pipeline_runs WHERE status = 'completed'"
        ).fetchone()["count"]
    return {
        "total_leads": total_leads,
        "total_processed": int(processed or 0),
        "total_approved": approved,
        "total_rejected": rejected,
    }


def safe_output_path(filename: str) -> Path:
    """Keep CSV review limited to the output folder."""
    candidate = (OUTPUT_DIR / filename).resolve()
    output_root = OUTPUT_DIR.resolve()
    if candidate.parent != output_root or candidate.suffix.lower() != ".csv":
        raise ValueError("Invalid CSV filename.")
    return candidate


def list_output_files() -> list[dict[str, Any]]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for path in OUTPUT_DIR.glob("*.csv"):
        if path.name == "approved_emails.csv":
            continue
        stat = path.stat()
        files.append(
            {
                "name": path.name,
                "created_time": stat.st_mtime,
                "size": stat.st_size,
            }
        )
    return sorted(files, key=lambda item: item["created_time"], reverse=True)


def latest_output_file() -> str:
    files = list_output_files()
    return files[0]["name"] if files else ""


def import_csv_for_review(filename: str) -> None:
    path = safe_output_path(filename)
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        with get_connection() as conn:
            for row_number, row in enumerate(reader, start=1):
                email = row.get("email") or row.get("generated_email") or ""
                # Import every CSV row so the review screen reflects the full
                # pipeline output. Rows without drafts stay pending and show a
                # clear empty-draft message in the UI.
                conn.execute(
                    """
                    INSERT OR IGNORE INTO reviewed_emails (
                        filename, row_number, company, institution_type, fraud_angle,
                        signal, source_url, contact_email, original_email, final_email, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        filename,
                        row_number,
                        row.get("company", ""),
                        row.get("institution_type", ""),
                        row.get("fraud_angle", ""),
                        row.get("signal", ""),
                        row.get("source_url", ""),
                        row.get("recipient_email") or row.get("contact_email") or "",
                        email,
                        email,
                    ),
                )


def reviewed_rows(filename: str) -> list[sqlite3.Row]:
    import_csv_for_review(filename)
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM reviewed_emails "
            "WHERE filename = ? "
            "ORDER BY row_number",
            (filename,),
        ).fetchall()


def set_email_status(email_id: int, status: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE reviewed_emails SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, email_id),
        )


def update_email(email_id: int, final_email: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE reviewed_emails SET final_email = ?, status = 'modified', "
            "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (final_email, email_id),
        )


def export_approved_emails() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    export_path = OUTPUT_DIR / "approved_emails.csv"
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT company, source_url, contact_email, final_email, status
            FROM reviewed_emails
            WHERE status = 'approved' AND TRIM(COALESCE(final_email, '')) != ''
            ORDER BY filename, row_number
            """
        ).fetchall()

    with export_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["company", "source_url", "contact_email", "final_email", "status"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    return export_path
