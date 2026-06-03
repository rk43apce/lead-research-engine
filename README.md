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
├── services/
│   ├── scraper.py
│   ├── researcher.py
│   ├── llm.py
│   ├── email_generator.py
│   ├── validator.py
│   ├── prompts.py
│   ├── config.py
│   ├── logger.py
│   └── models.py
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

Input and output paths are configured through `.env`:

```text
INPUT_CSV=input/leads.csv
OUTPUT_CSV=output/enriched_leads.csv
```

Detailed logs are written to `logs/app.log` with company names, step names, and timing. The console only shows simple run status.

## Generate Input Leads

To create `input/leads.csv` automatically from public institution data:

```bash
python generate_leads.py --total 1000
```

By default this creates bank leads from FDIC data and only writes rows that have a website URL. Leads without websites are skipped because the research pipeline needs a website to scrape useful context. The generated CSV includes:

- `company`
- `website`
- `institution_category`

The main pipeline only requires `company` and optional `website`; `institution_category` is kept only as a helpful label.

To use OpenAI as an AI-assisted lead source instead of FDIC:

```bash
python generate_leads.py --source openai --total 100 --institution-type both
```

OpenAI mode can request `community_bank`, `credit_union`, or `both`. It still requires `company` and `website` for every row, deduplicates names, and writes the same CSV columns. Treat OpenAI-sourced rows as AI-suggested leads; the downstream scraper/enrichment pipeline should verify that the websites are reachable and useful.

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

The research layer and LLM layer are intentionally separate. `researcher.py` and `scraper.py` gather grounded landing page text from the URL in the CSV, then follow press, news, media, and investor links discovered on that site up to three link levels deep. The researcher also scans homepage/contact/leadership-style pages for a non-generic recipient email. `llm.py` receives only grounded context and is instructed to return structured JSON. If no source-backed signal is found, the pipeline writes a safe "No recent verifiable public signal found" value and the email is not allowed to claim one.

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
