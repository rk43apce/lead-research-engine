# Memory Bank

This file is a compact working memory for future tasks in this repository. It is distilled from the existing markdown docs: `README.md`, `TECHNICAL_DOCUMENTATION.md`, `SERVICES_DOCUMENTATION.md`, `DATA_FLOW.md`, `REQUIREMENTS_MAPPING.md`, `LOW_LEVEL_DESIGN.md`, and `command.md`.

## Project Identity

- Project: AI lead research and cold email generator for The PreCogs.
- Purpose: research financial institution leads, identify grounded context and a verifiable public signal when available, generate a conservative personalized outreach email, and write an enriched CSV.
- This is a sales research and outreach personalization system, not a fraud detection engine.
- Core principle: factual grounding. Research gathers public facts and URLs; the LLM summarizes and writes; validation protects final output.

## Assignment Contract

For each input company, the system should:

1. Visit the prospect website when a website is provided.
2. Identify institution type and customer context.
3. Identify a relevant fraud or risk angle.
4. Find a recent, verifiable public signal when possible.
5. Generate a grounded cold email under 120 words.
6. Output a CSV with exactly the required assignment columns.

Output columns:

```text
company
institution_type
fraud_angle
signal
source_url
recipient_email
recipient_email_source_url
email
```

## Current Working Flow

```text
input/leads.csv
  -> services.lead.load_leads()
  -> services.pipeline.run_pipeline()
  -> process each lead asynchronously with concurrency limit
  -> services.researcher.CompanyResearcher.research()
  -> services.scraper.AsyncScraper.fetch_landing_page()
  -> website-only source/signal discovery from links on the company site
  -> ResearchContext
  -> services.email_generator.LeadContentGenerator.classify_context()
  -> services.llm client returns structured JSON
  -> LeadContentGenerator.generate_email()
  -> services.validator.LeadValidator validates email and signal
  -> services.output.write_output()
  -> timestamped output CSV
```

The low-level design says output paths are timestamped:

```text
output/enriched_leads_YYYYMMDD_HHMMSS.csv
```

Some older docs mention `output/enriched_leads.csv`; treat timestamped output as the newer design unless code says otherwise.

## Input

Input path is configured by environment, normally:

```text
INPUT_CSV=input/leads.csv
```

Required column:

```text
company
```

Optional column:

```text
website
```

Rows with missing company names are skipped and logged. If a website is missing, scraping is skipped and the lead continues with a safe no-signal state.

`generate_leads.py` can now create this input CSV automatically through LLM-assisted lead sourcing:

```bash
python generate_leads.py --total 1000
```

The generator requests rows with mandatory `company` and official `website`, deduplicates company names, and writes only `company`, `website`, and `institution_category`. Treat generated rows as AI-suggested leads; downstream scraping should verify the website context.

## Services

### `main.py`

- Entrypoint.
- Loads settings from `.env`.
- Configures logging.
- Runs the async pipeline.
- Writes final output through the output layer.
- Creates timestamped output path in the low-level design.

### `services/config.py`

- Reads runtime configuration from environment variables.
- Important settings in docs include:

```text
LLM_PROVIDER
OPENAI_API_KEY
OPENAI_MODEL
ANTHROPIC_API_KEY
ANTHROPIC_MODEL
INPUT_CSV
OUTPUT_CSV
REQUEST_TIMEOUT_SECONDS
MAX_CONCURRENCY
LOG_LEVEL
LOG_FILE
```

Use `LLM_PROVIDER=openai` or `LLM_PROVIDER=anthropic` to switch pipeline generation providers.

### `services/models.py`

Shared dataclasses and enums. Important models:

- `Lead`: input company and optional website.
- `PageContent`: fetched page URL, title, text, status, links, discovered emails, error.
- `PageLink`: normalized page link from scraped HTML.
- `PublicSignal`: selected source-backed signal summary, source URL, signal type, source title, confidence.
- `ContactEmail`: selected non-generic recipient email, source URL, confidence.
- `ResearchFacts`: compact local facts for LLM prompts, including services, customer clues, risk clues, evidence snippets, and source URLs.
- `ResearchContext`: grounded context for one lead, including public signal, contact email, and compact facts.
- `LLMResearchOutput`: institution type, customer segment, services, fraud angle.
- `EmailDraft`: generated email plus warnings.
- `EnrichedLead`: final CSV row.

`EnrichedLead.to_csv_row()` should mirror the assignment output columns.

### `services/logger.py`

- Central logging helpers.
- Logs company name, step name, timings, warnings, and errors.
- Redacts secrets.
- Logs go to `logs/app.log`.
- Useful commands from `command.md`:

