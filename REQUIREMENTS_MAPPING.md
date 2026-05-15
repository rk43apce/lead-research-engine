# Requirements Mapping

This document explains what the assignment asks for and how this project satisfies each requirement.

## Project Goal

Build an AI-powered lead research and cold email generation pipeline for The PreCogs, an AI fraud prevention company.

The system is not a fraud detection engine. It is a lead research and personalized outreach tool.

For each financial institution, it should:

1. Research the company
2. Identify institution type and customer context
3. Identify a relevant fraud/risk angle
4. Find a recent verifiable public signal
5. Generate a grounded cold email under 120 words
6. Output an enriched CSV

## Requirement 1: Visit Prospect Website And Understand Institution Type

### Requirement

For each company, visit the prospect's website and understand what kind of financial institution they are:

- community bank
- regional bank
- national bank
- payments processor
- credit union
- other financial institution type

Also identify who they serve.

### How The Project Handles It

Website scraping is handled by:

```text
services/scraper.py
```

Research orchestration is handled by:

```text
services/researcher.py
```

Classification is handled by:

```text
services/email_generator.py
services/prompts.py
```

Flow:

```text
Lead website
  -> AsyncScraper.fetch_landing_page()
  -> ResearchContext.about_text
  -> research_prompt()
  -> Gemini classification
  -> LLMResearchOutput
```

The scraper fetches only the landing page URL provided in `input/leads.csv`.

Then Gemini receives the scraped text and returns:

```text
institution_type
customer_segment
services
fraud_angle
```

### Why This Design

The scraper only collects website text. It does not decide business meaning.

Gemini is better suited for summarizing and classifying unstructured website text, but it is only allowed to use grounded context provided by the research layer.

## Requirement 2: Identify Relevant Fraud Surface Area

### Requirement

Identify the fraud surface area most relevant to the institution's business.

Examples:

- credit union: account takeover, member scams, loan fraud
- payments processor: ACH fraud, payment fraud, transaction monitoring
- bank: digital banking fraud, wire fraud, compliance risk

### How The Project Handles It

Fraud angle generation happens during classification:

```text
services/prompts.py
services/email_generator.py
```

The prompt asks Gemini to return:

```json
{
  "fraud_angle": "string"
}
```

The prompt also tells Gemini:

```text
Use only the supplied context.
Do not invent services, facts, customer segments, or signals.
If context is insufficient, use Unknown or cautious wording.
```

### Why This Design

The fraud angle is not guessed from the company name alone. It is generated from:

- scraped website text
- selected public signal
- institution type
- services found in context

This keeps the outreach relevant without pretending to perform fraud detection.

## Requirement 3: Find A Recent Verifiable Public Signal

### Requirement

Find at least one specific, recent, verifiable signal that makes the outreach timely.

Good examples:

- fraud or risk job posting
- regulatory action or consent order
- product/service launch
- fintech partnership
- leadership statement about fraud/risk
- industry group membership

### Current Simplified Handling

The current simplified demo flow does not use a search engine because DuckDuckGo HTML scraping can return bot/challenge pages.

Instead:

- the system scrapes only the landing page URL from the CSV
- it uses that page as grounded company context
- it does not invent a recent signal
- it returns `PublicSignal.none()` unless a future search/source layer is added

Landing page research is handled by:

```text
services/researcher.py
```

### Anti-Hallucination Rule

If no source-backed result is found, the code returns:

```python
PublicSignal.none()
```

That means the system safely says:

```text
No recent verifiable public signal found.
```

It does not invent one.

### Why This Design

The assignment values verifiable outreach. A signal without a URL is not useful, so the project only outputs a signal when a source URL exists.

## Requirement 4: Generate A Personalized Email Under 120 Words

### Requirement

Generate a personalized cold email that:

- opens with the specific signal
- connects the signal to a fraud problem
- positions The PreCogs as augmentation, not replacement
- mentions no-PII angle naturally
- ends with a low-friction CTA
- stays under 120 words

