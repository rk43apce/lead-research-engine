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
│ - assigns request_id       │
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
│ tracked by request_id      │
└─────────┬──────────────────┘
          │
          v
┌────────────────────────────┐
│ researcher.py              │
│ research(lead)             │
└─────────┬──────────────────┘
          │
          ├─────────────────────────────┐
          │                             │
          v                             v
┌──────────────────────┐      ┌────────────────────────┐
│ scraper.py           │      │ DuckDuckGo Search       │
│ fetch homepage/about │      │ fraud/risk signals      │
│ extract page text    │      │ partnerships/payments   │
└─────────┬────────────┘      └───────────┬────────────┘
          │                               │
          └───────────────┬───────────────┘
                          v
              ┌──────────────────────┐
              │ ResearchContext       │
              │ - lead                │
              │ - about_text          │
              │ - search_results      │
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
 → Lead objects with request_id
 → Research company website + DuckDuckGo
 → Build grounded ResearchContext
 → Gemini classifies company
 → Gemini writes email
 → Validator checks safety
 → Write enriched CSV
```

## Step-by-Step Explanation

1. `main.py` reads `input/leads.csv`.
2. Each valid row becomes a `Lead`.
3. Each `Lead` gets a `request_id` for log tracking.
4. `run_pipeline()` creates reusable services.
5. `process_lead()` handles each company asynchronously.
6. `researcher.py` coordinates website scraping and DuckDuckGo search.
7. `scraper.py` fetches homepage/about page text.
8. DuckDuckGo search finds public fraud/risk/payment/compliance signals.
9. The selected signal is stored in `ResearchContext`.
10. `email_generator.py` sends grounded context to Gemini.
11. `llm.py` calls Gemini and returns parsed JSON.
12. Gemini classification becomes `LLMResearchOutput`.
13. Gemini email generation becomes `EmailDraft`.
14. `validator.py` checks the email and signal.
15. The final result becomes `EnrichedLead`.
16. `main.py` writes `output/enriched_leads.csv`.

## Logging Flow

Every important step logs:

```text
company=<company name>
request_id=<lead request id>
step=<pipeline step>
```

Example:

```text
company=Navy Federal Credit Union | step=research | request_id=ba83eae40618 | Research complete
```

To debug one lead, search the log file for:

```text
request_id=<id>
```

That shows the full flow for that lead from start to finish.
