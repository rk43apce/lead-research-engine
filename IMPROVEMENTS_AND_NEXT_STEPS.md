# Improvements And Next Steps

## Improvements Completed

### 1. Automated Lead Generation

- Added command/script to generate leads automatically.
- Uses LLM-assisted lead sourcing.
- Focuses on U.S. community banks and credit unions.
- Backs up old lead CSV files before creating a new one.

### 2. Separate Run Scripts

- Added `run_generate_leads.sh` for lead generation only.
- Added `run_pipeline.sh` for enrichment pipeline only.
- Lead generation default count is 5.
- Both scripts can be configured with environment variables.

### 3. LLM Provider Switching

- Pipeline is no longer hardcoded to OpenAI.
- Added configurable provider support.
- Currently supports OpenAI and Anthropic.
- Provider can be switched using `.env`.

### 4. Deeper Website Scraping

- Scraper now goes up to 3 levels deep.
- Checks homepage, source/contact pages, detail pages, and third-level pages.
- Better coverage for press, news, careers, leadership, contact, fraud, security, and risk pages.

### 5. Recipient Email Discovery

- Extracts emails from page text, `mailto:` links, and obfuscated formats.
- Filters out generic emails like `info@`, `support@`, and `contact@`.
- Allows useful role emails like `fraud@`, `compliance@`, `risk@`, and `security@`.
- Adds `recipient_email` and `recipient_email_source_url` to output.

### 6. Blocked Website Handling

- Detects blocked, captcha, and security-check pages.
- Skips LLM enrichment for blocked websites.
- Leaves fraud angle and email blank instead of inventing data.

### 7. Token Reduction

- Added local research fact extraction.
- Sends compact facts to the LLM instead of large raw website text.
- Reduces token usage and improves speed/cost.

### 8. Better Email Personalization

- Prompts now use services, customer clues, risk clues, evidence snippets, and public signals.
- Email output now uses a consistent subject and body format.

## Next Improvements

### 1. Add Caching

- Cache scraped pages and LLM responses.
- Avoid repeated scraping and repeated LLM cost.

### 2. Improve Email Discovery With Search API

- Use Google/Bing/SerpAPI for companies where website scraping does not expose emails.
- Search for leadership, contact, compliance, fraud, or risk emails.

### 3. Add Blocked Website Report

- Create a separate CSV for blocked websites.
- Use it for manual review or alternate enrichment.

### 4. Add Lead Quality Score

- Score each lead based on website availability, public signal, recipient email, and research confidence.

### 5. Add Source Evidence Columns

- Include source URLs for services, customer clues, and risk clues.
- Makes output easier to audit.

### 6. Improve Institution Type Normalization

- Add deterministic rules:
  - company contains `Credit Union` -> `Credit Union`
  - company contains `Bank` -> `Bank`
- Prevent incorrect classifications like a bank being classified as a credit union.

### 7. Add Tests

- Test lead filtering.
- Test blocked website detection.
- Test email extraction.
- Test subject/body formatting.
- Test LLM provider switching.

### 8. Add Retry, Backoff, And Rate Limits

- Improve scraping stability.
- Avoid hitting websites too aggressively.

### 9. Add Output Summary Dashboard

- Show total leads processed.
- Show emails found.
- Show blocked websites.
- Show signals found.
- Show LLM failures.
- Show average processing time.
