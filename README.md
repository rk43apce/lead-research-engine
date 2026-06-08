# AI Lead Research and Cold Email Generator

This project researches financial institution leads, finds a verifiable public signal when available, and generates a grounded outreach email for The PreCogs.

It is a lead research and personalization system, not a fraud detection system.

## What It Does

- Reads a CSV of companies from `input/leads.csv`
- Scrapes the landing page URL provided in the CSV
- Discovers press, news, media, and investor links from the landing page
- Scrapes those discovered source pages, with one controlled second hop for specific releases when available
- Uses landing page text as grounded company context
- Safely says no recent public signal was found when no source-backed signal is discovered
- Sends grounded context to the configured LLM provider for classification and email generation
- Validates emails for word count, source discipline, and no-PII positioning
- Writes `output/enriched_leads.csv`

## Architecture

```text
.
├── input/
├── output/
├── web/
│   ├── app.py
│   ├── db.py
│   ├── pipeline_runner.py
│   ├── core/
│   │   ├── scraper.py
│   │   ├── researcher.py
│   │   ├── llm.py
│   │   ├── email_generator.py
│   │   ├── validator.py
│   │   ├── prompts.py
│   │   ├── config.py
│   │   ├── logger.py
│   │   └── models.py
│   ├── templates/
│   └── static/
├── main.py
├── requirements.txt
└── .env.example
```

For a detailed technical explanation of the full workflow, module responsibilities, data flow, guardrails, and production considerations, see [TECHNICAL_DOCUMENTATION.md](TECHNICAL_DOCUMENTATION.md).

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Choose an LLM provider in `.env`:

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
```

To switch to Anthropic later:

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-3-5-haiku-latest
```

## Run

```bash
python main.py
```

To process only the first few leads:

```bash
python main.py --limit 5
```

Input and output paths are configured through `.env`:

```text
INPUT_CSV=input/leads.csv
OUTPUT_CSV=output/enriched_leads.csv
```

Detailed logs are written to `logs/app.log` with company names, step names, and timing. The console only shows simple run status.

## Generate Input Leads

To create `input/leads.csv` automatically with the LLM lead generator:

```bash
python generate_leads.py --total 1000
```

The generator requests U.S. community banks and credit unions and only writes rows that have a website URL. Leads without websites are skipped because the research pipeline needs a website to scrape useful context. The generated CSV includes:

- `company`
- `website`
- `institution_category`

The main pipeline only requires `company` and optional `website`; `institution_category` is kept only as a helpful label. The generator can request `community_bank`, `credit_union`, or `both`.

## Input CSV

Required column:

- `company`

Optional column:

- `website`

Example:

```csv
company,website
Navy Federal Credit Union,https://www.navyfederal.org
Ally Bank,https://www.ally.com
```

## Output CSV

Columns:

- `company`
- `institution_type`
- `fraud_angle`
- `signal`
- `source_url`
- `recipient_email`
- `recipient_email_source_url`
- `email`

## Design Notes

The research layer and LLM layer are intentionally separate. `researcher.py` and `scraper.py` gather grounded landing page text from the URL in the CSV, then follow press, news, media, and investor links discovered on that site up to three link levels deep. The researcher also scans homepage/contact/leadership-style pages for a non-generic recipient email. Before prompting, the researcher compresses raw page text into compact facts such as services, customer clues, risk clues, evidence snippets, and source URLs. `llm.py` receives only that grounded compact context and is instructed to return structured JSON. If no source-backed signal is found, the pipeline writes a safe "No recent verifiable public signal found" value and the email is not allowed to claim one.

The system uses asyncio for lead-level concurrency and HTTP timeouts/retries to keep a batch of leads practical for the assignment target.

## Reliability Guardrails

- Source URL required for every signal
- LLM prompted to use supplied context only
- JSON-only LLM outputs
- Fallback classification and fallback email if the configured LLM fails
- Email validation trims to 120 words
- Missing signal emails are rewritten if they appear to imply a recent signal
- Logging captures per-lead warnings without stopping the batch

