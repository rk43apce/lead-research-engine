from __future__ import annotations

import json

from services.models import ResearchContext


def research_prompt(context: ResearchContext) -> str:
    payload = {
        "company": context.lead.company,
        "website": context.lead.website,
        "homepage_url": context.homepage_url,
        "institution_hint": context.facts.institution_hint,
        "customer_clues": context.facts.customer_clues,
        "services_found": context.facts.services,
        "risk_clues": context.facts.risk_clues,
        "evidence_snippets": context.facts.evidence_snippets,
        "source_urls": context.facts.source_urls,
        "public_signal": {
            "summary": context.public_signal.summary,
            "source_url": context.public_signal.source_url,
            "signal_type": context.public_signal.signal_type.value,
        },
        "recipient_email": {
            "email": context.contact_email.email,
            "source_url": context.contact_email.source_url,
            "confidence": context.contact_email.confidence,
        },
        "scrape_status": context.scrape_status,
        "is_website_blocked": context.is_website_blocked,
    }
    return f"""
You are classifying a financial institution for a B2B lead research workflow.

Rules:
- Use only the supplied context.
- Do not invent services, facts, customer segments, or signals.
- If is_website_blocked is true, return "Website blocked for scraping" for institution_type and leave fraud_angle empty.
- If the context is insufficient, use "Unknown" or a cautious phrase.
- Prefer services_found, customer_clues, risk_clues, and evidence_snippets over generic assumptions.
- Unless is_website_blocked is true, fraud_angle must not be blank or "Unknown"; provide a conservative likely fraud/risk angle based on the supplied institution type, services, and public signal.
- Return valid JSON only.

JSON schema:
{{
  "institution_type": "string",
  "customer_segment": "string",
  "services": ["string"],
  "fraud_angle": "string"
}}

Context:
{json.dumps(payload, ensure_ascii=True)}
""".strip()


def email_prompt(context: ResearchContext, institution_type: str, fraud_angle: str) -> str:
    signal = context.public_signal
    has_recent_signal = bool(signal.source_url and signal.signal_type.value != "website_context")
    payload = {
        "company": context.lead.company,
        "institution_type": institution_type,
        "fraud_angle": fraud_angle,
        "customer_clues": context.facts.customer_clues,
        "services_found": context.facts.services[:5],
        "risk_clues": context.facts.risk_clues,
        "evidence_snippets": context.facts.evidence_snippets[:3],
        "signal_summary": signal.summary,
        "source_url": signal.source_url,
        "has_signal": has_recent_signal,
        "has_website_context_source": bool(signal.source_url and signal.signal_type.value == "website_context"),
        "signal_type": signal.signal_type.value,
        "recipient_email": context.contact_email.email,
    }
    return f"""
Write one conservative B2B cold email for The PreCogs, an AI fraud prevention company.

Hard rules:
- Return a personalized subject and a body.
- Subject must be specific to the company context, not generic.
- Subject must be under 9 words.
- Email body must be under 120 words, ideally 80-105 words.
- Email body must read like a real personalized email, not one dense paragraph.
- Format the body as 4 short paragraphs separated by blank lines:
  1. simple greeting plus the company-specific observation
  2. why that matters for fraud/risk
  3. how The PreCogs can help, including no-PII positioning
  4. low-friction CTA plus a short signoff
- Keep each paragraph to 1-2 short sentences.
- Mention the recent signal first only if has_signal is true.
- If has_website_context_source is true, you may use the source as general website context, but do not call it recent news or a recent signal.
- If has_signal is false, say you could not find a recent public signal and do not pretend otherwise.
- Do not cite facts beyond the supplied payload.
- Include one concrete customer, service, or evidence detail from the payload when available.
- Connect that detail to a likely fraud/risk challenge.
- Position The PreCogs as augmenting existing teams and workflows, not replacing people.
- Mention the no-PII angle naturally.
- End with a low-friction CTA.
- Do not include placeholders like [Your Name].
- Return valid JSON only: {{"subject": "string", "email": "string"}}

Payload:
{json.dumps(payload, ensure_ascii=True)}
""".strip()
