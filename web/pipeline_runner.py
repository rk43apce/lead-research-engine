from __future__ import annotations

import os
import subprocess
import sys
import threading

from web.db import BASE_DIR, finish_pipeline_run, latest_output_file, update_pipeline_run


def start_pipeline(run_id: int, config: dict[str, str], limit_count: int, generate_leads: bool = False) -> None:
    """Run the existing CLI in the background; Flask only orchestrates it."""
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
            lead_result = _run_command(
                [
                    sys.executable,
                    "generate_leads.py",
                    "--total",
                    str(limit_count),
                    "--batch-size",
                    str(min(limit_count, 50)),
                    "--max-attempts",
                    "5",
                ],
                env,
            )
            captured_output.append("=== Lead Generation ===\n%s\n%s" % (lead_result.stdout, lead_result.stderr))
            if lead_result.returncode != 0:
                finish_pipeline_run(
                    run_id,
                    "failed",
                    error_message=_tail_text(lead_result.stderr or lead_result.stdout, 1000),
                    log_tail=_tail_text("\n".join(captured_output), 4000),
                )
                return
            update_pipeline_run(run_id, progress_percent=45, progress_message="Lead generation completed")

        update_pipeline_run(run_id, progress_percent=55, progress_message="Researching leads and drafting emails")
        pipeline_result = _run_command([sys.executable, "main.py", "--limit", str(limit_count)], env)
        captured_output.append("=== Enrichment Pipeline ===\n%s\n%s" % (pipeline_result.stdout, pipeline_result.stderr))
        log_tail = _tail_text("\n".join(captured_output), 4000)

        if pipeline_result.returncode == 0:
            update_pipeline_run(run_id, progress_percent=90, progress_message="Finalizing output CSV")
            finish_pipeline_run(
                run_id,
                "completed",
                output_file=_output_file_from_stdout(pipeline_result.stdout) or latest_output_file(),
                error_message="",
                log_tail=log_tail,
            )
        else:
            finish_pipeline_run(
                run_id,
                "failed",
                error_message=_tail_text(pipeline_result.stderr or pipeline_result.stdout, 1000),
                log_tail=log_tail,
            )
    except Exception as exc:
        finish_pipeline_run(
            run_id,
            "failed",
            error_message=str(exc),
            log_tail="",
        )


def _run_command(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=None,
    )


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


def _output_file_from_stdout(stdout: str) -> str:
    for line in reversed(stdout.splitlines()):
        if line.startswith("Output CSV:"):
            return os.path.basename(line.split(":", 1)[1].strip())
    return ""
