from __future__ import annotations

import os
import time
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, session, url_for

from web.auth import expected_password, expected_username, login_required
from web.db import (
    BASE_DIR,
    all_runs,
    all_pending_emails,
    already_sent_count,
    approved_emails_for_sending,
    approved_emails_with_send_status,
    approved_unsent_count,
    pending_review_count,
    create_email_send_run,
    create_pipeline_run,
    dashboard_counts,
    export_approved_emails,
    finish_email_send_run,
    get_config,
    init_db,
    latest_email_send_run,
    latest_output_file,
    latest_run,
    list_output_files,
    mark_reviewed_email_send_status,
    recent_email_send_events,
    record_email_send_event,
    reviewed_rows,
    safe_output_path,
    save_config,
    set_email_status,
    update_email,
)
from web.email_sender import create_email_provider
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
    counts = dashboard_counts()
    run = latest_run()
    latest_output = latest_output_file()
    pending = pending_review_count()
    unsent = approved_unsent_count()

    if not run:
        state = "no_run"
    elif pending > 0 and unsent == 0:
        state = "needs_review"
    elif pending > 0 and unsent > 0:
        state = "review_and_send"
    elif unsent > 0:
        state = "ready_to_send"
    else:
        state = "complete"

    return render_template(
        "dashboard.html",
        counts=counts,
        latest_run=run,
        latest_output=latest_output,
        pending_count=pending,
        approved_unsent=unsent,
        state=state,
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


@app.route("/review/pending")
@login_required
def review_pending():
    all_rows = [_row_with_email_parts(r) for r in all_pending_emails()]

    row_filter = request.args.get("row_filter", "has_email_draft")
    if row_filter not in {"all", "has_contact_email", "has_email_draft"}:
        row_filter = "has_email_draft"

    if row_filter == "has_contact_email":
        rows = [r for r in all_rows if (r.get("contact_email") or "").strip()]
    elif row_filter == "has_email_draft":
        rows = [r for r in all_rows if (r.get("final_email") or "").strip()]
    else:
        rows = all_rows

    row_counts = {
        "all": len(all_rows),
        "has_contact_email": sum(1 for r in all_rows if (r.get("contact_email") or "").strip()),
        "has_email_draft": sum(1 for r in all_rows if (r.get("final_email") or "").strip()),
    }

    return render_template(
        "review.html",
        filename=None,
        rows=rows,
        status_filter="pending",
        row_filter=row_filter,
        status_counts={"all": len(all_rows), "pending": len(all_rows), "modified": 0, "approved": 0, "rejected": 0},
        row_counts=row_counts,
        review_url=url_for("review_pending"),
    )


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


@app.route("/sending")
@login_required
def sending():
    config_values = get_config()
    approved_rows = approved_emails_for_sending()
    sent_rows = [_row_with_email_parts(r) for r in approved_emails_with_send_status()]
    return render_template(
        "sending.html",
        config=config_values,
        approved_count=len(approved_rows),
        latest_send_run=latest_email_send_run(),
        send_events=recent_email_send_events(),
        sent_rows=sent_rows,
    )


@app.route("/sending/settings")
@login_required
def sending_settings():
    config_values = get_config()
    return render_template(
        "sending_settings.html",
        config=config_values,
        masked_email_key=_masked_key(config_values.get("email_api_key", "") or os.getenv("SENDGRID_API_KEY", "")),
    )


@app.route("/sending/config", methods=["POST"])
@login_required
def sending_config_post():
    existing = get_config()
    api_key = request.form.get("email_api_key", "")
    if api_key.strip() == "":
        api_key = existing.get("email_api_key", "")
    save_config(
        {
            "email_provider": request.form.get("email_provider", "Mock"),
            "email_api_key": api_key,
            "email_from_email": request.form.get("email_from_email", ""),
            "email_from_name": request.form.get("email_from_name", "The PreCogs"),
            "email_reply_to": request.form.get("email_reply_to", ""),
            "email_rate_limit_seconds": request.form.get("email_rate_limit_seconds", "1.0"),
            "email_unsubscribe_footer": request.form.get("email_unsubscribe_footer", ""),
        }
    )
    flash("Email provider settings saved.", "success")
    return redirect(url_for("sending_settings"))


@app.route("/sending/confirm")
@login_required
def sending_confirm():
    config_values = get_config()
    rows_to_send = approved_emails_for_sending()
    sent_already = already_sent_count()

    provider = (config_values.get("email_provider") or "Mock").strip()
    from_email = (config_values.get("email_from_email") or "").strip()
    api_key = (config_values.get("email_api_key") or os.getenv("SENDGRID_API_KEY", "")).strip()
    unsubscribe_footer = (config_values.get("email_unsubscribe_footer") or "").strip()
    rate_limit = _safe_float(config_values.get("email_rate_limit_seconds"), 1.0)

    checks = []
    is_mock = provider.lower() == "mock"

    if is_mock:
        checks.append(("info", "Mock provider selected — no real emails will be sent."))
    else:
        if not from_email:
            checks.append(("error", "From email is not configured. Set it in Provider Settings."))
        elif _is_free_email_domain(from_email):
            domain = from_email.split("@")[-1]
            checks.append(("warning", "From email uses a free domain (%s). Cold email from free providers is typically blocked or spam-filtered. Use a domain you own with SendGrid domain authentication." % domain))
        else:
            checks.append(("ok", "From email uses a custom domain."))

        if not api_key:
            checks.append(("error", "No SendGrid API key configured."))
        else:
            checks.append(("ok", "SendGrid API key is configured."))

    if unsubscribe_footer:
        checks.append(("ok", "Unsubscribe footer will be appended to each email."))
    else:
        checks.append(("warning", "No unsubscribe footer set. CAN-SPAM compliance requires an opt-out mechanism."))

    if rate_limit > 0:
        checks.append(("ok", "Rate limiting: %.1fs delay between each email." % rate_limit))
    else:
        checks.append(("info", "Rate limiting disabled — all emails send without delay."))

    if sent_already > 0:
        checks.append(("info", "%d email(s) already sent and will be skipped (duplicate prevention)." % sent_already))

    has_errors = any(level == "error" for level, _ in checks)
    preview_rows = [_row_with_email_parts(r) for r in rows_to_send]

    return render_template(
        "sending_confirm.html",
        config=config_values,
        preview_rows=preview_rows,
        already_sent_count=sent_already,
        checks=checks,
        has_errors=has_errors,
        rate_limit=rate_limit,
        unsubscribe_footer=unsubscribe_footer,
    )


@app.route("/sending/send-approved", methods=["POST"])
@login_required
def send_approved_emails():
    config_values = get_config()
    approved_rows = approved_emails_for_sending()
    if not approved_rows:
        flash("No approved emails with recipient email IDs are ready to send.", "warning")
        return redirect(url_for("sending"))

    provider = create_email_provider(config_values)
    provider_name = config_values.get("email_provider", "Mock")
    rate_limit_seconds = _safe_float(config_values.get("email_rate_limit_seconds"), 1.0)
    unsubscribe_footer = (config_values.get("email_unsubscribe_footer") or "").strip()
    run_id = create_email_send_run(provider_name, len(approved_rows))
    sent_count = 0
    failed_count = 0

    for i, row in enumerate(approved_rows):
        if i > 0 and rate_limit_seconds > 0:
            time.sleep(rate_limit_seconds)
        subject, body = _split_email(row["final_email"])
        if unsubscribe_footer:
            body = body.rstrip() + "\n\n--\n" + unsubscribe_footer
        result = provider.send_email(row["contact_email"], subject or "The PreCogs", body)
        if result.success:
            sent_count += 1
            mark_reviewed_email_send_status(row["id"], "sent")
            record_email_send_event(
                run_id,
                row["id"],
                row["company"] or "",
                row["contact_email"] or "",
                "sent",
                provider_message_id=result.provider_message_id,
            )
        else:
            failed_count += 1
            mark_reviewed_email_send_status(row["id"], "failed", result.error_message)
            record_email_send_event(
                run_id,
                row["id"],
                row["company"] or "",
                row["contact_email"] or "",
                "failed",
                error_message=result.error_message,
            )

    status = "completed" if failed_count == 0 else "completed_with_errors"
    finish_email_send_run(run_id, status, sent_count, failed_count)
    flash("Send run finished: %s sent, %s failed." % (sent_count, failed_count), "success" if failed_count == 0 else "warning")
    return redirect(url_for("sending"))


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


_FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
    "aol.com", "protonmail.com", "live.com", "msn.com", "me.com",
}


def _is_free_email_domain(email: str) -> bool:
    domain = email.split("@")[-1].lower() if "@" in email else ""
    return domain in _FREE_EMAIL_DOMAINS


def _safe_float(value: str | None, fallback: float) -> float:
    try:
        return max(0.0, float(value or fallback))
    except (ValueError, TypeError):
        return fallback


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
