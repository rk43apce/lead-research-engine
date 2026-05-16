# Low Level Design

This document explains the pipeline from CSV input to final enriched CSV output.

## 1. Program Start

File: `main.py`

The application starts in `main()`.

```python
settings = Settings.from_env()
input_path = settings.input_csv
output_path = _timestamped_output_path(settings.output_csv)
```

Responsibilities:

- Load environment settings.
- Configure logging.
- Run the async pipeline.
- Write a timestamped output CSV.

The output path is timestamped so every run creates a new file:

```text
output/enriched_leads_YYYYMMDD_HHMMSS.csv
```

## 2. Configuration

File: `services/config.py`

`Settings.from_env()` reads runtime configuration from `.env`.

Important values:

```text
OPENAI_API_KEY
OPENAI_MODEL
INPUT_CSV
OUTPUT_CSV
REQUEST_TIMEOUT_SECONDS
MAX_CONCURRENCY
LOG_LEVEL
LOG_FILE
```

This keeps secrets, file paths, and runtime tuning outside the code.

## 3. Pipeline Setup

File: `services/pipeline.py`

`run_pipeline()` loads leads and creates shared services.

```python
leads = load_leads(input_path)

scraper = AsyncScraper(...)
researcher = CompanyResearcher(scraper=scraper)
llm = OpenAIClient(...)
generator = LeadContentGenerator(llm)
validator = LeadValidator()
```

Each class has one responsibility:

- `AsyncScraper`: fetch and parse website pages.
- `CompanyResearcher`: build grounded research context and find public signals.
- `OpenAIClient`: call OpenAI and return parsed JSON.
- `LeadContentGenerator`: classify context and generate email content.
- `LeadValidator`: enforce final safety and quality checks.

The pipeline uses:

```python
asyncio.Semaphore(settings.max_concurrency)
```

to limit how many leads are processed at the same time.

## 4. Per-Lead Processing

File: `services/pipeline.py`

Each lead goes through `process_lead()`.

```python
context = await researcher.research(lead)
classification = await generator.classify_context(context)
draft = await generator.generate_email(context, classification)
draft = validator.validate_email(draft, context.public_signal, company=company)
validator.validate_signal(context.public_signal, company=company)
```

The final row is created as:

```python
EnrichedLead(
    company=lead.company,
    institution_type=classification.institution_type,
    fraud_angle=classification.fraud_angle,
    signal=context.public_signal.summary,
    source_url=context.public_signal.source_url,
    email=draft.email,
)
```

Column ownership:

- `company`: input CSV.
- `institution_type`: LLM classification.
- `fraud_angle`: LLM classification or local fallback.
- `signal`: researcher-selected public signal.
- `source_url`: researcher-selected source URL.
- `email`: LLM generated email or safe fallback.

## 5. Lead Loading

File: `services/lead.py`

The input CSV must contain:

```text
company
```

Optional column:

```text
website
```

Each valid row becomes a `Lead` object.

```python
Lead(company="Capital One", website="https://www.capitalone.com")
```

Invalid rows are logged and skipped, so one bad row does not stop the run.

## 6. Research Start

File: `services/researcher.py`

The `research()` method creates the main context object.

```python
context = ResearchContext(lead=lead)
```

If no website exists, the pipeline returns a safe empty signal:

```python
context.public_signal = PublicSignal.none()
```

If a website exists, the homepage is scraped:

```python
page = await self.scraper.fetch_landing_page(lead.website, company=company)
context.homepage_url = page.url
context.about_text = page.text[:15000]
```

Then public signal discovery runs:

```python
signal = await self._find_public_signal(page, company=company)
context.public_signal = signal
```

If a signal is found, it is appended to the context sent to the LLM:

```python
context.about_text = self._append_signal_context(context.about_text, signal, limit=15000)
```

## 7. Scraping

File: `services/scraper.py`

`AsyncScraper` fetches pages and converts HTML into structured page data.

Important methods:

```python
fetch_landing_page()
fetch_pages()
fetch()
_parse_html()
_extract_links()
```

`fetch()` performs the HTTP request with retries.

```python
async with session.get(normalized_url, allow_redirects=True) as response:
```

Only HTML pages are parsed. Unsupported content types are skipped.

## 8. HTML Parsing

File: `services/scraper.py`

`_parse_html()` uses BeautifulSoup.

It extracts:

- Page title.
- Clean readable text.
- Normalized links.
- HTTP status code.

It removes non-content tags:

```python
for tag in soup(["script", "style", "noscript", "svg"]):
    tag.decompose()
```

It returns:

```python
PageContent(
    url=url,
    title=title,
    text=text,
    status_code=status_code,
    links=links,
)
```

## 9. Link Extraction

File: `services/scraper.py`

`_extract_links()` reads all anchor tags.

```python
for anchor in soup.find_all("a", href=True):
```

It skips:

