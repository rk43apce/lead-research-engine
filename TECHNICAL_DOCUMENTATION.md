# Technical Documentation

## Project Overview

This project is an automated AI lead research and cold email generation system for The PreCogs, an AI fraud prevention company.

The system takes a CSV list of financial institutions, researches each company using public web sources, identifies a fraud or risk-relevant outreach angle, and generates a personalized cold email. The final result is written as an enriched CSV file.

This is not a fraud detection system. It does not analyze transactions, score fraud risk, or make decisions about customers. It is a sales research and outreach personalization tool.

## Business Goal

The goal is to help a sales or growth team quickly prepare grounded outreach for banks, credit unions, fintechs, payment companies, and other financial institutions.

For each company, the system tries to answer:

- What type of financial institution is this?
- What services or customer segment does it appear to serve?
- Is there a recent public signal related to fraud, compliance, payments, partnerships, expansion, or hiring?
- How can that signal be connected to a conservative fraud/risk conversation?
- Can we generate a short, professional email that does not invent facts?

## Key Design Principle

The most important rule is factual grounding.

The research layer gathers public facts and source URLs. The LLM layer receives only that grounded context and is instructed not to invent missing information. If the research layer cannot find a signal with a real source URL, the system safely says that no recent verifiable public signal was found.

## High-Level Architecture

```text
Input CSV
   |
   v
main.py
   |
   v
Load leads with pandas
   |
   v
Async lead processing
   |
   +--> Scrape homepage/about pages
   |
   +--> Search DuckDuckGo for public signals
   |
   +--> Select best verifiable signal
   |
   +--> Send grounded context to Gemini
   |
   +--> Generate email
   |
   +--> Validate email and signal
   |
   v
Output enriched CSV
```

## Folder Structure

```text
.
├── input/
│   └── leads.csv
├── output/
│   ├── enriched_leads.csv
│   ├── example_enriched_leads.csv
│   └── smoke.csv
├── services/
│   ├── config.py
│   ├── email_generator.py
│   ├── llm.py
│   ├── logger.py
│   ├── models.py
│   ├── prompts.py
│   ├── researcher.py
│   ├── scraper.py
│   └── validator.py
├── main.py
├── requirements.txt
├── .env.example
├── README.md
└── TECHNICAL_DOCUMENTATION.md
```

## Main Components

### `main.py`

`main.py` is the application entrypoint.

Responsibilities:

- Load environment variables from `.env`
- Load settings from `.env`
- Read the input CSV
- Create service objects
- Process leads concurrently with `asyncio`
- Write the final CSV output
- Log progress and warnings

Main command:

```bash
python main.py
```

Input and output paths are configured through `.env`.

### `services/models.py`

This file contains the core dataclasses used across the system.

Important models:

- `Lead`: input company and optional website
- `PageContent`: scraped page result
- `SearchResult`: DuckDuckGo result
- `PublicSignal`: selected signal with source URL
- `ResearchContext`: all grounded research for one lead
- `LLMResearchOutput`: institution type, customer segment, services, and fraud angle
- `EmailDraft`: generated email and warnings
- `EnrichedLead`: final output row

Using dataclasses keeps the data flow explicit and easy to review.

### `services/scraper.py`

This service performs async web scraping.

Responsibilities:

- Normalize URLs
- Fetch homepage and likely about pages
- Parse HTML with BeautifulSoup
- Remove scripts, styles, SVGs, and noisy tags
- Extract readable text
- Apply request timeouts and retries
- Return errors without crashing the full batch

The scraper is intentionally simple. It uses `aiohttp` for speed and `BeautifulSoup4` for parsing.

### `services/researcher.py`

This service performs company research.

Responsibilities:

- Call the scraper for homepage/about context
- Search DuckDuckGo for public signals
- Run multiple targeted queries per company
- Deduplicate search results
- Normalize DuckDuckGo redirect URLs
- Select the strongest signal using keyword matching
- Return `PublicSignal.none()` if no verifiable source is found

The search queries focus on:

- Fraud and risk
- Compliance
- ACH, wire, and payments
- Partnerships
- Fintech expansion
- Press releases
- Fraud/risk hiring

