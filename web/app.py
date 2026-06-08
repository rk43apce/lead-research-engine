from __future__ import annotations

import os
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from web.auth import expected_password, expected_username, login_required
from web.db import (
    BASE_DIR,
    all_runs,
    create_pipeline_run,
    dashboard_counts,
    export_approved_emails,
    get_config,
    init_db,
    latest_output_file,
    latest_run,
    list_output_files,
    reviewed_rows,
    safe_output_path,
    save_config,
    set_email_status,
    update_email,
)
from web.pipeline_runner import start_pipeline


load_dotenv()
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-demo-secret-change-me")
init_db()


@app.template_filter("datetime")
def format_datetime(timestamp: float | int | None) -> str:
    if not timestamp:
        return ""
    return datetime.fromtimestamp(float(timestamp)).strftime("%Y-%m-%d %H:%M:%S")


@app.template_filter("filesize")
def format_filesize(size: int) -> str:
    if size < 1024:
        return "%s B" % size
    if size < 1024 * 1024:
        return "%.1f KB" % (size / 1024)
    return "%.1f MB" % (size / (1024 * 1024))


@app.route("/login", methods=["GET"])
def login():
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login_post():
    username = request.form.get("username", "")
    password = request.form.get("password", "")
    if username == expected_username() and password == expected_password():
        session["is_authenticated"] = True
        session["username"] = username
        return redirect(url_for("dashboard"))
    flash("Invalid username or password.", "danger")
    return redirect(url_for("login"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    return render_template(
        "dashboard.html",
        counts=dashboard_counts(),
        latest_run=latest_run(),
        latest_output=latest_output_file(),
    )


@app.route("/config", methods=["GET"])
@login_required
def config():
    config_values = get_config()
    masked_key = _masked_key(config_values.get("api_key", ""))
    return render_template("config.html", config=config_values, masked_key=masked_key)


@app.route("/config", methods=["POST"])
@login_required
def config_post():
    existing = get_config()
    api_key = request.form.get("api_key", "")
    if api_key.strip() == "":
        api_key = existing.get("api_key", "")
    save_config(
        {
            "llm_provider": request.form.get("llm_provider", "Mock"),
            "api_key": api_key,
            "model_name": request.form.get("model_name", ""),
            "input_csv_path": request.form.get("input_csv_path", "input/leads.csv"),
            "output_csv_path": request.form.get("output_csv_path", "output/enriched_leads.csv"),
            "number_of_leads": request.form.get("number_of_leads", "5"),
            "max_concurrency": request.form.get("max_concurrency", "2"),
        }
    )
    flash("Configuration saved.", "success")
    return redirect(url_for("config"))


@app.route("/pipeline")
@login_required
def pipeline():
    return render_template(
        "pipeline.html",
        config=get_config(),
        latest_run=latest_run(),
        runs=all_runs(),
    )


@app.route("/pipeline/status")
@login_required
def pipeline_status():
    run = latest_run()
    if not run:
        return jsonify({"has_run": False})
    return jsonify(
        {
            "has_run": True,
            "id": run["id"],
            "status": run["status"],
            "progress_percent": run["progress_percent"] or 0,
            "progress_message": run["progress_message"] or run["status"],
            "started_at": run["started_at"] or "-",
            "ended_at": run["ended_at"] or "-",
            "output_file": run["output_file"] or "-",
            "error_message": run["error_message"] or "",
        }
    )


@app.route("/pipeline/run", methods=["POST"])
@login_required
def pipeline_run():
    config_values = get_config()
    limit_count = _positive_int(request.form.get("limit_count"), config_values.get("number_of_leads", "5"))
    generate_leads = request.form.get("action") == "generate_and_run"
    run_id = create_pipeline_run(limit_count)
    start_pipeline(run_id, config_values, limit_count, generate_leads=generate_leads)
    if generate_leads:
        flash("Lead generation and pipeline started.", "success")
    else:
        flash("Pipeline started.", "success")
    return redirect(url_for("pipeline"))


@app.route("/outputs")
@login_required
def outputs():
    selected_date = request.args.get("date", "").strip()
    files = list_output_files()
    if selected_date:
        files = [
            file
            for file in files
            if datetime.fromtimestamp(float(file["created_time"])).strftime("%Y-%m-%d") == selected_date
        ]
    return render_template("outputs.html", files=files, selected_date=selected_date)


@app.route("/outputs/<path:filename>")
@login_required
def review_output(filename):
    if filename == "approved_emails.csv":
        flash("approved_emails.csv is an export file, not a reviewable pipeline output.", "warning")
        return redirect(url_for("outputs"))
    try:
        output_path = safe_output_path(filename)
        if not output_path.exists():
            flash("Output CSV no longer exists on disk.", "warning")
            return redirect(url_for("outputs"))
        all_rows = [_row_with_email_parts(row) for row in reviewed_rows(filename)]
    except ValueError:
        flash("Invalid output filename.", "danger")
        return redirect(url_for("outputs"))
    status_filter = request.args.get("status", "pending")
    allowed_filters = {"pending", "modified", "approved", "rejected", "all"}
    if status_filter not in allowed_filters:
        status_filter = "pending"
    row_filter = request.args.get("row_filter", "has_email_draft")
    allowed_row_filters = {"all", "has_contact_email", "has_email_draft"}
    if row_filter not in allowed_row_filters:
        row_filter = "all"

    status_rows = all_rows
    if status_filter != "all":
        status_rows = [row for row in all_rows if row.get("status") == status_filter]

    rows = status_rows
    if row_filter == "has_contact_email":
        rows = [row for row in status_rows if (row.get("contact_email") or "").strip()]
    elif row_filter == "has_email_draft":
        rows = [row for row in status_rows if (row.get("final_email") or "").strip()]

    status_counts = {
        "all": len(all_rows),
        "pending": sum(1 for row in all_rows if row.get("status") == "pending"),
        "modified": sum(1 for row in all_rows if row.get("status") == "modified"),
        "approved": sum(1 for row in all_rows if row.get("status") == "approved"),
        "rejected": sum(1 for row in all_rows if row.get("status") == "rejected"),
    }
    row_counts = {
        "all": len(status_rows),
        "has_contact_email": sum(1 for row in status_rows if (row.get("contact_email") or "").strip()),
        "has_email_draft": sum(1 for row in status_rows if (row.get("final_email") or "").strip()),
    }
    return render_template(
        "review.html",
        filename=filename,
        rows=rows,
        status_filter=status_filter,
        row_filter=row_filter,
        status_counts=status_counts,
        row_counts=row_counts,
    )


@app.route("/email/<int:email_id>/approve", methods=["POST"])
@login_required
def approve_email(email_id):
    set_email_status(email_id, "approved")
    flash("Email approved.", "success")
    return redirect(request.referrer or url_for("outputs"))


@app.route("/email/<int:email_id>/reject", methods=["POST"])
@login_required
def reject_email(email_id):
    set_email_status(email_id, "rejected")
    flash("Email rejected.", "warning")
    return redirect(request.referrer or url_for("outputs"))


@app.route("/email/<int:email_id>/update", methods=["POST"])
@login_required
def update_reviewed_email(email_id):
    subject = request.form.get("subject", "")
    body = request.form.get("body", "")
    action = request.form.get("action", "save")
    update_email(email_id, _combine_email(subject, body))

    if action in {"approve", "approve_export"}:
        set_email_status(email_id, "approved")
        if action == "approve_export":
            path = export_approved_emails()
            return _download_approved_export(path)
        else:
            flash("Email approved after review.", "success")
    else:
        flash("Email draft saved as modified.", "success")

    return redirect(request.referrer or url_for("outputs"))


@app.route("/export/approved", methods=["POST"])
@login_required
def export_approved():
    path = export_approved_emails()
    return _download_approved_export(path)


def _download_approved_export(path):
    # The UI only prepares approved emails; returning an attachment makes the
    # demo workflow clear without adding a real email-sending step yet.
    return send_file(
        path,
        as_attachment=True,
        download_name="approved_emails.csv",
        mimetype="text/csv",
    )


@app.route("/logs")
@login_required
def logs():
    run = latest_run()
    lines = []
    if run and run["log_tail"]:
        lines = run["log_tail"].splitlines()
    return render_template("logs.html", lines=lines, latest_run=run)


def _masked_key(api_key: str) -> str:
    if not api_key:
        return "No API key saved"
    if len(api_key) <= 8:
        return "****"
    return "%s...%s" % (api_key[:4], api_key[-4:])


def _positive_int(value: str | None, fallback: str) -> int:
    try:
        parsed = int(value or fallback)
    except ValueError:
        parsed = int(fallback)
    return max(parsed, 1)


def _split_email(email: str) -> tuple[str, str]:
    email = (email or "").strip()
    if not email:
        return "", ""

    lines = email.splitlines()
    first_line = lines[0].strip() if lines else ""
    if first_line.lower().startswith("subject:"):
        subject = first_line.split(":", 1)[1].strip()
        body = "\n".join(lines[1:]).strip()
        return subject, body

    return "", email


def _combine_email(subject: str, body: str) -> str:
    subject = (subject or "").strip()
    body = (body or "").strip()
    if subject:
        return "Subject: %s\n\n%s" % (subject, body)
    return body


def _row_with_email_parts(row):
    values = dict(row)
    subject, body = _split_email(values.get("final_email", ""))
    values["email_subject"] = subject
    values["email_body"] = body
    return values


if __name__ == "__main__":
    app.run(debug=True, port=5000)
