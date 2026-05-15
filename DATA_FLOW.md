# Data Flow Diagram

This document explains how data moves through the AI lead research and cold email generation pipeline from input CSV to final enriched CSV.

## End-to-End Flow

```text
┌────────────────────┐
│ input/leads.csv    │
│ company, website   │
└─────────┬──────────┘
          │
          v
┌────────────────────────────┐
│ main.py                    │
│ load_leads()               │
│ - reads CSV                │
│ - validates company column │
│ - creates Lead objects     │
└─────────┬──────────────────┘
          │
          v
┌────────────────────────────┐
│ run_pipeline()             │
│ - creates services         │
│ - runs leads async         │
│ - limits concurrency       │
└─────────┬──────────────────┘
          │
          v
┌────────────────────────────┐
│ process_lead()             │
│ One company at a time      │
└─────────┬──────────────────┘
          │
          v
┌────────────────────────────┐
│ researcher.py              │
│ research(lead)             │
└─────────┬──────────────────┘
          │
          │
          v
┌──────────────────────┐
│ scraper.py           │
│ fetch landing page   │
│ extract page text    │
└─────────┬────────────┘
          │
          v
              ┌──────────────────────┐
              │ ResearchContext       │
              │ - lead                │
              │ - about_text          │
              │ - public_signal       │
              │ - errors              │
              └──────────┬───────────┘
                         │
                         v
┌────────────────────────────────────┐
│ email_generator.py                 │
│ classify_context()                 │
│ - builds research_prompt           │
│ - sends grounded context to Gemini │
└─────────┬──────────────────────────┘
          │
          v
┌────────────────────────────┐
│ llm.py / GeminiClient      │
│ generate_json()            │
│ - calls Gemini API         │
│ - parses JSON              │
│ - returns dict             │
└─────────┬──────────────────┘
          │
          v
┌────────────────────────────┐
│ LLMResearchOutput          │
│ - institution_type         │
│ - customer_segment         │
│ - services                 │
│ - fraud_angle              │
└─────────┬──────────────────┘
          │
          v
┌────────────────────────────────────┐
│ email_generator.py                 │
│ generate_email()                   │
│ - builds email_prompt              │
│ - sends signal + fraud angle       │
│ - gets email draft                 │
│ - fallback if Gemini fails         │
└─────────┬──────────────────────────┘
          │
          v
┌────────────────────────────┐
│ EmailDraft                 │
│ - email                    │
│ - warnings                 │
└─────────┬──────────────────┘
          │
          v
┌────────────────────────────┐
│ validator.py               │
│ validate_email()           │
│ validate_signal()          │
│ - word count               │
│ - no hallucinated signal   │
│ - source URL checks        │
│ - no-PII angle             │
└─────────┬──────────────────┘
          │
          v
┌────────────────────────────┐
│ EnrichedLead               │
│ - company                  │
│ - institution_type         │
│ - fraud_angle              │
│ - signal                   │
│ - source_url               │
│ - email                    │
└─────────┬──────────────────┘
          │
          v
┌────────────────────────────┐
│ output/enriched_leads.csv  │
│ Final enriched CSV         │
└────────────────────────────┘
```

## Short Version

```text
CSV
 → Lead objects
 → Scrape landing page from CSV website URL
 → Build grounded ResearchContext
 → Gemini classifies company
 → Gemini writes email
 → Validator checks safety
 → Write enriched CSV
```

## Step-by-Step Explanation

1. `main.py` reads `input/leads.csv`.
2. Each valid row becomes a `Lead`.
3. `run_pipeline()` creates reusable services.
4. `process_lead()` handles each company asynchronously.
5. `researcher.py` coordinates landing page scraping.
6. `scraper.py` fetches the landing page text.
7. The landing page text is stored in `ResearchContext`.
8. Since no search engine is used, `public_signal` safely remains `PublicSignal.none()`.
9. `email_generator.py` sends grounded context to Gemini.
10. `llm.py` calls Gemini and returns parsed JSON.
11. Gemini classification becomes `LLMResearchOutput`.
12. Gemini email generation becomes `EmailDraft`.
13. `validator.py` checks the email and signal.
14. The final result becomes `EnrichedLead`.
15. `main.py` writes `output/enriched_leads.csv`.

## Logging Flow

Every important step logs:

```text
company=<company name>
step=<pipeline step>
```

Example:

```text
company=Navy Federal Credit Union | step=research | Research complete
```

To debug one lead, search the log file for:

```text
company=<company name>
```

That shows the full flow for that lead from start to finish.
