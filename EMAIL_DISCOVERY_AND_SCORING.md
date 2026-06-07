# Email Discovery and Scoring

This document explains how the pipeline tries to find a recipient email for each lead, how generic emails are filtered out, and how the best email is scored.

The implementation lives mainly in:

- `services/researcher.py`
- `services/scraper.py`
- `services/prompts.py`

## High-Level Flow

The UI and CLI do not manually enter recipient emails. The pipeline tries to discover them from the public company website.

For each lead:

1. Read the company website from `input/leads.csv`.
2. Scrape the landing page.
3. Extract emails from the page.
4. Find contact-related links from the same website.
5. Crawl likely contact pages up to three levels deep.
6. Extract emails from those pages.
7. Reject generic or unrelated emails.
8. Score remaining candidate emails.
9. Pick the highest-scoring email.
10. Write the result to the output CSV.

The final output fields are:

- `recipient_email`
- `recipient_email_source_url`

If no strong recipient email is found, those fields stay blank. The pipeline can still generate the lead research and cold email draft.

## Where Emails Come From

Email extraction happens in `services/scraper.py`.

The scraper looks for:

- `mailto:` links
- normal emails inside HTML, such as `name@bank.com`
- obfuscated emails in readable text, such as `name at bank dot com`

Emails are normalized to lowercase and deduplicated before being passed to the researcher.

## Contact Page Discovery

The researcher starts with the company landing page, then ranks links that look useful for finding a real recipient.

Preferred contact-page keywords include:

- `leadership`
- `executive`
- `management`
- `team`
- `board`
- `contact us`
- `contact`
- `about us`
- `about`
- `fraud`
- `security`
- `risk`
- `compliance`
- `privacy`
- `email`

Each keyword has a bonus score. Higher-value pages are crawled first.

Examples:

| Page keyword | Bonus |
| --- | ---: |
| `leadership` | 14 |
| `executive` | 13 |
| `management` | 13 |
| `team` | 10 |
| `contact us` | 9 |
| `contact` | 8 |
| `about us` | 7 |
| `fraud` | 6 |
| `security` | 6 |
| `risk` | 5 |
| `compliance` | 5 |
| `privacy` | 4 |
| `email` | 4 |

The crawler only follows pages from the same company website. This avoids pulling emails from vendors, social networks, or unrelated sites.

## Generic Email Filtering

Before scoring a candidate email, the pipeline checks whether it is usable.

The following types of emails are rejected:

1. Invalid email format.
2. Generic mailbox prefixes.
3. Emails from a different root domain than the company website.

Generic prefixes currently rejected:

```text
admin
alerts
banking
careers
contact
customerservice
help
hello
hr
info
jobs
marketing
media
noreply
no-reply
onlinebanking
privacy
service
support
webmaster
```

Examples rejected:

```text
info@examplebank.com
support@examplebank.com
contact@examplebank.com
privacy@examplebank.com
noreply@examplebank.com
```

## Domain Safety Filter

The pipeline also checks that the email belongs to the same root domain as the company website.

Example:

```text
Company website: https://examplebank.com
Accepted: jane.smith@examplebank.com
Rejected: jane.smith@gmail.com
Rejected: sales@vendor.com
```

This keeps the output focused on the actual lead and avoids third-party contact emails.

## Email Scoring

After filtering, each usable candidate starts with:

```text
base score = 10
```

Then additional points are added.

### Page Quality Bonus

If the email appears on a useful page, the page keyword bonus is added.

Example:

```text
Email found on /leadership page:
base score + leadership bonus
10 + 14 = 24
```

### Valuable Prefix Bonus

If the email prefix contains useful business, risk, or fraud-related words, the score increases.

Useful prefix keywords include:

```text
executive
leadership
management
fraud
risk
security
compliance
operations
treasury
commercial
business
lending
loan
mortgage
bsa
aml
```

Each matching keyword adds:

```text
+8
```

Examples:

```text
fraudteam@examplebank.com
risk.operations@examplebank.com
compliance@examplebank.com
```

### Role Email Bonus

Some role-based emails are considered useful enough to keep and boost.

Role prefixes include:

```text
bsa
aml
fraud
risk
security
compliance
operations
treasury
commercial
business
lending
loans
mortgage
mortgages
executive
management
```

If the email prefix exactly matches one of these, it gets:

```text
+10
```

Examples:

```text
fraud@examplebank.com
risk@examplebank.com
compliance@examplebank.com
```

### Person-Style Email Bonus

If the prefix contains a dot or hyphen, the pipeline treats it as more likely to be a person or specific mailbox.

Examples:

```text
jane.smith@examplebank.com
jane-smith@examplebank.com
```

Bonus:

```text
+4
```

## Confidence Calculation

The selected email is the highest-scoring candidate.

Confidence is calculated as:

```text
confidence = min(score / 30, 0.95)
```

Examples:

| Score | Confidence |
| ---: | ---: |
| 15 | 0.50 |
| 24 | 0.80 |
| 28 | 0.93 |
| 30+ | 0.95 |

The confidence is capped at `0.95` because website scraping cannot fully guarantee deliverability or ownership.

## Example Scoring

Candidate:

```text
jane.smith@examplebank.com
```

Found on:

```text
https://examplebank.com/leadership
```

Score:

```text
base score: +10
leadership page: +14
person-style email: +4
total: 28
confidence: 28 / 30 = 0.93
```

Rejected candidate:

```text
info@examplebank.com
```

Reason:

```text
The prefix `info` is generic.
```

## How Email Discovery Connects To Draft Generation

The discovered recipient email is included in the LLM prompt payload in `services/prompts.py`.

The email draft generation prompt receives:

- company name
- institution type
- fraud angle
- customer clues
- services found
- risk clues
- public signal
- recipient email

The recipient email is not used to send anything. It is only saved in the enriched output and later shown in the review UI.

## Current Limitations

The current system uses only public website scraping.

It does not use:

- Apollo
- Hunter
- Clearbit
- ZoomInfo
- LinkedIn
- email verification APIs
- MX or SMTP deliverability checks

Because of this, some leads may have no recipient email if the company only publishes generic addresses.

## Future Improvements

Possible next steps:

- Add optional Hunter/Apollo integration for verified contacts.
- Add job-title targeting such as fraud, risk, compliance, BSA, AML, or operations.
- Store multiple candidate emails with their scores instead of only the best one.
- Show email confidence in the Review Emails UI.
- Add deliverability verification before export.
- Add a manual override field in the review drawer.

