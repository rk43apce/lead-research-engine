# Services Folder Documentation

This document explains the role and responsibility of every file inside the `services/` folder.

The project is intentionally split into small service files so each part has one clear job. This makes the pipeline easier to test, debug, and explain during a technical interview.

## High-Level Service Flow

```text
main.py
  |
  v
services.pipeline.run_pipeline()
  |
  v
services.lead.load_leads()
  |
  v
For each lead:
  1. services.researcher researches public context
  2. services.scraper fetches website text
  3. services.llm calls Gemini
  4. services.email_generator creates classification and email
  5. services.validator checks safety rules
  6. main.py writes final CSV
```

The most important design rule is:

```text
Research finds facts.
Gemini summarizes and writes.
Validator protects the final output.
```

## `services/__init__.py`

### Responsibility

Marks the `services` folder as a Python package.

### Why It Exists

It allows imports like:

```python
from services.scraper import AsyncScraper
from services.validator import LeadValidator
```

### What It Should Not Do

This file should not contain business logic. Keeping it empty is fine for this project.

## `services/config.py`

### Responsibility

Loads application settings from environment variables.

### What It Handles

- Gemini API key
- Gemini model name
- request timeout
- max concurrency
- input CSV path
- output CSV path
- log level
- log file path

### Why This Is Useful

Configuration stays outside the code, so the same code can run locally, in a demo, or in production with different settings.

### Example

```python
settings = Settings.from_env()
```

### Interview Explanation

`config.py` centralizes runtime configuration. The rest of the code reads from a `Settings` object instead of calling `os.getenv()` everywhere.

## `services/models.py`

### Responsibility

Defines the data objects passed between services.

### Important Models

`Lead`

Represents one row from the input CSV.

Fields:
- `company`
- `website`

`PageContent`

Represents scraped website content.

Fields:
- `url`
- `title`
- `text`
- `status_code`
- `error`

`PublicSignal`

Represents the selected source-backed signal.

Fields:
- `summary`
- `source_url`
- `signal_type`
- `source_title`
- `confidence`

`ResearchContext`

Contains all grounded research for one company.

Fields:
- original lead
- homepage URL
- scraped text
- search results
- selected public signal
- errors

`LLMResearchOutput`

Represents Gemini's classification output.

Fields:
- institution type
- customer segment
- services
- fraud angle

`EmailDraft`

Represents generated email text plus warnings.

`EnrichedLead`

Represents the final output row written to CSV.

### Interview Explanation

`models.py` is the shared language of the application. Each service accepts and returns structured objects instead of passing loose dictionaries everywhere.

## `services/logger.py`

### Responsibility

Provides simple logging helpers for the whole project.

### What It Handles

- rotating file logs
- timestamps
- log levels
- company name
- step name
- extra fields
- basic secret redaction

### Main Functions

```python
configure_logging()
log_info()
log_warning()
log_error()
log_debug()
log_timing()
```

### Why Company And Step Matter

Each important log line includes the company name and pipeline step, so we can trace the flow without making demo logs too noisy:

```text
CSV row -> research -> scrape -> search -> Gemini -> validation -> output
```

### Interview Explanation

`logger.py` keeps logging consistent across the pipeline without building a custom logging framework.

## `services/lead.py`

### Responsibility

Reads the input CSV and converts rows into `Lead` objects.

### What It Handles

- checks that the input CSV exists
- reads CSV with pandas
- validates required `company` column
- skips rows with missing company names
- reads optional `website` column
- logs valid and invalid row counts

### Why It Exists Separately

CSV parsing is input handling. It should not live inside `main.py` or the research logic.

### Interview Explanation

`lead.py` is the input adapter. It turns raw CSV rows into clean internal `Lead` objects.

## `services/pipeline.py`

### Responsibility

Orchestrates the full workflow for all leads.

### Main Functions

`run_pipeline()`

Creates the services once, loads leads, starts async processing, and returns enriched leads.

`process_lead()`

Processes one company from start to finish.

### Per-Lead Flow

```python
context = await researcher.research(lead)
classification = await generator.classify_context(context)
draft = await generator.generate_email(context, classification)
draft = validator.validate_email(draft, context.public_signal)
signal_warnings = validator.validate_signal(context.public_signal)
```

### Why Services Are Created Once

The scraper, researcher, Gemini client, generator, and validator are shared across lead tasks. This avoids recreating the same objects for every company.

### Why Async Is Used

Research and scraping spend most of their time waiting on the network. Async lets the system process multiple leads faster without using heavy threading.

### Why A Semaphore Is Used

The semaphore limits concurrency so the app does not overwhelm websites or the Gemini API.

### Interview Explanation

`pipeline.py` is the conductor. It does not scrape, search, write prompts, or validate rules itself. It calls the right service in the right order.

## `services/scraper.py`

### Responsibility

Fetches basic website text from the landing page URL provided in the CSV.

### What It Handles

- normalizes URLs
- adds `https://` if missing
- fetches pages using `aiohttp`
- retries temporary network failures
- checks content type
- parses HTML with BeautifulSoup
- removes non-content tags
- extracts title and readable text
- limits text size for prompt safety

### Important Functions

`normalize_url()`

Makes sure URLs are valid for HTTP requests.

`AsyncScraper.fetch()`

Fetches and parses one URL.

`AsyncScraper.fetch_landing_page()`

Fetches only the landing page URL from the CSV.

### What It Should Not Do

The scraper should not decide institution type, fraud angle, or signal quality. It only collects clean website text.

### Interview Explanation

`scraper.py` is a lightweight content collector. It gives the research and LLM layers clean text, but it does not make business decisions.

## `services/researcher.py`

### Responsibility