- `mailto:`
- `tel:`
- `javascript:`
- non-HTTP links
- duplicate URLs

Relative links are converted into absolute URLs:

```python
absolute_url = urljoin(base_url, raw_href)
```

Each link becomes:

```python
PageLink(url=absolute_url, text=link_text[:180])
```

## 10. Public Signal Discovery

File: `services/researcher.py`

The core method is:

```python
async def _find_public_signal(self, landing_page: PageContent, company: str) -> PublicSignal:
```

This replaced DuckDuckGo search with website-only discovery.

The method does four main things:

1. Rank homepage links that look like source pages.
2. Fetch those source pages.
3. Discover detail article URLs from those source pages.
4. Select the best source-backed signal page.

## 11. Source Link Ranking

File: `services/researcher.py`

The homepage links are ranked by:

```python
candidate_links = self._rank_source_links(landing_page.links, landing_page.url)
candidate_urls = [link.url for link in candidate_links[:10]]
```

Source-like links include:

```text
press
press release
news
newsroom
media
investor
announcements
updates
blog
careers
jobs
```

The ranking checks that the link belongs to the same company site:

```python
if not self._same_company_site(landing_url, link.url):
    continue
```

Then it scores the link using:

- `SOURCE_PAGE_BONUS`
- `SIGNAL_KEYWORDS`
- `ARTICLE_URL_PATTERN`

## 12. Signal Keywords

File: `services/researcher.py`

Signal categories are defined in `SIGNAL_KEYWORDS`.

```python
SignalType.FRAUD_RISK
SignalType.COMPLIANCE
SignalType.PAYMENTS
SignalType.PARTNERSHIP
SignalType.EXPANSION
SignalType.HIRING
SignalType.PRESS
```

Examples:

```python
SignalType.PAYMENTS: ("ach", "wire", "payment", "payments", "real-time", "instant", "card")
SignalType.PARTNERSHIP: ("partner", "partnership", "integrat", "collaboration")
SignalType.FRAUD_RISK: ("fraud", "scam", "identity", "aml", "kyc", "risk")
```

These categories help select and classify the public signal.

## 13. Fetch Source Pages

File: `services/researcher.py`

After ranking source links:

```python
pages = await self.scraper.fetch_pages(candidate_urls, company=company)
```

Examples of source pages:

```text
/newsroom
/press
/investor-relations
/media
/careers
```

These pages often contain links to actual announcements or articles.

## 14. Discover Detail Pages

File: `services/researcher.py`

`_discover_detail_urls()` looks inside source pages.

Example:

```text
/newsroom
  -> /news/2026/new-payment-launch
  -> /news/2026/fintech-partnership
  -> /news/2026/fraud-prevention-update
```

The method scores detail links using the same keyword strategy.

Then the pipeline fetches the top detail pages:

```python
detail_pages = await self.scraper.fetch_pages(detail_urls[:10], company=company)
```

## 15. Select Best Signal Page

File: `services/researcher.py`

The researcher considers:

```python
detail_pages + pages + [landing_page]
```

Then:

```python
best_page = self._select_signal_page(...)
```

Usable pages must have:

- Text.
- No fetch error.
- HTTP status below 400.

Each usable page is scored by `_score_signal_page()`.

The highest scoring page becomes the signal source.

## 16. Score Signal Page

File: `services/researcher.py`

`_score_signal_page()` builds a searchable string:

```python
haystack = "%s %s %s" % (
    page.title.lower(),
    page.url.lower(),
    page.text[:5000].lower(),
)
```

Then it adds points for:

- Source-page terms like `news`, `press`, `investor`.
- Article-looking URLs.
- Signal keywords like `fraud`, `ACH`, `wire`, `partnership`, `launch`, `hiring`.

## 17. Classify Signal Type

File: `services/researcher.py`

`_classify_signal_type()` determines the category of the selected signal.

```python
signal_type = self._classify_signal_type(best_page)
```

Non-press categories are weighted higher than generic press terms.

That means:

```text
press page about ACH payments -> payments
press page about fintech partnership -> partnership
press page about fraud hiring -> fraud_risk or hiring
```

## 18. Summarize Signal

File: `services/researcher.py`

`_summarize_signal()` creates a short source-backed summary using:

- Page title.
- First sentence from page text.

Then `_find_public_signal()` returns:

```python
PublicSignal(
    summary=summary,
    source_url=best_page.url,
    signal_type=signal_type,
    source_title=best_page.title,
    confidence=0.72,
)
```

This feeds the final CSV `signal` and `source_url` columns.

## 19. Research Context Sent to LLM

File: `services/prompts.py`

The classification prompt receives:

```python
payload = {
    "company": context.lead.company,
    "website": context.lead.website,
    "homepage_url": context.homepage_url,
    "homepage_and_about_text": context.about_text[:12000],
    "public_signal": {
        "summary": context.public_signal.summary,
        "source_url": context.public_signal.source_url,
        "signal_type": context.public_signal.signal_type.value,
    },
}
```