The researcher does not ask the LLM to invent a signal. It only passes a signal forward if there is a source URL.

### `services/llm.py`

This file wraps the Gemini API.

Responsibilities:

- Send prompts to Gemini
- Request JSON output
- Parse JSON safely
- Retry failed requests
- Use the API key through the `x-goog-api-key` header
- Redact API keys from logs

Current default model:

```text
gemini-flash-latest
```

This model can be overridden with:

```bash
GEMINI_MODEL=gemini-flash-latest
```

### `services/prompts.py`

This file contains prompt templates.

There are two main prompts:

- `research_prompt`: asks Gemini to classify the institution and fraud/risk angle using only supplied context
- `email_prompt`: asks Gemini to generate a short cold email using only grounded context

Both prompts require valid JSON output.

The email prompt enforces:

- Under 120 words
- Mention recent signal first only when a signal exists
- Do not invent facts
- Connect the signal to a likely fraud/risk challenge
- Position The PreCogs as augmenting existing teams
- Mention no-PII naturally
- End with a low-friction CTA

### `services/email_generator.py`

This service coordinates Gemini calls.

Responsibilities:

- Generate the institution classification
- Generate the email
- Provide conservative fallback output if Gemini fails

The fallback path is important for reliability. If the Gemini key is missing, expired, rate-limited, or the request fails, the pipeline still produces a safe CSV row instead of crashing.

### `services/validator.py`

This service checks the generated output before writing the CSV.

Validation includes:

- Signal URL must be HTTP or HTTPS
- Email must be under 120 words
- Email must not claim a recent signal if no signal was found
- Email should mention the no-PII angle

If an email appears to hallucinate a signal, the validator rewrites it to a safe generic version.

### `services/config.py`

This file reads runtime settings from environment variables.

Supported settings:

```bash
GEMINI_API_KEY=your_api_key
GEMINI_MODEL=gemini-flash-latest
INPUT_CSV=input/leads.csv
OUTPUT_CSV=output/enriched_leads.csv
REQUEST_TIMEOUT_SECONDS=12
MAX_CONCURRENCY=5
SEARCH_RESULTS_PER_QUERY=5
LOG_LEVEL=INFO
```

### `services/logger.py`

This file centralizes logging setup and simple logging helpers.

Logs include:

- Number of leads loaded
- Current company being processed
- Gemini failures
- Scraper/search warnings
- Output CSV location

## Complete Runtime Flow

### Step 1: Load configuration

The program starts in `main.py`.

It loads `.env` using `python-dotenv`, then creates a `Settings` object from environment variables.

### Step 2: Read input CSV

The input CSV is read with pandas.

Required column:

```csv
company
```

Optional column:

```csv
website
```

Example:

```csv
company,website
Navy Federal Credit Union,https://www.navyfederal.org
Ally Bank,https://www.ally.com
```

Each row becomes a `Lead` object.

### Step 3: Start async processing

The pipeline creates one task per lead and runs them concurrently.

Concurrency is controlled by:

```bash
MAX_CONCURRENCY=5
```

This keeps the system fast enough for small batches while avoiding too many simultaneous web requests.

### Step 4: Scrape company website

If a website is provided, the scraper fetches:

- Homepage
- Common about page paths such as `/about`, `/about-us`, `/company`, `/who-we-are`, and `/our-story`

The extracted text is used as grounded company context.

### Step 5: Search for public signals

The researcher searches DuckDuckGo using targeted queries like:

```text
"Company Name" fraud risk banking
"Company Name" compliance risk financial institution
"Company Name" ACH wire payments launch
"Company Name" partnership fintech payments
"Company Name" press release expansion bank credit union
"Company Name" fraud analyst job
```

Search results are converted into `SearchResult` objects with:

- Title
- URL
- Snippet

### Step 6: Select the best signal

The researcher scores search results using fraud/risk/payment/compliance keywords.

If a result appears relevant and has a source URL, it becomes the selected `PublicSignal`.

If nothing reliable is found, the system uses:

```text
No recent verifiable public signal found.
```

In that case, `source_url` remains empty.

### Step 7: Classify company with Gemini

The grounded context is sent to Gemini.

