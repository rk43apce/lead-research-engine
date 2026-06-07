# Today's Work Summary

## 1. Lead Generation Automation

Built an automated lead generation flow so the project no longer depends only on manually prepared CSV files.

What changed:

- Added a lead generation command.
- Uses LLM-assisted lead sourcing.
- Generates website-backed leads for the pipeline.
- Automatically backs up the old lead CSV with date/time before creating a new one.
- Added a shell script that first generates leads and then runs the full pipeline.

Business value:

- Reduces manual lead-list preparation.
- Keeps the prospect list closer to the actual target market.
- Makes the pipeline easier to rerun for fresh leads.

## 2. LLM Provider Switching

Improved the LLM layer so the pipeline is not locked to one provider.

What changed:

- Added a shared `generate_json()` interface.
- Added provider factory logic.
- Current supported providers:
  - OpenAI
  - Anthropic
- Provider can be changed through environment variables instead of code changes.

Example:

```env
LLM_PROVIDER=openai
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4.1-mini
```

Or:

```env
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-3-5-haiku-latest
```

Business value:

- Easier to switch vendors.
- Reduces dependency on one LLM provider.
- Makes future model upgrades simpler.

## 3. Deeper Website Scraping

Improved website research depth so the system gathers better company context.

What changed:

- Scraper now follows links up to three levels deep.
- Current crawl pattern:

```text
homepage -> source/contact pages -> detail pages -> third-level pages
```

- Research now checks more useful page types:
  - news
  - press
  - investor
  - careers
  - about
  - contact
  - leadership
  - fraud/security/risk pages

Business value:

- Better company research.
- Higher chance of finding public signals.
- Better chance of finding useful recipient email pages.
- More grounded email personalization.

## 4. Token Reduction And Better Personalization

Reduced LLM prompt size by extracting compact research facts locally before calling the model.

What changed:

- Instead of sending large raw website text to the LLM, the system now extracts compact facts first.
- Extracted facts include:
  - institution hint
  - customer clues
  - services
  - risk clues
  - evidence snippets
  - source URLs

Before:

```text
Send large scraped webpage text to LLM
```

After:

```text
Scrape website
Extract compact research facts locally
Send only focused facts to LLM
```

Business value:

- Lower LLM token usage.
- Lower cost per run.
- Faster model calls.
- More personalized emails because prompts include specific services, customer clues, and risk context.