## Limitations

This version does not perform web search, so it only discovers signals linked from the company website's static HTML. For a production deployment, I would add an official search API, browser rendering for script-heavy sites, content freshness extraction, domain allow/deny lists, persistent caching, and a reviewed source ranking policy.

## Demo Flask UI

The Flask UI is the main application shell. The core research, validation, lead sourcing, and email generation modules live under `web/core`, and the UI starts those modules directly in a background thread. The small root-level CLI files are kept as compatibility wrappers.

### UI Setup

Install dependencies:

```bash
pip install -r requirements.txt
```

Add login settings to `.env`:

```bash
ADMIN_USERNAME=admin
ADMIN_PASSWORD=admin123
FLASK_SECRET_KEY=change-this-for-local-demo
```

Run the UI:

```bash
python -m web.app
```

Then open:

```text
http://127.0.0.1:5000
```

### UI Pages

- `/login` uses the hardcoded admin credentials from `.env` and Flask session auth.
- `/` shows total reviewed leads, processed runs, approved emails, rejected emails, latest run status, and latest output CSV.
- `/config` stores provider, API key, model, CSV paths, lead limit, and concurrency in SQLite. API keys are masked in the UI.
- `/pipeline` starts the `web/core` pipeline directly, records status/progress in SQLite, and writes the generated output CSV.
- `/outputs` lists CSV files in `output/`.
- `/outputs/<filename>` imports a CSV into the review table and lets a human approve, reject, or modify email drafts.
- `/logs` shows the latest pipeline run log captured in SQLite.

### LLM Provider Settings

For demo use, choose `Mock` in the UI. It uses no network and no API key.

For real providers, choose `OpenAI`, `Anthropic`, `Gemini`, or `Groq` and save the API key/model in the UI. The UI maps those values to the core pipeline settings:

```text
OPENAI_API_KEY
OPENAI_MODEL
ANTHROPIC_API_KEY
ANTHROPIC_MODEL
GEMINI_API_KEY
GEMINI_MODEL
GROQ_API_KEY
GROQ_MODEL
```

The existing CLI still supports `.env` configuration directly, including:

```text
LLM_PROVIDER
INPUT_CSV
OUTPUT_CSV
MAX_CONCURRENCY
LOG_FILE
```

### Approval Workflow

The review screen is the human-in-the-loop step. Generated emails start as `pending`. A reviewer can:

- approve an email
- reject an email
- edit and save a modified email

Only rows with `status = approved` are exported or sent by the sending module.

To create the approved export, click `Export Approved Emails` from the output or review page. This writes:

```text
output/approved_emails.csv
```

Exported fields:

- `company`
- `source_url`
- `contact_email`
- `final_email`
- `status`

### Email Sending

The UI includes a vendor-neutral email sending controller at:

```text
/sending
```

The sending page lets an admin configure:

- email provider
- provider API key
- from email
- from name
- reply-to email

Supported providers:

- `Mock`: records a successful send without calling an external service
- `Twilio SendGrid`: sends through the SendGrid Mail Send API

Only approved rows with both `contact_email` and `final_email` are eligible to send. This keeps the human-in-the-loop approval step mandatory before any vendor call.

For Twilio SendGrid, configure these fields in the UI or store the key in SQLite through the UI:

```text
Provider: Twilio SendGrid
Provider API key: your SendGrid API key
From email: verified sender email
From name: The PreCogs
Reply-to email: optional
```

The sender can also read these optional `.env` values when UI fields are blank:

```text
SENDGRID_API_KEY
EMAIL_FROM_EMAIL
EMAIL_FROM_NAME
EMAIL_REPLY_TO
```

Send history is stored in SQLite:

- `email_send_runs`
- `email_send_events`

The sender is intentionally wrapped behind `web/email_sender.py` so another provider can be added later without changing the review workflow.