### How The Project Handles It

Email generation is handled by:

```text
services/email_generator.py
services/prompts.py
```

The email prompt includes hard rules:

```text
Under 120 words.
Mention the recent signal first only if has_signal is true.
If has_signal is false, say you could not find a recent public signal.
Do not cite facts beyond the supplied payload.
Position The PreCogs as augmenting existing teams and workflows.
Mention the no-PII angle naturally.
End with a low-friction CTA.
Return valid JSON only.
```

The validator then checks the result:

```text
services/validator.py
```

Validator checks:

- email word count
- unsupported recent-signal claims
- missing no-PII angle
- signal URL validity

If the email is longer than 120 words, it trims it.

If no source URL exists but the email sounds like it found recent news, it rewrites the email safely.

### Why This Design

The LLM creates the email, but the validator protects the final output. This is important because LLMs can be useful writers, but they should not be trusted without guardrails.

## Requirement 5: Output Required CSV Columns

### Requirement

Output a CSV with:

```text
company
institution_type
fraud_angle
signal
source_url
email
```

### How The Project Handles It

The final output model is:

```text
services/models.py -> EnrichedLead
```

The CSV row is created by:

```python
EnrichedLead.to_csv_row()
```

It returns exactly:

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

The CSV is written in:

```text
main.py
```

Output path:

```text
output/enriched_leads.csv
```

### Why This Design

The final model mirrors the assignment output. Internal warnings and logs are not written to CSV because they are debugging metadata, not requested output fields.

## End-To-End Flow

```text
input/leads.csv
  |
  v
services.lead.load_leads()
  |
  v
services.pipeline.run_pipeline()
  |
  v
For each lead:
  |
  +-- services.researcher.research()
  |     |
  |     +-- services.scraper.fetch_landing_page()
  |     +-- safe no-signal fallback
  |
  +-- services.email_generator.classify_context()
  |     |
  |     +-- services.prompts.research_prompt()
  |     +-- services.llm.GeminiClient.generate_json()
  |
  +-- services.email_generator.generate_email()
  |     |
  |     +-- services.prompts.email_prompt()
  |     +-- services.llm.GeminiClient.generate_json()
  |
  +-- services.validator.validate_email()
  +-- services.validator.validate_signal()
  |
  v
main.py writes output/enriched_leads.csv
```

## Reliability And Safety Choices

### Separate Research From LLM

The research layer finds facts and source URLs.

The LLM layer only receives grounded context.

This prevents Gemini from becoming the source of truth.

### Source URL Required For Signals

If no source URL exists, the system does not invent a signal.

This satisfies the no-hallucinated-facts requirement.

### Structured JSON From Gemini

Gemini is asked to return JSON, not free-form text.

This makes parsing and validation easier.

### Validator As Final Safety Gate

The validator checks:

- email length
- unsupported signal claims
- no-PII positioning
- signal URL quality

### Async Processing

The pipeline uses async processing because scraping and search are network-bound.

This helps process multiple leads faster.

### Logging And Request IDs

Each lead gets a request ID.

Logs include:

- company name
- processing step
- request ID
- duration

This makes it easier to debug one lead without reading the whole log file manually.

## What Happens If Data Is Missing

### Missing Website

The scraper is skipped, and the system safely continues with no public signal.

### No Public Signal

The system returns:

```text
No recent verifiable public signal found.
```

The email must avoid pretending that a recent signal exists.

### Gemini Failure

The email generator returns a conservative fallback instead of crashing the whole batch.

### One Lead Fails

The pipeline returns a safe failed row for that company and continues processing other leads.

## Interview Summary

The system satisfies the assignment by using a grounded research-first workflow:

```text
Scrape and search first.
Select only source-backed signals.
Pass grounded context to Gemini.
Generate conservative outreach.
Validate before output.
Write structured CSV.
```

The most important architectural decision is separating factual discovery from language generation. That makes the system safer, easier to debug, and easier to explain.