```bash
grep "Landing page text extracted" logs/app.log
grep "Gemini classification input prepared" logs/app.log
grep "Gemini email input prepared" logs/app.log
```

### `services/lead.py`

- Reads CSV with pandas.
- Validates required `company` column.
- Reads optional `website`.
- Creates `Lead` objects.
- Logs valid and invalid row counts.

### `services/lead_sourcing.py`

- Generates lead CSV rows through LLM-assisted sourcing.
- Requires `OPENAI_API_KEY`.
- Requests community banks, credit unions, or both.
- Filters rows missing company/website and deduplicates by company name.

### `generate_leads.py`

- CLI entrypoint for lead sourcing.
- Default command:

```bash
python generate_leads.py --total 1000
```

- Supports source split controls with `--banks` and `--credit-unions`.

### `services/pipeline.py`

- Orchestrates all leads.
- Creates shared service objects once.
- Uses `asyncio.Semaphore(settings.max_concurrency)` to limit lead-level concurrency.
- Per-lead flow:

```python
context = await researcher.research(lead)
classification = await generator.classify_context(context)
draft = await generator.generate_email(context, classification)
draft = validator.validate_email(draft, context.public_signal, company=company)
validator.validate_signal(context.public_signal, company=company)
```

- On individual lead failure, should return a safe failed row and continue processing other leads.

### `services/scraper.py`

- Async website content collector.
- Normalizes URLs and adds `https://` when needed.
- Fetches HTML with `aiohttp`.
- Applies timeouts and retries.
- Parses HTML with BeautifulSoup.
- Removes non-content tags like `script`, `style`, `noscript`, and `svg`.
- Extracts title, readable text, and normalized links.
- Skips `mailto:`, `tel:`, `javascript:`, non-HTTP, and duplicate links.
- Converts relative links to absolute URLs.
- Should not classify companies, decide fraud angles, or write emails.

### `services/researcher.py`

- Factual grounding layer.
- Calls scraper for landing page context.
- Stores homepage URL and about text in `ResearchContext`.
- Current low-level design: website-only public signal discovery, not broad web search.
- Ranks source-like links on the company site, fetches source pages, discovers detail URLs, fetches detail pages, follows one more third-level hop, scores pages, and selects the best source-backed signal.
- Current crawl depth is homepage plus three link levels: homepage -> source pages -> detail pages -> third-level pages.
- Also ranks contact/leadership/about/team links and scans those pages for non-generic same-domain recipient emails.
- Generic inboxes such as `info@`, `support@`, `contact@`, `noreply@`, and similar are rejected for `recipient_email`.
- Extracts compact `ResearchFacts` locally before LLM calls to reduce prompt tokens and improve personalization.
- Source-like links include press, press release, news, newsroom, media, investor, announcements, updates, blog, careers, and jobs.
- Only same-company-site links should be considered for this website-only signal discovery.
- Signal types include fraud/risk, compliance, payments, partnership, expansion, hiring, and press.
- If no reliable source URL exists, returns `PublicSignal.none()`.
- Should not call the LLM or write emails.

### `services/llm.py`

- API wrapper only.
- Sends prompts, requests JSON output, parses JSON safely, retries failures, and redacts keys in logs.
- Docs conflict:
  - Some docs say Gemini with `GEMINI_API_KEY`, `GEMINI_MODEL=gemini-flash-latest`, and `x-goog-api-key`.
  - Low-level design says OpenAI with `OPENAI_API_KEY`, `OPENAI_MODEL`, and `/v1/responses`.
- Before API changes, inspect the actual code and `.env.example`.
- Should not own business logic, signal selection, validation, or CSV writing.

### `services/prompts.py`

- Builds LLM prompts separately from the API client.
- `research_prompt()` asks for JSON classification:

```json
{
  "institution_type": "string",
  "customer_segment": "string",
  "services": ["string"],
  "fraud_angle": "string"
}
```

- `email_prompt()` asks for JSON email:

```json
{
  "email": "string"
}
```

Prompt safety rules:

- Use only supplied context.
- Do not invent facts, services, customer segments, or signals.
- Mention recent signal only when a source-backed signal exists.
- Keep email under 120 words.
- Position The PreCogs as augmenting existing teams and workflows, not replacing them.
- Mention the no-PII angle naturally.
- End with a low-friction CTA.
- Return valid JSON only.

### `services/email_generator.py`

- Converts grounded research into:
  1. company classification
  2. email draft
- Calls the LLM through the client.
- Provides conservative fallback classification and fallback email if the LLM fails.
- Normalizes or derives a fraud angle when the model returns blank/unknown output.
- Local fraud-angle fallback should use institution type, services, public signal summary, and signal type.
- Should not scrape, search, validate source URLs, or write CSV.

### `services/validator.py`