Gemini returns JSON like:

```json
{
  "institution_type": "Credit union",
  "customer_segment": "Consumer and member banking",
  "services": ["checking", "loans", "credit cards"],
  "fraud_angle": "Digital banking and payment activity may increase pressure on account takeover and scam review workflows."
}
```

If Gemini fails, a safe fallback classification is used.

### Step 8: Generate cold email

Gemini receives only:

- Company name
- Institution type
- Fraud angle
- Signal summary
- Source URL
- Whether a signal exists

The generated email must be under 120 words and cannot invent facts.

### Step 9: Validate output

The validator checks the email and signal.

If the email is too long, it is trimmed.

If no signal exists but the email claims one, the email is replaced with a safe generic message.

### Step 10: Write output CSV

The final enriched rows are written to:

```text
output/enriched_leads.csv
```

Output columns:

```csv
company,institution_type,fraud_angle,signal,source_url,email
```

## Example Output

Example row:

```csv
company,institution_type,fraud_angle,signal,source_url,email
Example Credit Union,Credit union,Member-facing digital banking can increase pressure on account takeover and payment fraud workflows.,Example Credit Union announced a digital banking upgrade focused on payments.,https://example.com/news,"I saw Example Credit Union's public update about digital banking and payments. Moves like that can create more pressure around account takeover, mule activity, and payment review queues. The PreCogs helps augment fraud and risk teams with AI-driven prevention context, without replacing analyst judgment or requiring PII-heavy workflows. Would a short note on where this could fit be useful?"
```

## Error Handling Strategy

The system is designed to continue processing even when individual steps fail.

Examples:

- If a website cannot be fetched, search still runs
- If DuckDuckGo search fails, the system continues with no signal
- If Gemini fails, fallback classification and email are used
- If one lead has warnings, other leads continue processing

Warnings are logged and attached internally to the `EnrichedLead` object, but only assignment-required columns are written to the output CSV.

## Anti-Hallucination Controls

The project uses several controls to reduce hallucination risk:

- Research and LLM layers are separate
- The LLM receives only grounded context
- Prompts explicitly forbid invented facts
- Signal selection requires a source URL
- Missing signal state is represented explicitly
- Email validation checks for unsupported recent-signal language
- Gemini responses must be valid JSON
- Fallbacks are conservative and transparent

## Performance Notes

The project is designed for small lead batches, such as 20 companies.

Performance choices:

- `asyncio` for concurrent lead processing
- `aiohttp` for non-blocking scraping/search
- Request timeouts to avoid long hangs
- Limited result counts per query
- Configurable concurrency

For 20 leads, the target is under 10 minutes, depending on network speed, search responsiveness, and Gemini latency.

## Security Notes

Do not commit `.env` or real API keys.

The Gemini API key should be stored only in:

```text
.env
```

The code sends the key through the `x-goog-api-key` header rather than appending it to the URL. This avoids leaking the key through URL logs.

If a key is ever pasted into chat, logs, Git history, screenshots, or shared documents, rotate it immediately.

## How To Run Locally

Install dependencies:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create `.env`:

```bash
cp .env.example .env
```

Edit `.env`:

```bash
GEMINI_API_KEY=your_real_key
GEMINI_MODEL=gemini-flash-latest
```

Run:

```bash
python main.py
```

Input and output files are controlled with `INPUT_CSV` and `OUTPUT_CSV` in `.env`.

## Production Improvements

For a production version, the next improvements would be:

- Cache search and scraped page results
- Fetch and parse full source pages for candidate signals
- Extract publication dates from source pages
- Add source domain ranking
- Add allow/deny lists for low-quality sources
- Add persistent warning columns or a separate audit file
- Add unit tests for validators, URL normalization, and prompt parsing
- Add integration tests with mocked Gemini and search responses
- Add rate limiting per domain
- Add structured run metrics
- Add retry backoff with jitter

## Summary

This system automates lead research and cold email personalization while keeping factual grounding as the main constraint.

It reads financial institution leads, gathers public context, finds a verifiable signal when possible, asks Gemini to classify and draft outreach from that context only, validates the result, and writes a clean enriched CSV for review or sales use.