Collects factual public context from the company landing page.

### What It Handles

- calls the scraper for landing page context
- stores clean landing page text in `ResearchContext`
- returns `PublicSignal.none()` because this simplified flow does not search external signals

### Anti-Hallucination Rule

If the researcher cannot find a source URL, it does not invent a signal.

```python
return PublicSignal.none()
```

### What It Should Not Do

The researcher should not write emails or call Gemini. It only collects grounded website context.

### Interview Explanation

`researcher.py` is the factual grounding layer. It decides what public information is safe enough to pass to Gemini.

## `services/llm.py`

### Responsibility

Calls the Gemini API and returns parsed JSON.

### What It Handles

- builds the Gemini request
- sends the prompt to Gemini
- uses low temperature for conservative output
- asks for JSON response format
- uses `aiohttp` for non-blocking async API calls
- parses JSON
- validates that the response is a dictionary
- avoids logging full prompts or full responses

### Why `aiohttp` Is Used

The rest of the pipeline is async, so the Gemini client also uses `aiohttp`. This keeps API calls non-blocking and avoids using a worker thread for each Gemini request.

### What It Should Not Do

The Gemini client should not decide business logic, choose signals, validate hallucinations, or write CSV rows.

### Interview Explanation

`llm.py` is only an API wrapper. It sends prompts to Gemini and returns structured JSON.

## `services/prompts.py`

### Responsibility

Builds the prompts sent to Gemini.

### Main Prompts

`research_prompt()`

Asks Gemini to classify the company using only grounded context.

Expected JSON:

```json
{
  "institution_type": "string",
  "customer_segment": "string",
  "services": ["string"],
  "fraud_angle": "string"
}
```

`email_prompt()`

Asks Gemini to write one conservative cold email for The PreCogs.

Expected JSON:

```json
{
  "email": "string"
}
```

### Prompt Safety Rules

The prompts tell Gemini:
- use only supplied context
- do not invent facts
- mention recent signals only when a source exists
- stay under 120 words
- position The PreCogs as augmentation, not replacement
- mention no-PII naturally
- return valid JSON only

### Interview Explanation

`prompts.py` keeps prompt design separate from API calling. This makes it easier to tune prompts without touching Gemini request code.

## `services/email_generator.py`

### Responsibility

Turns grounded research context into two LLM outputs:

1. company classification
2. personalized email draft

### What It Handles

- builds classification prompt
- calls Gemini for structured classification
- builds email prompt
- calls Gemini for email draft
- returns safe fallback classification if Gemini fails
- returns safe fallback email if Gemini fails

### Why Fallbacks Exist

One bad API call should not break the full CSV batch. If Gemini fails, the pipeline still produces a safe row with conservative output.

### What It Should Not Do

This file should not scrape websites, collect research context, or validate source URLs.

### Interview Explanation

`email_generator.py` converts research context into LLM-generated output. It depends on grounded facts and has safe fallbacks when the LLM fails.

## `services/validator.py`

### Responsibility

Acts as the final safety layer before the row is written to CSV.

### What It Handles

- validates signal URLs
- checks that sourced signals have summaries
- normalizes email whitespace
- enforces 120-word email limit
- detects unsupported recent-signal claims
- rewrites unsafe emails when no source URL exists
- warns if the no-PII angle is missing

### Anti-Hallucination Protection

If no source URL exists, the email should not say:

```text
I saw...
noticed...
recently announced...
your recent...
```

If it does, the validator rewrites the email to a safe generic version.

### What It Should Not Do

The validator should not call Gemini, search the web, or change the selected source. It only checks safety rules.

### Interview Explanation

`validator.py` is the final quality gate. It protects against long emails, unsupported claims, missing source discipline, and missing no-PII positioning.

## `services/output.py`

### Responsibility

Writes the final enriched leads to the output CSV.

### What It Handles

- creates the output folder if needed
- converts each `EnrichedLead` into a CSV row
- writes `output/enriched_leads.csv`
- logs output path, row count, and duration

### What It Should Not Do

This file should not research companies, call Gemini, or validate email quality. It only handles final CSV writing.

### Interview Explanation

`output.py` is the output adapter. It keeps CSV writing out of `main.py`, so `main.py` stays focused on startup and orchestration.

## Service Ownership Summary

| File | Main Responsibility |
| --- | --- |
| `__init__.py` | Marks `services` as a Python package |
| `config.py` | Loads runtime settings from environment variables |
| `models.py` | Defines shared dataclasses and enums |
| `logger.py` | Provides simple file logging helpers |
| `lead.py` | Reads and validates input CSV rows |
| `pipeline.py` | Orchestrates the full lead processing flow |
| `scraper.py` | Fetches and cleans landing page text |
| `researcher.py` | Collects grounded context from the provided company URL |
| `llm.py` | Calls Gemini and parses JSON |
| `prompts.py` | Builds safe, grounded prompts |
| `email_generator.py` | Generates classification and email draft |
| `validator.py` | Applies final safety and quality checks |
| `output.py` | Writes final enriched CSV output |

## Why This Architecture Is Interview-Friendly

Each file has one clear responsibility:

- Input handling is separate from processing.
- Scraping is separate from research selection.
- Research is separate from LLM generation.
- Prompt construction is separate from API calls.
- Validation happens after generation.
- Logging is reusable across all services.

This makes the system easier to explain, test, and debug.

## Best One-Minute Explanation

This project reads leads from CSV, researches each company, selects a source-backed public signal, sends only grounded context to Gemini, generates a conservative cold email for The PreCogs, validates the result, and writes an enriched CSV. The `services/` folder keeps each responsibility separate so the LLM never owns factual discovery and the validator can catch unsupported claims before output.