- Final safety and quality gate before CSV output.
- Checks:
  - email word count
  - signal/source URL consistency
  - valid HTTP/HTTPS source URL
  - no unsupported recent-signal claim when no source exists
  - no-PII positioning
- Trims emails longer than 120 words.
- If no source exists but the email says things like "I saw", "noticed", "recently announced", or "your recent", rewrite to a safe generic email.
- Should not call the LLM, scrape/search, or change the selected source.

### `services/output.py`

- Output adapter.
- Creates output directory if needed.
- Converts `EnrichedLead` objects to CSV rows.
- Writes CSV with pandas.
- Logs path, row count, and duration.
- Should not research, call LLM, or validate.

## Public Signal Discovery

The newest low-level design describes website-only signal discovery:

1. Scrape landing page.
2. Rank source-like links from landing page.
3. Fetch top source pages.
4. Discover article/detail URLs from source pages.
5. Fetch top detail pages.
6. Score `detail_pages + source_pages + landing_page`.
7. Select highest-scoring usable page.
8. Summarize with page title and first sentence.
9. Return `PublicSignal(summary, source_url, signal_type, source_title, confidence)`.

Candidate pages must have text, no fetch error, and status below 400.

Scoring uses source-page terms, article-looking URLs, and signal keywords such as fraud, scam, identity, AML, KYC, risk, ACH, wire, payment, real-time, card, partner, integration, launch, expansion, careers, and jobs.

If no source-backed signal is found:

```text
No recent verifiable public signal found.
```

`source_url` should remain empty.

## Anti-Hallucination Rules

- The LLM must never be the source of truth for public signals.
- A signal requires a source URL.
- Missing signal state must be explicit.
- If no source URL exists, the email must not imply recent news was found.
- LLM outputs must be structured JSON.
- Fallbacks should be conservative and transparent.
- Validation runs after LLM generation.

## Run Commands

Setup:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Run:

```bash
python main.py
```

Environment examples from docs:

```text
INPUT_CSV=input/leads.csv
OUTPUT_CSV=output/enriched_leads.csv
REQUEST_TIMEOUT_SECONDS=12
MAX_CONCURRENCY=5
LOG_LEVEL=INFO
```

LLM provider swapping is controlled through environment variables:

```text
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini

LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-3-5-haiku-latest
```

`services.llm.create_llm_client()` owns provider selection. Pipeline code should depend on the shared `generate_json()` interface, not provider-specific clients.

## Reliability And Error Handling

- One bad lead should not stop the full batch.
- Failed website fetches should produce warnings and continue.
- Failed signal discovery should continue with `PublicSignal.none()`.
- Failed LLM calls should use fallback classification/email.
- Logs should include company, step, request ID when available, and duration.
- Console stays simple; detailed diagnostics go to `logs/app.log`.

## Performance Notes

- Designed for small batches, around 20 leads.
- Uses async processing because scraping/API calls are network-bound.
- Uses `aiohttp` for non-blocking HTTP.
- Uses configurable request timeout and max concurrency.
- Production improvements mentioned in docs: caching, browser rendering for script-heavy sites, source freshness extraction, domain ranking, allow/deny lists, warning/audit output, unit tests, integration tests with mocked LLM/search, rate limiting, structured metrics, and retry backoff with jitter.

## Security Notes

- Do not commit `.env` or real API keys.
- If an API key appears in chat, logs, screenshots, docs, or Git history, rotate it.
- Prefer sending API keys in headers, not URL query parameters.
- Avoid logging full prompts/responses if they might include sensitive context.

## Known Documentation Mismatches

- LLM provider:
  - Current code supports `LLM_PROVIDER=openai` and `LLM_PROVIDER=anthropic`.
  - Some older docs still mention Gemini or direct `OpenAIClient` construction.
  - Use `services.llm.create_llm_client()` for pipeline provider selection.
- Public signal discovery:
  - Some docs mention DuckDuckGo or broader search.
  - Requirement/data-flow docs mention simplified landing-page-only behavior.
  - Low-level design describes a richer website-only flow that follows press/news/source links on the same site.
  - Treat website-only source discovery as current design unless source code shows otherwise.
- Output path:
  - Some docs say `output/enriched_leads.csv`.
  - Low-level design says timestamped `output/enriched_leads_YYYYMMDD_HHMMSS.csv`.
  - Confirm current `main.py`/`services/output.py` behavior before changing output tests or docs.

## Interview Summary

This project reads financial institution leads from CSV, gathers grounded public context from each company website, selects a source-backed public signal when available, asks the LLM to classify and draft outreach using only that context, validates against hallucinated claims and length limits, and writes an enriched CSV. The key architectural decision is separating factual discovery from language generation, with the validator acting as the final safety gate.
