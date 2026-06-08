from __future__ import annotations

import asyncio
import contextlib
import io
import os
import threading
from datetime import datetime
from pathlib import Path

from web.core.config import Settings
from web.core.lead_sourcing import LeadSourceGenerator
from web.core.logger import configure_logging, log_info
from web.core.output import write_output
from web.core.pipeline import run_pipeline
from web.db import finish_pipeline_run, latest_output_file, update_pipeline_run


def start_pipeline(run_id: int, config: dict[str, str], limit_count: int, generate_leads: bool = False) -> None:
    """Run the core pipeline in the background; Flask remains the orchestration layer."""
    thread = threading.Thread(
        target=_run_pipeline,
        args=(run_id, config, limit_count, generate_leads),
        daemon=True,
    )
    thread.start()


def _run_pipeline(run_id: int, config: dict[str, str], limit_count: int, generate_leads: bool) -> None:
    update_pipeline_run(run_id, status="running", progress_percent=10, progress_message="Starting pipeline")
    env = os.environ.copy()
    env.update(_pipeline_env(config))

    try:
        captured_output = []
        if generate_leads:
            update_pipeline_run(run_id, progress_percent=20, progress_message="Generating fresh leads")
            captured_output.append("=== Lead Generation ===\n%s" % _generate_leads(env, limit_count))
            update_pipeline_run(run_id, progress_percent=45, progress_message="Lead generation completed")

        update_pipeline_run(run_id, progress_percent=55, progress_message="Researching leads and drafting emails")
        pipeline_output, output_file = _run_enrichment(env, limit_count)
        captured_output.append("=== Enrichment Pipeline ===\n%s" % pipeline_output)
        log_tail = _tail_text("\n".join(captured_output), 4000)

        update_pipeline_run(run_id, progress_percent=90, progress_message="Finalizing output CSV")
        finish_pipeline_run(
            run_id,
            "completed",
            output_file=output_file or latest_output_file(),
            error_message="",
            log_tail=log_tail,
        )
    except Exception as exc:
        finish_pipeline_run(
            run_id,
            "failed",
            error_message=str(exc),
            log_tail=_tail_text(str(exc), 4000),
        )


def _generate_leads(env: dict[str, str], limit_count: int) -> str:
    api_key = env.get("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for lead generation.")

    output_buffer = io.StringIO()
    with contextlib.redirect_stdout(output_buffer), contextlib.redirect_stderr(output_buffer):
        generator = LeadSourceGenerator(timeout_seconds=float(env.get("REQUEST_TIMEOUT_SECONDS", "25")))
        leads = asyncio.run(
            generator.generate(
                output_path=Path(env.get("INPUT_CSV", "input/leads.csv")),
                api_key=api_key,
                model=env.get("OPENAI_MODEL", "gpt-4.1-mini"),
                total=limit_count,
                institution_type="both",
                batch_size=min(limit_count, 50),
                max_attempts=5,
            )
        )
        log_info("Lead sourcing completed", step="lead_sourcing", count=len(leads), output=env.get("INPUT_CSV", "input/leads.csv"))
        print("Done. Generated %s leads." % len(leads))
        print("Input CSV: %s" % env.get("INPUT_CSV", "input/leads.csv"))
    return output_buffer.getvalue()


def _run_enrichment(env: dict[str, str], limit_count: int) -> tuple[str, str]:
    settings = _settings_from_env(env)
    output_path = _timestamped_output_path(settings.output_csv)
    output_buffer = io.StringIO()

    with contextlib.redirect_stdout(output_buffer), contextlib.redirect_stderr(output_buffer):
        configure_logging(settings.log_level, log_file=settings.log_file)
        print("AI lead research pipeline is running...")
        log_info("Starting lead research pipeline", step="startup", input=settings.input_csv, output=output_path)
        rows = asyncio.run(run_pipeline(settings, input_path=settings.input_csv, limit=limit_count))
        write_output(output_path, rows)
        log_info("Lead research pipeline completed", step="shutdown", total_rows=len(rows))
        print("Done. Processed %s leads." % len(rows))
        print("Output CSV: %s" % output_path)

    return output_buffer.getvalue(), output_path.name


def _settings_from_env(env: dict[str, str]) -> Settings:
    return Settings(
        llm_provider=env.get("LLM_PROVIDER", "openai"),
        openai_api_key=env.get("OPENAI_API_KEY"),
        openai_model=env.get("OPENAI_MODEL", "gpt-4.1-mini"),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY"),
        anthropic_model=env.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest"),
        gemini_api_key=env.get("GEMINI_API_KEY"),
        gemini_model=env.get("GEMINI_MODEL", "gemini-1.5-flash"),
        groq_api_key=env.get("GROQ_API_KEY"),
        groq_model=env.get("GROQ_MODEL", "llama-3.1-8b-instant"),
        request_timeout_seconds=float(env.get("REQUEST_TIMEOUT_SECONDS", "12")),
        max_concurrency=int(env.get("MAX_CONCURRENCY", "2")),
        input_csv=Path(env.get("INPUT_CSV", "input/leads.csv")),
        output_csv=Path(env.get("OUTPUT_CSV", "output/enriched_leads.csv")),
        log_level=env.get("LOG_LEVEL", "INFO"),
        log_file=Path(env.get("LOG_FILE", "logs/app.log")),
    )


def _timestamped_output_path(path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return path.with_name("%s_%s%s" % (path.stem, timestamp, path.suffix))


def _pipeline_env(config: dict[str, str]) -> dict[str, str]:
    provider = (config.get("llm_provider") or "Mock").lower()
    api_key = (config.get("api_key") or "").strip()
    model = (config.get("model_name") or "").strip()

    env = {
        "LLM_PROVIDER": provider,
        "INPUT_CSV": config.get("input_csv_path", "input/leads.csv"),
        "OUTPUT_CSV": config.get("output_csv_path", "output/enriched_leads.csv"),
        "MAX_CONCURRENCY": config.get("max_concurrency", "2"),
    }

    # The UI stores one generic API key and maps it to the existing CLI env names.
    if provider == "gemini":
        _set_if_present(env, "GEMINI_API_KEY", api_key)
        _set_if_present(env, "GEMINI_MODEL", model)
    elif provider == "anthropic":
        _set_if_present(env, "ANTHROPIC_API_KEY", api_key)
        _set_if_present(env, "ANTHROPIC_MODEL", model)
    elif provider == "groq":
        _set_if_present(env, "GROQ_API_KEY", api_key)
        _set_if_present(env, "GROQ_MODEL", model)
    elif provider == "mock":
        env["LLM_PROVIDER"] = "mock"
    else:
        env["LLM_PROVIDER"] = "openai"
        _set_if_present(env, "OPENAI_API_KEY", api_key)
        _set_if_present(env, "OPENAI_MODEL", model)
    return env


def _set_if_present(env: dict[str, str], key: str, value: str) -> None:
    if value:
        env[key] = value


def _tail_text(value: str, max_chars: int) -> str:
    return value[-max_chars:] if value else ""