The LLM is instructed to:

- Use only supplied context.
- Not invent services, facts, customer segments, or signals.
- Return valid JSON only.
- Always provide a non-blank fraud angle.

## 20. OpenAI Call

File: `services/llm.py`

`OpenAIClient.generate_json()` sends the prompt to OpenAI.

```python
body = {
    "model": self.model,
    "input": prompt,
    "temperature": 0.2,
    "text": {
        "format": {
            "type": "json_object",
        },
    },
}
```

The request goes to:

```text
https://api.openai.com/v1/responses
```

The client logs:

- Request payload.
- Raw response.
- Response text.

Then it parses and returns a Python dictionary.

## 21. Institution Type Detection

File: `services/email_generator.py`

`classify_context()` calls the LLM:

```python
data = await self.llm.generate_json(
    prompt,
    operation="openai_classification",
    company=company,
)
```

Then it extracts:

```python
institution_type = str(data.get("institution_type") or "Unknown financial institution")
customer_segment = str(data.get("customer_segment") or "Unknown")
services = data.get("services") or []
```

This becomes:

```python
LLMResearchOutput(
    institution_type=institution_type,
    customer_segment=customer_segment,
    services=normalized_services,
    fraud_angle=fraud_angle,
)
```

## 22. Fraud Angle Detection

File: `services/email_generator.py`

The model may return a useful fraud angle, or it may return `Unknown`.

The app handles that with:

```python
fraud_angle = self._normalize_fraud_angle(...)
```

If the value is blank, `Unknown`, `N/A`, `none`, `null`, or `-`, the app derives a conservative fraud angle locally.

The fallback uses:

- Institution type.
- Services.
- Public signal summary.
- Public signal type.

Examples:

Payments:

```text
Payment activity can create pressure to monitor ACH, wire, card, and account takeover risk without adding PII exposure.
```

Partnership:

```text
Partnerships and integrations can expand fraud review complexity across onboarding, transaction monitoring, and third-party workflows.
```

Credit union:

```text
Member-facing digital banking can expose credit unions to account takeover, identity fraud, and payment-risk review needs.
```

## 23. Email Generation

File: `services/email_generator.py`

`generate_email()` builds an email prompt using:

- Company.
- Institution type.
- Fraud angle.
- Signal summary.
- Source URL.
- Whether a real signal exists.
- Signal type.

Rules in the prompt:

- Under 120 words.
- Mention recent signal only if `has_signal` is true.
- Do not invent facts.
- Connect context to fraud or risk challenge.
- Mention no-PII naturally.
- Return JSON only.

## 24. Validation

File: `services/validator.py`

The validator protects the output after the LLM responds.

It checks:

- Email word count.
- No unsupported recent-signal language when no source exists.
- No-PII positioning.
- Signal/source URL consistency.

This keeps the final CSV safer even if the model output is imperfect.

## 25. Output Model

File: `services/models.py`

The final row is represented by:

```python
EnrichedLead
```

It writes:

```python
{
    "company": self.company,
    "institution_type": self.institution_type,
    "fraud_angle": self.fraud_angle,
    "signal": self.signal,
    "source_url": self.source_url,
    "email": self.email,
}
```

## 26. CSV Output

File: `services/output.py`

`write_output()` turns rows into a DataFrame and writes CSV.

```python
pd.DataFrame(output_rows).to_csv(path, index=False)
```

The path is timestamped by `main.py`, so each run produces a separate file.

## End-to-End Flow

```text
input/leads.csv
  -> load_leads()
  -> Lead(company, website)
  -> CompanyResearcher.research()
  -> AsyncScraper.fetch_landing_page()
  -> PageContent(homepage text + links)
  -> _find_public_signal()
  -> rank source links
  -> fetch source pages
  -> discover detail URLs
  -> fetch detail pages
  -> select best signal page
  -> PublicSignal(summary, source_url, signal_type)
  -> ResearchContext(homepage text + public signal)
  -> LeadContentGenerator.classify_context()
  -> OpenAIClient.generate_json()
  -> LLMResearchOutput(institution_type, services, fraud_angle)
  -> normalize or derive fraud_angle if needed
  -> LeadContentGenerator.generate_email()
  -> LeadValidator
  -> EnrichedLead
  -> timestamped output CSV
```

## Demo Summary

The low-level design is a staged pipeline:

```text
CSV input
  -> scraping
  -> website-only signal discovery
  -> grounded LLM classification
  -> fraud angle normalization
  -> email generation
  -> validation
  -> timestamped CSV output
```

The most important design decision is separation of responsibilities. The scraper only collects website data, the researcher selects a source-backed signal, the LLM classifies and drafts using only grounded context, the fallback logic guarantees a useful fraud angle, and the validator protects the final output.
